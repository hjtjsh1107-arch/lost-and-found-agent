import csv
import json

import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.categories import taxonomy
from backend.config import ROOT, Settings
from backend.item_schemas import classify, definitions, select_schema
from backend.models import MockModels, OllamaModels
from backend.schemas import AttributeExtraction, Finding, PrivacyReview, Recognition, VisualReview
from backend.service import registration_view
from scripts.make_sample_photo import umbrella_photo

KEY = "schema-api-testing-key-0123456789"
AUTH = {"Authorization": f"Bearer {KEY}"}


class FixtureModels(MockModels):
    def __init__(self, name, item_type, attributes, major=None, minor=None):
        self.result = Recognition(name=name, observed_item_type=item_type, major_category_code=major,
                                  minor_category_code=minor, photo_risk="low")
        self.attributes = attributes
        self.selected = None

    def recognize(self, photos, memo):
        assert len(photos) == 1
        return self.result

    def extract_attributes(self, photos, item, schema, regions, memo):
        self.selected = schema["schema_id"]
        return AttributeExtraction(attributes=self.attributes)

    def verify(self, photos, item, regions):
        return VisualReview(findings=[
            Finding(field=key, status="verified", reason="합성 테스트 근거") for key, value in item.items() if value is not None
        ])

    def privacy(self, candidates):
        return PrivacyReview(restricted_fields=[], hold_photos=False)


@pytest.fixture
def settings(tmp_path):
    return Settings(mode="real", staff_token=KEY, database_url=f"sqlite:///{(tmp_path / 'dynamic.db').as_posix()}")


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "sample.png"
    umbrella_photo(path)
    return path.read_bytes()


def submit(client, photo):
    created = client.post("/staff/drafts", headers=AUTH, files={"photos": ("sample.png", photo, "image/png")},
                          data={"metadata": json.dumps({
                              "found_at": "2025-02-01T12:00:00+09:00", "region": "서울", "location": "시험 장소",
                              "institution": "시험 안내소", "finder": "가상 담당자",
                          })})
    assert created.status_code == 201, created.text
    return created.json()


def transition(client, draft, operation, **body):
    return client.post(f"/staff/drafts/{draft['id']}/{operation}", headers=AUTH,
                       json={"revision": draft["revision"], **body})


def test_official_snapshot_matches_csv():
    catalog = taxonomy()
    assert (catalog["major_count"], catalog["minor_count"]) == (21, 85)
    assert {group["name"] for group in catalog["majors"]} == definitions()["major"].keys()
    assert catalog["web_comparison"] == "not_verified"
    with (ROOT / "data" / "categories.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows == [
        {"major_code": group["code"], "major_name": group["name"], "minor_code": minor["code"], "minor_name": minor["name"]}
        for group in catalog["majors"] for minor in group["minors"]
    ]


@pytest.mark.parametrize("name,item_type,schema_id,major,required_field", [
    ("코트", "트렌치 코트", "trench-coat", "의류", "size"),
    ("목도리", "목도리", "scarf-gloves", "의류", "material"),
    ("우산", "접이식 우산", "umbrella", "기타물품", "handle_color"),
    ("휴대폰", "스마트폰", "phone", "휴대폰", "screen"),
    ("자동차 스마트키", "스마트키", "car-key", "자동차", "button_count"),
    ("신용카드", "신용카드", "credit-card", "카드", "card_number"),
    ("현금", "지폐", "cash", "현금", "denominations"),
    ("가방", "백팩", "bag", "가방", "strap"),
    ("전구", "일반 전구", "general", None, "features"),
])
def test_type_profiles(settings, photo, name, item_type, schema_id, major, required_field):
    models = FixtureModels(name, item_type, {"color": "회색", "handle_color": "흰색", "features": "확인된 외관"})
    with TestClient(create_app(settings, models)) as client:
        analysis = submit(client, photo)["analysis"]
    assert models.selected == schema_id == analysis["item_schema"]["schema_id"]
    assert analysis["observation"]["major_category_name"] == major
    assert analysis["observation"]["observed_item_type"] == item_type
    fields = {field["field_key"]: field for field in analysis["fields"]}
    assert required_field in fields
    assert fields["name"]["value"] == name
    if schema_id != "umbrella":
        assert "handle_color" not in fields and "handle" not in analysis["evidence"]
    if schema_id == "trench-coat":
        assert fields["brand"]["value"] is None and fields["brand"]["label"] == "브랜드"


def test_codes_and_schema_change(settings, photo):
    models = FixtureModels("코트", "트렌치코트", {"color": "회색", "size": "M"}, "PRK000", "PRK200")
    with TestClient(create_app(settings, models)) as client:
        draft = submit(client, photo)
        assert draft["analysis"]["observation"]["minor_category_code"] is None
        assert "기타의류" in {row["minor_name"] for row in draft["analysis"]["observation"]["category_candidates"]}
        preview = client.post(f"/staff/drafts/{draft['id']}/schema", headers=AUTH,
                              json={"name": "스마트키", "subcategory": "스마트키"})
        assert preview.json()["classification"]["minor_category_code"] == "PRF100"
        response = transition(client, draft, "validate", item={"name": "스마트키", "subcategory": "스마트키"},
                              major_category_code="PRK000", minor_category_code="PRF100")
        assert response.status_code == 422
        response = transition(client, draft, "validate", item={"name": "스마트키", "subcategory": "스마트키", "button_count": "3"},
                              major_category_code="PRF000", minor_category_code="PRF100")
        assert response.status_code == 422
        response = transition(client, draft, "validate", item={"name": "스마트키", "subcategory": "스마트키", "button_count": 3},
                              major_category_code="PRF000", minor_category_code="PRF100")
        assert response.status_code == 200, response.text
        updated = response.json()
        result = updated["analysis"]
        assert result["item"]["color"] == "회색"
        assert result["item"]["button_count"] == 3 and "size" not in result["item"]
        assert result["observation"]["category_status"] == "mapped"
        assert result["observation"]["observed_item_type"] == "트렌치코트"
        ready = transition(client, updated, "approve", confirmed=True).json()
        notice = transition(client, ready, "publish").json()
        assert notice["minor_category_code"] == "PRF100"
        assert client.get("/notices", params={"category": "PRF000", "minor_category": "PRF100"}).json()["total"] == 1
        assert client.get("/notices", params={"category": "PRK000", "minor_category": "PRF100"}).status_code == 422


def test_card_identifiers_never_persist_or_publish(settings, photo):
    class CardModels(FixtureModels):
        def verify(self, photos, item, regions):
            assert regions == []
            result = super().verify(photos, item, regions)
            result.findings[0].reason = "가상 카드 사용자"
            return result

    supplied = {"card_number": "5423765432109876", "cardholder": "가상 카드 사용자", "cvv": "000",
                "card_type": "신용카드", "features": "보안코드 321"}
    with TestClient(create_app(settings, CardModels("신용카드", "신용카드", supplied))) as client:
        draft = submit(client, photo)
        assert draft["analysis"]["privacy"]["photo_available"] is False
        updated = transition(client, draft, "validate", item={"name": "신용카드", "subcategory": "신용카드",
                             "attributes": supplied}, description="유효기간 10/29 연락 010-0000-0000").json()
        ready = transition(client, updated, "approve", confirmed=True).json()
        published = transition(client, ready, "publish")
        assert published.status_code == 200, published.text
        for record in (json.dumps(updated, ensure_ascii=False), published.text,
                       client.get(f"/staff/drafts/{draft['id']}/logs", headers=AUTH).text):
            for secret in ("5423765432109876", "가상 카드 사용자", "보안코드 321", "10/29"):
                assert secret not in record


def test_cash_total_only_calculated_after_staff_confirms(settings, photo):
    given = [{"denomination": 500, "count": 3}, {"denomination": 100, "count": 2}]
    model_values = {"currency": "원", "total_amount": 9999, "cash_complete": True, "denominations": given}
    with TestClient(create_app(settings, FixtureModels("현금", "지폐", model_values))) as client:
        draft = submit(client, photo)
        assert draft["analysis"]["item"]["total_amount"] is None
        assert draft["analysis"]["item"]["denominations"] is None
        for confirmed in (False, True):
            draft = transition(client, draft, "validate", item={"name": "현금", "subcategory": "지폐",
                               "currency": "원", "denominations": given, "cash_complete": confirmed,
                               "total_amount": 1}, description="500원 3개").json()
            assert draft["analysis"]["item"]["total_amount"] == (1700 if confirmed else None)
        assert draft["analysis"]["privacy"]["verification_only"]["total_amount"] == 1700
        assert "500원" not in str(draft["analysis"]["privacy"]["public"])
        ready = transition(client, draft, "approve", confirmed=True).json()
        assert "1700" not in transition(client, ready, "publish").text


def test_new_schema_field_is_data_driven(monkeypatch):
    data = definitions()
    sample = {**data["fields"]["features"], "label": "접이부 특징"}
    monkeypatch.setitem(data["fields"], "hinge", sample)
    monkeypatch.setitem(data["major"], "의류", ["hinge"])
    item = {"name": "상의", "category": "의류"}
    schema = select_schema(item, classify(item))
    assert [field for field in schema["fields"] if field["field_key"] == "hinge"][0]["label"] == "접이부 특징"


def test_structured_response_accepts_legacy_observation_and_flat_details():
    recognized = Recognition.model_validate_json(OllamaModels.json_object(
        '사진 분석 결과\n{"item":{"name":"코트","subcategory":"트렌치 코트"},"photo_risk":"모름"}\n완료',
    ))
    assert recognized.observed_item_type == "트렌치 코트"
    assert recognized.photo_risk == "uncertain"
    details = AttributeExtraction.model_validate({
        "색상": "회색", "허용되지 않은 내용": "무시", "photo_evidence": {"색상": [1, "2"]},
    })
    assert details.attributes == {"color": "회색"}
    assert details.photo_evidence == {"color": [1]}


def test_legacy_coat_has_no_umbrella_fields_and_preserves_stored_values():
    snapshot = {
        "item": {"category": "기타", "name": "코트", "subcategory": "트렌치 코트", "color": "회색",
                 "handle_color": "갈색", "handle": "곡선", "detail_1": "이전 공통 필드"},
        "evidence": {"handle_color": {"value": "갈색"}, "name": {"value": "코트", "photos": [1]}},
        "findings": [{"field": "handle_color", "status": "uncertain", "reason": "미확인"}],
    }
    view = registration_view(snapshot, quantity=1)
    assert view["item_schema"]["schema_id"] == "trench-coat"
    assert view["observation"]["observed_item_type"] == "트렌치 코트"
    fields = {field["field_key"]: field for field in view["fields"]}
    assert fields["size"]["value"] is None
    assert {"handle_color", "handle", "detail_1"}.isdisjoint(fields)
    assert "handle_color" not in view["evidence"]
    assert snapshot["item"]["handle_color"] == "갈색"
