from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

from backend.item_schemas import definitions

Category = Literal["우산", "휴대전화", "지갑", "카드", "가방", "기타"]
Status = Literal["verified", "uncertain", "rejected"]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Feature = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


class Input(BaseModel):
    found_at: AwareDatetime
    region: ShortText
    location: ShortText
    finder: ShortText
    institution: ShortText
    memo: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    quantity: int = Field(default=1, ge=1, le=999)

    @field_validator("found_at")
    @classmethod
    def no_future(cls, value):
        if value > datetime.now(timezone.utc) + timedelta(minutes=1):
            raise ValueError("습득일시는 미래일 수 없습니다.")
        return value


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category = "기타"
    subcategory: Feature | None = None
    name: Feature | None
    brand: Feature | None = None
    model: Feature | None = None
    color: Feature | None
    material: Feature | None = None
    shape: Feature | None = None
    part_colors: Feature | None = None
    handle_color: Feature | None = None
    handle: Feature | None = None
    pattern: Feature | None = None
    accessories: Feature | None = None
    features: Feature | None = None
    damage: Feature | None = None
    detail_1: Feature | None = None
    detail_2: Feature | None = None

    @field_validator("*", mode="before")
    @classmethod
    def normalize_unknown(cls, value, info):
        if info.field_name != "category" and isinstance(value, str):
            if value.strip().lower() in {"", "unknown", "null", "미확인", "확인 불가"}:
                return None
        return value


class Extraction(BaseModel):
    item: Item
    photo_evidence: dict[str, list[int]] = Field(default_factory=dict)
    photo_risk: Literal["low", "high", "uncertain"] = "uncertain"
    uncertain_fields: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def normalize_observations(cls, value):
        if not isinstance(value, dict):
            return value
        observed = value.get("item", value)
        if not isinstance(observed, dict) or not (observed.keys() & Item.model_fields.keys()):
            return value
        item = {field: content for field, content in observed.items() if field in Item.model_fields}
        parts = item.get("part_colors")
        if isinstance(parts, dict) and all(isinstance(part, str) and isinstance(color, str) for part, color in parts.items()):
            if not item.get("handle_color"):
                item["handle_color"] = next((color for part, color in parts.items() if part.casefold() in {"handle", "손잡이"}), None)
        if isinstance(item.get("color"), dict):
            shades = item["color"]
            for field, aliases in (("color", ("body", "main", "fabric", "본체", "주색")),
                                   ("handle_color", ("handle", "손잡이"))):
                if field == "color" or not item.get(field):
                    item[field] = next((shades[key] for key in aliases if isinstance(shades.get(key), str)), None)
            if not parts:
                item["part_colors"] = shades
        labels = {"handle": "손잡이", "body": "본체", "main": "주색", "fabric": "천", "case": "케이스",
                  "strap": "끈", "state": "상태", "shape": "형태", "color": "색상"}
        for field in Item.model_fields.keys() - {"category"}:
            content = item.get(field)
            if isinstance(content, list):
                content = ", ".join(entry for entry in content if isinstance(entry, str))
            elif isinstance(content, dict):
                content = ", ".join(f"{labels.get(key.casefold(), key)} {entry}" for key, entry in content.items()
                                    if isinstance(entry, str))
            item[field] = content if isinstance(content, str) and 0 < len(content.strip()) <= 120 else None
        category = item.get("category")
        item["category"] = category if category in ("우산", "휴대전화", "지갑", "카드", "가방", "기타") else "기타"
        evidence = value.get("photo_evidence")
        evidence = {field: [index for index in indexes if type(index) is int]
                    for field, indexes in evidence.items() if field in Item.model_fields and isinstance(indexes, list)
                    } if isinstance(evidence, dict) else {}
        risk = value.get("photo_risk")
        return {**value, "item": item, "photo_evidence": evidence,
                "photo_risk": risk if risk in ("low", "high", "uncertain") else "uncertain"}


class Finding(BaseModel):
    field: str
    status: Status
    reason: Feature


class Recognition(BaseModel):
    name: Feature | None
    observed_item_type: Feature | None = None
    major_category_code: Feature | None = None
    minor_category_code: Feature | None = None
    photo_risk: Literal["low", "high", "uncertain"] = "uncertain"

    @model_validator(mode="before")
    @classmethod
    def normalize_observation(cls, value):
        """응답에 포함된 실제 물품 표기만 읽고 해석할 수 없는 부가값은 미확인으로 처리합니다."""
        if not isinstance(value, dict):
            return value
        item = value.get("item", value)
        if not isinstance(item, dict):
            return value
        name = item.get("name") or item.get("item_name") or item.get("물품명")
        observed = (item.get("observed_item_type") or item.get("subcategory") or item.get("item_type")
                    or item.get("물품 유형"))
        risk = value.get("photo_risk")
        return {
            "name": name if isinstance(name, str) and name.strip() else observed,
            "observed_item_type": observed if isinstance(observed, str) else None,
            **{field: item.get(field) if isinstance(item.get(field), str) else None
               for field in ("major_category_code", "minor_category_code")},
            "photo_risk": risk if risk in ("low", "high", "uncertain") else "uncertain",
        }


class AttributeExtraction(BaseModel):
    attributes: dict[str, JsonValue] = Field(max_length=45)
    photo_evidence: dict[str, list[int]] = Field(default_factory=dict)
    uncertain_fields: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def normalize_attribute_object(cls, value):
        """VLM이 반환한 중첩 또는 평면 속성 객체를 한 가지 계약으로 정규화합니다."""
        if not isinstance(value, dict):
            return value
        supplied = value.get("attributes", value.get("item", value))
        if not isinstance(supplied, dict):
            return value
        fields = definitions()["fields"]
        labels = {spec["label"]: key for key, spec in fields.items()}
        attributes = {
            labels.get(field, field): entry for field, entry in supplied.items()
            if labels.get(field, field) in fields
        }
        if attributes or "attributes" in value:
            evidence = value.get("photo_evidence")
            evidence = ({labels.get(field, field): [index for index in indexes if type(index) is int]
                         for field, indexes in evidence.items() if isinstance(indexes, list)}
                        if isinstance(evidence, dict) else {})
            uncertain = value.get("uncertain_fields")
            uncertain = ([labels.get(field, field) for field in uncertain if isinstance(field, str)]
                         if isinstance(uncertain, list) else [])
            return {"attributes": attributes, "photo_evidence": evidence, "uncertain_fields": uncertain}
        return value


class VisualReview(BaseModel):
    findings: list[Finding] = Field(max_length=35)

    @model_validator(mode="before")
    @classmethod
    def compact_findings(cls, value):
        if not isinstance(value, dict) or "findings" in value:
            return value
        statuses = ("verified", "uncertain", "rejected")
        if not any(isinstance(value.get(status), list) for status in statuses):
            return value
        reasons = value.get("reasons") or {}
        reasons = reasons if isinstance(reasons, dict) else {}
        defaults = {"verified": "사진과 일치합니다.", "uncertain": "사진에서 충분히 확인되지 않습니다.",
                    "rejected": "사진과 추출 결과가 일치하지 않습니다."}
        decisions = {
            field: status for status in statuses if isinstance(value.get(status), list)
            for field in value[status] if isinstance(field, str) and (field in Item.model_fields or field in definitions()["fields"])
        }
        return {"findings": [
            {"field": field, "status": status,
             "reason": reasons[field][:120] if isinstance(reasons.get(field), str) else defaults[status]}
            for field, status in decisions.items()
        ]}


class OCRFinding(BaseModel):
    region: int = Field(ge=1, le=2000)
    kind: str = Field(max_length=32)


class PrivacyReview(BaseModel):
    restricted_fields: list[str] = Field(max_length=25)
    hold_photos: bool
    ocr_findings: list[OCRFinding] = Field(default_factory=list, max_length=200)


class Revision(BaseModel):
    revision: int = Field(ge=1)


class Correction(Revision):
    item: dict[str, JsonValue] = Field(max_length=45)
    major_category_code: ShortText | None = None
    minor_category_code: ShortText | None = None
    title: Feature | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    region: ShortText | None = None
    location: ShortText | None = None
    institution: ShortText | None = None
    memo: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    quantity: int | None = Field(default=None, ge=1, le=999)


class SchemaSelection(BaseModel):
    name: Feature | None = None
    subcategory: Feature | None = None
    major_category_code: ShortText | None = None
    minor_category_code: ShortText | None = None


class Approval(Revision):
    confirmed: Literal[True]
    publish_photos: bool = False


class ManualMask(Revision):
    position: int = Field(ge=1, le=3)
    boxes: list[tuple[int, int, int, int]] = Field(max_length=30)
