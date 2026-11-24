import json
import os
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import update

from backend.api import create_app
from backend.config import Settings
from backend.db import Draft, database
from backend.models import MockModels, ModelFailure, TextRegion
from backend.schemas import Finding, PrivacyReview, VisualReview
from scripts.make_sample_photo import umbrella_photo

AUTH = {"Authorization": "Bearer local-mock-only"}
INTAKE = {
    "found_at": "2025-02-01T12:00:00+09:00", "region": "서울시 중구", "location": "시청역",
    "finder": "테스트이름", "institution": "고객안내센터", "memo": "연락 010-0000-0000", "quantity": 2,
}


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as browser:
        yield browser


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "umbrella.png"
    umbrella_photo(path)
    return path.read_bytes()


def submit(client, photo, fields=None, **kwargs):
    return client.post("/staff/drafts", headers=kwargs.pop("headers", AUTH), data={
        "metadata": json.dumps({**INTAKE, **(fields or {})}, ensure_ascii=False),
    }, files={"photos": ("umbrella.png", photo, "image/png")}, **kwargs)


def transition(client, draft, action, **fields):
    return client.post(f"/staff/drafts/{draft['id']}/{action}", headers=AUTH,
                       json={"revision": draft["revision"], **fields})


def assert_workflow(client, photo):
    response = submit(client, photo)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "review"
    assert draft["analysis"]["item"]["category"] == "우산"
    assert draft["analysis"]["item"]["brand"] is None
    assert draft["analysis"]["privacy"]["restricted"]["memo"] == "연락 010-****-****"
    assert transition(client, draft, "publish").status_code == 409
    assert client.get(f"/notices/{draft['id']}").status_code == 404
    path = f"/staff/drafts/{draft['id']}"
    assert client.get(path).status_code == 401
    assert client.get(path + "/logs").status_code == 401
    preview = client.get(path + "/photos/1", headers=AUTH)
    assert preview.content != photo
    assert len(Image.open(BytesIO(preview.content)).getcolors()) == 1
    revised = transition(client, draft, "validate", item={**draft["analysis"]["item"], "brand": "확인 브랜드"},
                         title="시청역 검정 우산", description="연락 010-0000-0000").json()
    assert revised["analysis"]["evidence"]["brand"]["source"] == "담당자 수정"
    assert revised["analysis"]["privacy"]["verification_only"]["brand"] == "확인 브랜드"
    assert revised["analysis"]["privacy"]["public"]["description"] == "연락 [마스킹]"
    assert transition(client, draft, "approve", confirmed=True).status_code == 409
    assert transition(client, revised, "approve", confirmed=True, publish_photos=True).status_code == 409
    approval = transition(client, revised, "approve", confirmed=True)
    assert approval.status_code == 200, approval.text
    approved = approval.json()
    published = transition(client, approved, "publish")
    assert published.status_code == 200, published.text
    assert published.json()["quantity"] == 2
    assert transition(client, approved, "publish").json() == published.json()
    assert client.get(path + "/photos/1?kind=original", headers=AUTH).status_code == 404
    assert client.get(f"/notices/{draft['id']}/photos/1").status_code == 404
    result = client.get("/notices", params={"q": "시청역 검정 우산", "category": "우산"}).json()
    assert any(row["id"] == draft["id"] for row in result["items"])
    assert client.get("/notices", params={"q": "확인 브랜드"}).json()["total"] == 0
    for secret in ("테스트이름", "010-0000-0000", "확인 브랜드", "verification_only"):
        assert secret not in json.dumps(result, ensure_ascii=False)
    logs = client.get(path + "/logs", headers=AUTH).json()
    assert "테스트이름" not in json.dumps(logs, ensure_ascii=False)
    assert [row["agent"] for row in logs][:2] == ["Registration Agent", "Evidence Tracker"]
    assert logs[-1]["decision"] == "published"
    return draft["id"]


def test_workflow(client, photo):
    assert_workflow(client, photo)


def test_postgresql_workflow(photo):
    url = os.getenv("LAF_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set LAF_TEST_POSTGRES_URL to a disposable test database")
    with TestClient(create_app(Settings(database_url=url))) as client:
        notice_id = assert_workflow(client, photo)
    with TestClient(create_app(Settings(database_url=url))) as restarted:
        assert restarted.get(f"/notices/{notice_id}").status_code == 200
        official = restarted.get("/notices", params={"category": "PRZ000", "q": notice_id})
        assert official.status_code == 200 and official.json()["total"] == 1


def test_conflict_and_reextraction_limit(client, photo):
    draft = submit(client, photo, {"memo": "색상: 파랑"}).json()
    assert draft["status"] == "needs_review"
    assert draft["analysis"]["questions"]
    assert transition(client, draft, "approve", confirmed=True).status_code == 409
    for attempt in (1, 2):
        draft = transition(client, draft, "reextract").json()
        assert draft["reextractions"] == attempt
    assert transition(client, draft, "reextract").status_code == 409
    revised = transition(client, draft, "validate", item=draft["analysis"]["item"], memo="검정색 확인").json()
    assert revised["status"] == "review"


def test_edits_invalidate_approval(client, photo):
    draft = submit(client, photo).json()
    approved = transition(client, draft, "approve", confirmed=True).json()
    modified = transition(client, approved, "validate", item=approved["analysis"]["item"], title="새 제목").json()
    assert modified["status"] == "review"
    assert transition(client, modified, "publish").status_code == 409


def test_input_validation(client, photo):
    invalid = submit(client, b"fake")
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "image_input_failed"
    assert submit(client, photo, {"finder": " "}).status_code == 422
    assert submit(client, photo, {"found_at": "2025-02-01T12:00:00"}).status_code == 422
    assert submit(client, photo, headers={}).status_code == 401


def test_staff_session_and_background_analysis(client, photo):
    login = client.post("/staff/session", json={"key": "local-mock-only"})
    assert login.status_code == 200
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "local-mock-only" not in cookie
    assert client.get("/staff/session").json() == {"authenticated": True}
    assert client.post("/staff/session", json={"key": "local-mock-only"},
                       headers={"Origin": "https://other.example"}).status_code == 403
    queued = submit(client, photo, headers={"Origin": "http://testserver"}, params={"background": True})
    assert queued.status_code == 202
    path = f"/staff/drafts/{queued.json()['id']}"
    progress = client.get(path + "/progress")
    assert progress.json() == {"status": "review", "running": False, "stages": []}
    assert progress.headers["cache-control"] == "no-store"
    revision = client.get(path).json()["revision"]
    assert client.post(path + "/approve", headers={"Origin": "https://other.example"},
                       json={"revision": revision, "confirmed": True}).status_code == 403
    assert client.delete("/staff/session").status_code == 204
    assert client.get(path).status_code == 401


def test_policy_rechecked_at_publication(client, photo, settings):
    draft = submit(client, photo).json()
    approved = transition(client, draft, "approve", confirmed=True).json()
    snapshot = approved["analysis"]
    snapshot["privacy"]["public"]["title"] = "이름: 테스트이름"
    engine, sessions = database(settings)
    with sessions.begin() as session:
        session.execute(update(Draft).where(Draft.id == draft["id"]).values(analysis=snapshot))
    engine.dispose()
    assert transition(client, approved, "publish").status_code == 409
    assert client.get(f"/notices/{draft['id']}").status_code == 404


def test_model_failure_is_visible(settings, photo):
    class MissingModel(MockModels):
        def extract(self, photos, regions, memo):
            raise ModelFailure("테스트 모델 응답 오류")

    with TestClient(create_app(settings, MissingModel())) as client:
        response = submit(client, photo)
        assert response.status_code == 201
        draft = response.json()
        assert draft["status"] == "error"
        assert draft["analysis"]["error"] == "테스트 모델 응답 오류"
        assert transition(client, draft, "approve", confirmed=True).status_code == 409


@pytest.mark.parametrize("category,held", [("우산", False), ("카드", True), ("휴대전화", True)])
def test_image_policy(settings, photo, category, held):
    class OCRSample(MockModels):
        def read_text(self, photos):
            if Image.open(BytesIO(photos[0])).getpixel((25, 25)) == (21, 28, 41):
                return []
            return [TextRegion(1, "테스트이름 010-0000-0000", [[20, 20], [140, 20], [140, 60], [20, 60]], 0.94)]

        def extract(self, photos, regions, memo):
            result = super().extract(photos, regions, memo)
            result.item.category = category
            result.item.name = category
            result.photo_risk = "low"
            return result

        def verify(self, photos, item, regions):
            return VisualReview(findings=[Finding(field=field, status="verified", reason="테스트 관찰 근거")
                                          for field, value in item.items() if value])

        def privacy(self, candidates):
            return PrivacyReview(restricted_fields=[], hold_photos=False)

    real = settings.model_copy(update={"mode": "real", "staff_token": "test-key-" + "x" * 32})
    headers = {"Authorization": f"Bearer {real.staff_token}"}
    with TestClient(create_app(real, OCRSample())) as client:
        draft = submit(client, photo, headers=headers).json()
        path = "/staff/drafts/" + draft["id"]
        assert draft["analysis"]["privacy"]["ocr"][0]["text"] == "[마스킹]"
        assert draft["analysis"]["privacy"]["photo_available"] is not held
        masked = client.get(path + "/photos/1", headers=headers).content
        assert Image.open(BytesIO(masked)).getpixel((25, 25)) == (21, 28, 41)
        assert client.get(path + "/photos/1?kind=original", headers=headers).status_code == 404
        approval = client.post(path + "/approve", json={"revision": draft["revision"], "confirmed": True,
                                                       "publish_photos": True}, headers=headers)
        assert approval.status_code == (409 if held else 200)
        if not held:
            revision = approval.json()["revision"]
            assert client.post(path + "/publish", json={"revision": revision}, headers=headers).status_code == 200
            assert client.get(f"/notices/{draft['id']}/photos/1").content == masked
