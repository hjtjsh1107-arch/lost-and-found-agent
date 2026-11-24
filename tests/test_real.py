import json
import os
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from backend.api import create_app
from backend.config import Settings
from backend.models import OllamaModels
from backend.photo_privacy import create_photo_checks, text_regions
from backend.privacy import detect
from scripts.make_sample_photo import umbrella_photo


def test_real_workflow(tmp_path):
    if os.getenv("LAF_TEST_REAL") != "1":
        pytest.skip("Set LAF_TEST_REAL=1 with local OCR and Ollama configured")
    settings = Settings(
        mode="real",
        database_url=os.getenv("LAF_REAL_TEST_POSTGRES_URL", f"sqlite:///{(tmp_path / 'real.db').as_posix()}"),
        staff_token="temporary-real-smoke-test-key-only",
        ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
        vlm_model=os.getenv("VLM_MODEL", "qwen3-vl:8b"),
        verification_vlm_model=os.getenv("VERIFICATION_VLM_MODEL") or None,
        llm_model=os.getenv("LLM_MODEL", "qwen3-vl:8b"),
        model_timeout=int(os.getenv("MODEL_TIMEOUT", "180")),
    )
    supplied = os.getenv("LAF_REAL_TEST_PHOTO")
    image = Path(supplied) if supplied else tmp_path / "real-umbrella.png"
    if not supplied:
        umbrella_photo(image)
    metadata = {
        "found_at": "2025-02-01T12:00:00+09:00", "region": "서울시 중구", "location": "실험용 안내소",
        "finder": "가상 습득자", "institution": "예제 보관기관", "memo": "성명: 가상인, 연락 010-0000-0000",
    }
    models = OllamaModels(settings)
    ocr_sample = Image.new("RGB", (640, 160), "white")
    ImageDraw.Draw(ocr_sample).text(
        (25, 45), "0000 1111 2222 3333", font=ImageFont.load_default(size=42), fill="black",
    )
    buffer = BytesIO()
    ocr_sample.save(buffer, format="PNG")
    regions = models.read_text([buffer.getvalue()])
    assert any("2222" in region.text and region.polygon for region in regions)
    assert any(hit.kind == "card_number" for region in regions for hit in detect(region.text))
    with TestClient(create_app(settings, models)) as client:
        client.headers.update({"Authorization": f"Bearer {settings.staff_token}"})
        response = client.post(
            "/staff/drafts?background=true", data={"metadata": json.dumps(metadata, ensure_ascii=False)},
            files={"photos": (image.name, image.read_bytes(), "application/octet-stream")},
        )
        assert response.status_code == 202, response.text
        path = f"/staff/drafts/{response.json()['id']}"
        draft = client.get(path).json()
        assert draft["status"] != "error", draft["analysis"]
        logs = client.get(path + "/logs").json()
        assert {row["agent"] for row in logs if row["status"] == "ok"} >= {
            "Registration Agent", "Verification Agent", "Privacy Agent",
        }
        assert draft["mode"] == "real"
        assert draft["analysis"]["privacy"]["privacy_check_status"] == "passed"
        assert {"name", "phone"} <= {hit["type"] for hit in draft["analysis"]["privacy"]["detections"]}
        assert "010-0000-0000" not in json.dumps(draft, ensure_ascii=False)
        if draft["status"] == "needs_review":
            correction = {"name": os.getenv("LAF_REAL_TEST_ITEM_NAME", "우산")}
            result = client.post(path + "/validate", json={
                "revision": draft["revision"], "item": correction,
                "region": "서울특별시 중구", "location": "시청역 안내 데스크", "institution": "시청역 유실물센터",
            })
            assert result.status_code == 200, result.text
            draft = result.json()
        assert draft["status"] == "review", draft["analysis"]
        approved = client.post(path + "/approve", json={"revision": draft["revision"], "confirmed": True})
        assert approved.status_code == 200, approved.text
        result = client.post(path + "/publish", json={"revision": approved.json()["revision"]})
        assert result.status_code == 200, result.text
        assert any(item["id"] == draft["id"] for item in client.get("/notices", params={"q": draft["id"]}).json()["items"])


def test_real_ocr_masks_and_rescans_synthetic_card():
    if os.getenv("LAF_TEST_REAL") != "1":
        pytest.skip("Set LAF_TEST_REAL=1 with local OCR configured")
    models = OllamaModels(Settings())
    original = (Path(__file__).with_name("fixtures") / "privacy-card.png").read_bytes()
    regions = models.read_text([original])
    kinds = {kind for region in text_regions(regions, []).values() for kind in region}
    assert {"card_number", "name"} <= kinds
    candidates, checks = create_photo_checks(
        [original], regions, [], (), {}, models.read_text, retained=(), blocked=True,
        uncertain=False, mock=False, check_context=lambda _: False,
    )
    assert checks[0]["privacy_check_status"] == "blocked"
    assert checks[0]["masking_status"] == "masked"
    assert {"card_number", "name"} <= {kind for region in checks[0]["regions"] for kind in region["types"]}
    assert candidates[0] != original
    assert not text_regions(models.read_text(candidates), [])
    assert not Image.open(BytesIO(candidates[0])).getexif()
