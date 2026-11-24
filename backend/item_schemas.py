import json
from functools import lru_cache
from pathlib import Path

from backend.categories import category_pair, classification_view, key, taxonomy


@lru_cache
def definitions():
    """유형별 필드와 정책을 선언 파일에서 읽습니다."""
    path = Path(__file__).with_name("policies") / "item_schemas.json"
    return json.loads(path.read_text(encoding="utf-8"))


def observed_profile(item, major_name=None):
    """공식 분류와 양립하는 관찰 유형 특화 스키마를 찾습니다."""
    text = key(f"{item.get('subcategory') or ''} {item.get('name') or ''}")
    matches = [
        (len(key(alias)), profile) for profile in definitions()["observed"]
        if major_name is None or profile["major"] == major_name
        for alias in profile["aliases"] if key(alias) in text
    ]
    return max(matches, key=lambda match: match[0])[1] if matches else None


def classify(item, *, proposed=None, selection=None):
    """관찰 단서와 모델 후보를 공식 코드표에 대조하며 담당자 선택을 우선합니다."""
    proposed = proposed or {}
    profile = observed_profile(item)
    legacy = {"휴대전화": "휴대폰"}
    hint = profile["major"] if profile else legacy.get(item.get("category"), item.get("category"))
    labels = {key(row["name"]): row for row in taxonomy()["majors"]}
    inferred_major = next((labels[label] for label in (
        key(hint), key(item.get("subcategory")), key(item.get("name")),
    ) if label in labels), None)
    model_minor = None
    if selection is not None:
        major, minor = category_pair(selection.get("major_category_code"), selection.get("minor_category_code"))
    else:
        try:
            model_major, model_minor = category_pair(proposed.get("major_category_code"), proposed.get("minor_category_code"))
        except ValueError:
            model_major = None
        major = inferred_major or model_major
        eligible = major and major["name"] not in definitions()["staff_only_categories"]
        minor = None
        if eligible:
            exact = [key(item.get("subcategory")), key(profile.get("matched_minor") if profile else None)]
            allowed = {key(row["name"]): row for row in major["minors"]
                       if not any(term in row["name"] for term in ("남성", "여성", "아기"))}
            minor = next((allowed[name] for name in exact if name in allowed), None)
        elif major:
            inferred_major, major = major, None
    candidate_major = major or inferred_major or next(
        (row for row in taxonomy()["majors"] if row["name"] == "기타물품"), None,
    )
    candidates = []
    if candidate_major and not minor:
        preferred = set(profile.get("suggested_minor", [])) if profile and candidate_major["name"] == profile["major"] else set()
        for row in sorted(candidate_major["minors"], key=lambda entry: (entry["name"] not in preferred, entry != model_minor)):
            candidates.append({"major_code": candidate_major["code"], "major_name": candidate_major["name"],
                               "minor_code": row["code"], "minor_name": row["name"]})
    return classification_view(major, minor, source="staff" if selection is not None else "observed", candidates=candidates)


def select_schema(item, classification):
    """공통 필드에 관찰 유형, 공식 소분류 또는 대분류 필드를 결합합니다."""
    data = definitions()
    major = classification.get("major_category_name")
    minor = classification.get("minor_category_name")
    profile = observed_profile(item, major)
    if profile:
        schema_id, fields = profile["schema_id"], profile["fields"]
    elif minor in data["minor"].get(major, {}):
        schema_id = f"official-{classification['minor_category_code']}"
        fields = data["minor"][major][minor]
    elif major in data["major"]:
        schema_id = f"official-{classification['major_category_code']}"
        fields = data["major"][major]
    else:
        schema_id, fields = "general", data["fallback"]
    field_keys = list(dict.fromkeys(data["common"] + fields))
    return {
        "schema_id": schema_id, "schema_version": data["schema_version"],
        "fields": [
            {"field_key": field, **data["fields"][field], "display_order": index}
            for index, field in enumerate(field_keys)
        ],
    }


def normalize_value(definition, value):
    """단일 필드의 자료형과 선택지를 검사하고 공란을 미확인으로 통일합니다."""
    if value is None or (isinstance(value, str) and value.strip().casefold() in {"", "unknown", "null", "미확인", "확인 불가"}):
        return None
    kind = definition["data_type"]
    if kind in {"string", "select"} and isinstance(value, str):
        value = value.strip()
        if len(value) <= 120 and (kind == "string" or value in definition["options"]):
            return value
    if kind == "number" and type(value) is int and 0 <= value <= 1_000_000_000:
        return value
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "multiselect" and isinstance(value, list) and all(isinstance(x, str) and x in definition["options"] for x in value):
        return list(dict.fromkeys(value)) or None
    if kind == "denominations" and isinstance(value, list) and len(value) <= 40 and all(
        isinstance(row, dict) and set(row) == {"denomination", "count"}
        and type(row["denomination"]) is int and 1 <= row["denomination"] <= 1_000_000_000
        and type(row["count"]) is int and 1 <= row["count"] <= 1_000
        for row in value
    ):
        if len({row["denomination"] for row in value}) == len(value):
            return value or None
    raise ValueError(f"{definition['label']} 항목의 값이나 자료형을 확인해주세요.")


def project_item(values, schema, *, strict=False):
    """유형에 적용되는 값만 남기고 담당자 확인에 근거해 총액을 계산합니다."""
    result = {"category": values.get("category") or "기타"}
    for definition in schema["fields"]:
        field = definition["field_key"]
        if field in {"major_category_code", "minor_category_code", "quantity"}:
            continue
        value = values.get(field) if definition["store"] and definition["extraction_source"] != "calculated" else None
        if not strict:
            kind = definition["data_type"]
            if kind == "string" and isinstance(value, dict):
                value = ", ".join(f"{key} {part}" for key, part in value.items() if isinstance(part, str))
            if kind == "string" and isinstance(value, list):
                value = ", ".join(part for part in value if isinstance(part, str))
            if kind == "number" and isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10:
                value = int(value)
            if kind == "boolean" and isinstance(value, str):
                value = {"true": True, "false": False, "예": True, "아니요": False}.get(value.strip().casefold())
        try:
            result[field] = normalize_value(definition, value)
        except ValueError:
            if strict:
                raise
            result[field] = None
    if "total_amount" in result and result["cash_complete"] is True and result["denominations"] and result["currency"]:
        result["total_amount"] = sum(row["denomination"] * row["count"] for row in result["denominations"])
    return result


def formatted(value):
    """검증 및 검색 가능한 문자열로 변환하되 내부 자료형은 유지합니다."""
    if value is None or isinstance(value, str):
        return value
    if type(value) is bool:
        return "예" if value else "아니요"
    if isinstance(value, list):
        return ", ".join(f"{entry['denomination']} × {entry['count']}" if isinstance(entry, dict) else str(entry) for entry in value)
    return str(value)
