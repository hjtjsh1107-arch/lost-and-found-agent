import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import create_app
from backend.config import Settings
from backend.db import Draft, DraftPhoto, NoticePhoto, database
from backend.models import MockModels, ModelFailure, OllamaModels, TextRegion
from backend.privacy import detect, redact
from backend.schemas import Finding, OCRFinding, PrivacyReview, VisualReview


KEY = "synthetic-privacy-tests-only-local-token"
AUTH = {"Authorization": f"Bearer {KEY}"}
FIXTURE = Path(__file__).with_name("fixtures") / "privacy-card.png"


@pytest.mark.parametrize("text,kind", [
    ("성명: 가상인", "name"), ("010-0000-0000", "phone"), ("0000 0000 0000 0000", "card_number"),
    ("유효기간: 01/49", "card_expiry"), ("CVC: 000", "card_security"),
    ("000000-1000000", "resident_id"), ("가상로 123", "address"), ("fixture@example.invalid", "email"),
    ("M000A0000", "passport"), ("00-00-000000-00", "driver_license"), ("계좌: 000-000-000", "account"),
    ("00가0000", "license_plate"), ("IMEI: 000000000000000", "imei"), ("일련번호: DUMMY-DEVICE", "serial"),
])
def test_structured_detection(text, kind):
    assert kind in {found.kind for found in detect(text)}
    assert not detect(redact(text))
    assert redact(text) != text


def test_masked_display_and_public_text_differ():
    assert redact("가상인", ["가상인"], partial=True) == "가○○"
    assert redact("010-0000-0000", partial=True) == "010-****-****"
    assert redact("fixture@example.invalid", partial=True) == "***@example.invalid"
    assert redact("010-****-****") == "[마스킹]"
    assert redact("유효기간: 01/49", partial=True) == "유효기간: [마스킹]"


def test_contextual_model_receives_public_field_meanings(config, monkeypatch):
    models = OllamaModels(config)

    def chat(model, schema, instruction, data):
        assert data["public"] == {"object_label": "우산", "pickup_venue": "역 안내소"}
        return schema(restricted_fields=["object_label", "memo"], hold_photos=False)

    monkeypatch.setattr(models, "_chat", chat)
    review = models.privacy({"public": {"name": "우산", "location": "역 안내소"}, "input": {"memo": "가상인"}})
    assert review.restricted_fields == ["name", "memo"]


class PrivacyFixtureModels(MockModels):
    """합성 OCR의 경계와 모델 판단을 지정해 실제 마스킹 코드만 비교합니다."""

    item_name = "우산"
    risk = "low"
    bands = ()
    context_field = None
    fail_ocr = False
    skipped_scan = False

    def __init__(self):
        self.scan_count = 0

    def read_text(self, photos):
        self.scan_count += 1
        if self.fail_ocr:
            raise ModelFailure("가상 OCR 실패", "ocr_failed")
        if self.skipped_scan and self.scan_count == 1:
            return []
        found = []
        for index, photo in enumerate(photos, 1):
            with Image.open(BytesIO(photo)) as image:
                for text, box in self.bands:
                    x1, y1, x2, y2 = box
                    if image.getpixel((x1, y1)) == (21, 28, 41):
                        continue
                    found.append(TextRegion(index, text, [[x1,y1], [x2,y1], [x2,y2], [x1,y2]], .99))
        return found

    def extract(self, photos, regions, memo):
        result = super().extract(photos, regions, memo)
        result.item.name = result.item.subcategory = self.item_name
        result.photo_risk = self.risk
        return result

    def verify(self, photos, item, regions):
        return VisualReview(findings=[Finding(field=key, status="verified", reason="가상 근거")
                                      for key, value in item.items() if value])

    def privacy(self, candidates):
        findings = [OCRFinding(region=entry["region"], kind="name") for entry in candidates.get("ocr", [])
                    if "OCR DEMO PERSON" in entry["text"]]
        field = self.context_field
        mapping = {**candidates.get("input", {}), **candidates.get("public", {}), **candidates.get("attributes", {})}
        return PrivacyReview(restricted_fields=[field] if field and "CONTEXT ONLY PERSON" in str(mapping.get(field)) else [],
                             hold_photos=False, ocr_findings=findings)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.photo_privacy.face_regions", lambda _: [])
    return Settings(mode="real", staff_token=KEY, database_url=f"sqlite:///{(tmp_path / 'privacy.db').as_posix()}")


def submit(client, memo="", *, synthetic_card=False):
    metadata = {"found_at": "2025-01-01T13:00:00+09:00", "region": "서울", "location": "가상 안내 데스크",
                "finder": "가상 담당자", "institution": "가상 보관소", "memo": memo}
    plain = BytesIO()
    Image.new("RGB", (800, 500), "#16385c").save(plain, format="PNG")
    photo = FIXTURE.read_bytes() if synthetic_card else plain.getvalue()
    response = client.post("/staff/drafts", files={"photos": ("synthetic.png", photo, "image/png")},
                           data={"metadata": json.dumps(metadata)}, headers=AUTH)
    assert response.status_code == 201, response.text
    return response.json()


def change(client, draft, route, **values):
    return client.post(f"/staff/drafts/{draft['id']}/{route}", headers=AUTH, json={"revision": draft["revision"], **values})


def image(client, draft, kind="masked", authenticated=True):
    return client.get(f"/staff/drafts/{draft['id']}/photos/1", params={"kind":kind}, headers=AUTH if authenticated else {})


@pytest.mark.parametrize("name,bands,kinds", [
    ("신용카드", (("0000 0000", (110,200,270,245)), ("0000 0000", (275,200,480,245)),
                 ("OCR DEMO PERSON", (110,258,500,300)), ("EXP: 01/49", (110,318,290,365)),
                 ("CVC:", (300,318,390,365)), ("000", (392,318,570,365))),
     {"card_number", "name", "card_expiry", "card_security"}),
    ("신분증", (("성명: 가상인", (110,258,500,300)), ("000000-1000000", (110,205,500,245))), {"name", "resident_id"}),
])
def test_sensitive_documents_are_masked_in_staff_preview_and_never_public(config, name, bands, kinds):
    models = PrivacyFixtureModels()
    models.item_name, models.bands = name, bands
    with TestClient(create_app(config, models)) as client:
        draft = submit(client, synthetic_card=True)
        privacy = draft["analysis"]["privacy"]
        check = privacy["photo_checks"][0]
        assert kinds <= {kind for region in check["regions"] for kind in region["types"]}
        assert not privacy["photo_available"]
        assert image(client, draft, "original").status_code == 404
        masked = Image.open(BytesIO(image(client, draft).content))
        assert masked.getpixel((120, 220)) == (21, 28, 41)
        assert models.scan_count >= 2
        assert change(client, draft, "approve", confirmed=True, publish_photos=True).status_code == 409
        assert "OCR DEMO PERSON" not in json.dumps(draft, ensure_ascii=False)


@pytest.mark.parametrize("scenario", ["background", "ocr_failure", "residual_ocr", "detector_failure"])
def test_uncertainty_blocks_photo_publish(config, monkeypatch, scenario):
    model = PrivacyFixtureModels()
    if scenario == "background":
        model.item_name, model.risk = "휴대폰", "high"
    elif scenario == "ocr_failure":
        model.fail_ocr = True
    elif scenario == "detector_failure":
        monkeypatch.setattr("backend.photo_privacy.face_regions", lambda _: None)
    else:
        model.bands = (("010-0000-0000", (110, 205, 480, 245)),)
        model.skipped_scan = True
    with TestClient(create_app(config, model)) as client:
        draft = submit(client)
        privacy = draft["analysis"]["privacy"]
        assert privacy["photo_checks"][0]["masking_status"] == "covered"
        assert not privacy["photo_available"]
        if scenario == "background":
            assert "personal_image" in {hit["type"] for hit in privacy["detections"]}
        assert image(client, draft, "original").status_code == 404
        assert change(client, draft, "approve", confirmed=True, publish_photos=True).status_code == 409


def test_contextual_and_structured_text_never_persist(config):
    model = PrivacyFixtureModels()
    model.context_field = "memo"
    with TestClient(create_app(config, model)) as client:
        draft = submit(client, "CONTEXT ONLY PERSON / 성명: 가상인, 전화 010-0000-0000")
        assert draft["analysis"]["privacy"]["restricted"]["memo"] == "[마스킹]"
        assert "CONTEXT ONLY PERSON" not in json.dumps(draft)
        assert draft["intake"]["memo"] == "[마스킹]"
        updated = change(client, draft, "validate", item={"name": "우산", "features": "IMEI: 000000000000000"},
                         title="우산 00가0000", description="연락 fixture@example.invalid")
        assert updated.status_code == 200, updated.text
        draft = updated.json()
        assert "features" in draft["analysis"]["privacy"]["discard"]
        assert draft["analysis"]["item"]["features"] is None
        approval = change(client, draft, "approve", confirmed=True).json()
        publication = change(client, approval, "publish")
        assert publication.status_code == 200, publication.text
        for text in ("000000000000000", "00가0000", "fixture@example.invalid"):
            assert text not in publication.text
        engine, sessions = database(config)
        with sessions() as session:
            stored = session.get(Draft, draft["id"])
            assert "000000000000000" not in json.dumps(stored.analysis)
            assert stored.intake["finder"] == "[마스킹]"
        engine.dispose()


def test_manual_addition_invalidates_approval_and_public_only_serves_certified_bytes(config):
    with TestClient(create_app(config, PrivacyFixtureModels())) as client:
        draft = submit(client)
        assert draft["analysis"]["privacy"]["photo_checks"][0]["masking_status"] == "clean"
        assert image(client, draft, "original", authenticated=False).status_code == 401
        assert image(client, draft, "original").status_code == 200
        assert Image.open(BytesIO(image(client, draft).content)).getpixel((120, 220)) == (22, 56, 92)
        approved = change(client, draft, "approve", confirmed=True, publish_photos=True).json()
        changed = change(client, approved, "masking", position=1, boxes=[[110, 200, 500, 245]]).json()
        assert changed["status"] == "review" and changed["approval"] is None
        assert change(client, approved, "publish").status_code == 409
        assert change(client, changed, "publish").status_code == 409
        masked = image(client, changed).content
        assert not Image.open(BytesIO(masked)).getexif()
        assert Image.open(BytesIO(masked)).getpixel((120, 220)) == (21, 28, 41)
        approved = change(client, changed, "approve", confirmed=True, publish_photos=True).json()
        engine, sessions = database(config)
        with sessions.begin() as session:
            row = session.get(DraftPhoto, (draft["id"], 1))
            row.masked = row.original
        assert change(client, approved, "publish").status_code == 409
        with sessions.begin() as session:
            row = session.get(DraftPhoto, (draft["id"], 1))
            row.masked = masked
        assert change(client, approved, "publish").status_code == 200
        path = f"/notices/{draft['id']}/photos/1"
        assert client.get(path).content == masked
        assert image(client, draft, "original").status_code == 404
        with sessions.begin() as session:
            row = session.get(NoticePhoto, (draft["id"], 1))
            row.content = FIXTURE.read_bytes()
        assert client.get(path).status_code == 404
        engine.dispose()


def test_publish_harness_rejects_raw_text_and_fingerprint_mismatch(config):
    with TestClient(create_app(config, PrivacyFixtureModels())) as client:
        draft = submit(client)
        approved = change(client, draft, "approve", confirmed=True).json()
        engine, sessions = database(config)
        for value in ("공개 000000-1000000", "승인 후 바뀐 문장"):
            with sessions.begin() as session:
                row = session.get(Draft, draft["id"])
                privacy = row.analysis["privacy"]
                row.analysis = {**row.analysis, "privacy": {**privacy, "public": {**privacy["public"], "description": value}}}
            assert change(client, approved, "publish").status_code == 409
        engine.dispose()


def test_originals_can_be_discarded_without_losing_text_draft(config):
    with TestClient(create_app(config, PrivacyFixtureModels())) as client:
        draft = submit(client)
        revised = change(client, draft, "discard-originals").json()
        assert revised["status"] == "review"
        assert not revised["analysis"]["privacy"]["photo_available"]
        assert image(client, revised, "original").status_code == 404
        assert change(client, revised, "reextract").status_code == 409
        approved = change(client, revised, "approve", confirmed=True).json()
        assert change(client, approved, "publish").status_code == 200


def test_detected_face_area_is_opaque_and_checked_again(config, monkeypatch):
    def synthetic_face(photo):
        """가상 얼굴 영역은 해당 픽셀이 가려지면 재검사에서 검출되지 않습니다."""
        with Image.open(BytesIO(photo)) as graphic:
            return [] if graphic.getpixel((120, 220)) == (21, 28, 41) else [[110, 200, 500, 245]]

    monkeypatch.setattr("backend.photo_privacy.face_regions", synthetic_face)
    with TestClient(create_app(config, PrivacyFixtureModels())) as client:
        draft = submit(client)
        check = draft["analysis"]["privacy"]["photo_checks"][0]
        assert check["regions"][0]["types"] == ["face"]
        assert check["privacy_check_status"] == "passed"
        assert image(client, draft, "original").status_code == 404
        assert Image.open(BytesIO(image(client, draft).content)).getpixel((120, 220)) == (21, 28, 41)
