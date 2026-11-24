import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.categories import observation
from backend.config import Settings
from backend.model_diagnostics import collect_calls
from backend.models import MockModels, ModelFailure, OllamaModels
from backend.schemas import Extraction, Finding, Item, PrivacyReview, VisualReview
from scripts.make_sample_photo import umbrella_photo

TOKEN = "vlm-pipeline-test-key-not-an-app-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "test-image.png"
    umbrella_photo(path)
    return path.read_bytes()


@pytest.fixture
def settings(tmp_path):
    return Settings(mode="real", staff_token=TOKEN, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


class ObservedUmbrella(MockModels):
    def extract(self, photos, regions, memo):
        assert photos
        assert regions == []
        item = Item(category="우산", name="우산", subcategory="접이식 우산", color="밝은 회색", shape="접힌 상태",
                    handle="곡선형 손잡이", part_colors="흰색 손잡이", handle_color="흰색", accessories="베이지색 부속품")
        return Extraction(item=item, photo_evidence={key: [1] for key, value in item if value},
                          uncertain_fields=["accessories"], photo_risk="low")

    def verify(self, photos, item, regions):
        return VisualReview(findings=[
            Finding(field=key, status="rejected" if key == "handle" else "verified", reason="시험 검증 결과")
            for key, value in item.items() if value
        ])

    def privacy(self, candidates):
        return PrivacyReview(restricted_fields=[], hold_photos=False)


def submit(client, photo):
    result = client.post("/staff/drafts", headers=AUTH, files={"photos": ("item.png", photo, "image/png")},
                         data={"metadata": json.dumps({
                             "found_at": "2025-02-01T12:00:00+09:00", "region": "서울시",
                             "location": "시험 안내소", "institution": "시험 보관기관", "finder": "가상접수자",
                         })})
    assert result.status_code == 201
    return result.json()


def test_verified_appearance_populates_notice_without_official_mapping(settings, photo):
    with TestClient(create_app(settings, ObservedUmbrella())) as client:
        draft = submit(client, photo)
        analysis = draft["analysis"]
        assert draft["status"] == "review"
        assert analysis["analysis_status"] == "partial"
        assert analysis["observation"]["category_status"] == "unmapped"
        assert analysis["observation"]["observed_item_type"] == "접이식 우산"
        assert analysis["evidence"]["handle"]["status"] == "rejected"
        assert analysis["evidence"]["accessories"]["status"] == "uncertain"
        preview = analysis["privacy"]["public"]
        assert preview["title"] == "밝은 회색 접이식 우산 습득"
        assert "접힌 상태" in preview["description"] and "손잡이 색상은 흰색" in preview["description"]
        assert "곡선형 손잡이" not in str(preview) and "베이지색 부속품" not in str(preview)
        assert analysis["privacy"]["ocr"] == []
        logs = client.get(f"/staff/drafts/{draft['id']}/logs", headers=AUTH).text
        assert "곡선형 손잡이" not in logs and "가상접수자" not in logs


def test_privacy_can_withhold_even_verified_features(settings, photo):
    class HoldColor(ObservedUmbrella):
        def privacy(self, candidates):
            assert "handle_color" in candidates["public"]
            return PrivacyReview(restricted_fields=["handle_color"], hold_photos=True)

    with TestClient(create_app(settings, HoldColor())) as client:
        result = submit(client, photo)["analysis"]["privacy"]
        assert "손잡이 색상은 흰색" not in str(result["public"])
        assert result["restricted"]["handle_color"] == "[마스킹]"
        assert not result["photo_available"]


def test_official_mode_blocks_an_unmapped_item(settings, photo):
    strict = settings.model_copy(update={"require_official_category": True})
    with TestClient(create_app(strict, ObservedUmbrella())) as client:
        draft = submit(client, photo)
        assert draft["status"] == "needs_review"
        assert "접이식 우산" in draft["analysis"]["privacy"]["public"]["title"]
        response = client.post(f"/staff/drafts/{draft['id']}/approve", headers=AUTH,
                               json={"revision": draft["revision"], "confirmed": True})
        assert response.status_code == 409


def test_partial_official_catalog_preserves_observations():
    item = {"category": "가방", "name": "천 가방", "subcategory": "기타가방", "color": "파랑"}
    exact = observation(item)
    assert [exact["category_status"], exact["major_category_code"], exact["minor_category_code"]] == ["mapped", "PRA000", "PRA300"]
    incomplete = observation({**item, "subcategory": "천가방"})
    assert incomplete["category_status"] == "unmapped"
    assert incomplete["minor_category_code"] is None
    assert incomplete["observed_item_name"] == "천 가방"
    assert incomplete["attributes"] == {"color": "파랑"}


def test_model_dict_and_list_observations_validate():
    values = Extraction.model_validate({
        "item": {"category": "우산", "name": "우산", "color": "연회색",
                 "part_colors": {"handle": "흰색", "fabric": "연회색"}, "accessories": ["끈"]},
        "photo_evidence": [1],
    })
    assert values.item.part_colors == "손잡이 흰색, 천 연회색"
    assert values.item.handle_color == "흰색"
    assert values.item.accessories == "끈"
    assert values.photo_evidence == {}
    alternate = Extraction.model_validate({
        "item": {"category": "우산", "name": "우산", "color": "베이지",
                 "part_colors": ["손잡이 흰색", "천 베이지"], "handle_color": "흰색", "handle": "링 모양"},
        "photo_evidence": [1],
    })
    assert alternate.item.part_colors == "손잡이 흰색, 천 베이지"
    assert alternate.item.handle == "링 모양"


def test_compact_verification_prefers_conflicts_and_ignores_unknown_keys():
    output = VisualReview.model_validate({
        "verified": ["name", "color", "untrusted-key"],
        "rejected": ["color"],
        "reasons": {"color": "물품 본체가 아니라 배경 색입니다."},
    })
    assert [(finding.field, finding.status) for finding in output.findings] == [("name", "verified"), ("color", "rejected")]
    assert output.findings[1].reason == "물품 본체가 아니라 배경 색입니다."


def test_ocr_error_retains_vlm_results_and_holds_photos(settings, photo):
    class OCRFailure(ObservedUmbrella):
        def read_text(self, photos):
            raise ModelFailure("OCR failed", "ocr_failed")

    with TestClient(create_app(settings, OCRFailure())) as client:
        analysis = submit(client, photo)["analysis"]
        assert analysis["warnings"]
        assert analysis["item"]["name"] == "우산"
        assert analysis["privacy"]["photo_available"] is False


@pytest.mark.parametrize(("result", "code"), [
    ("offline", "model_connection_failed"),
    (404, "model_not_found"),
    (400, "image_input_failed"),
    (200, "parsing_failed"),
])
def test_ollama_failure_codes_and_image_payload(settings, photo, monkeypatch, result, code):
    client = httpx.Client

    def handle(request):
        payload = json.loads(request.content)
        assert request.url.path == "/api/chat"
        assert base64.b64decode(payload["messages"][-1]["images"][0]) == photo
        if result == "offline":
            raise httpx.ConnectError("fixture")
        return httpx.Response(result, json={"message": {"content": "[bad content]"}})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    with collect_calls() as entries, pytest.raises(ModelFailure) as failure:
        OllamaModels(settings).extract([photo], [], "")
    assert failure.value.code == code
    assert entries[0]["error_code"] == code
    assert entries[0]["called"]
    assert entries[0]["image_input_ok"] is (result == 200)
