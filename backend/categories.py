import json
import unicodedata
from functools import lru_cache

from backend.config import ROOT


def key(name):
    """표시명은 보존하면서 비교용 문자열만 정규화합니다."""
    return "".join(unicodedata.normalize("NFKC", name or "").casefold().split())


@lru_cache
def taxonomy():
    """공식 JSON에서 대분류별 소분류와 출처 상태를 구성합니다."""
    path = ROOT / "data" / "categories.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    majors = {}
    minor_codes = set()
    for row in data.get("categories", []):
        parent = majors.setdefault(row["major_code"], {"code": row["major_code"], "name": row["major_name"], "minors": []})
        if parent["name"] != row["major_name"] or row["minor_code"] in minor_codes:
            raise ValueError("공식 분류 스냅샷의 코드 관계가 일관되지 않습니다.")
        minor_codes.add(row["minor_code"])
        parent["minors"].append({"code": row["minor_code"], "name": row["minor_name"]})
    return {"majors": list(majors.values()), "web_comparison": data.get("web_comparison", "not_verified"),
            "status": data.get("status", "unavailable"), "major_count": len(majors), "minor_count": len(minor_codes)}


def category_pair(major_code=None, minor_code=None):
    """공식 코드의 존재와 소분류의 상위 소속을 검사합니다."""
    major = next((row for row in taxonomy()["majors"] if row["code"] == major_code), None)
    minor = next((row for row in major["minors"] if row["code"] == minor_code), None) if major else None
    if (major_code and not major) or (minor_code and not minor):
        raise ValueError("공식 분류 코드를 확인해주세요. 소분류는 선택한 대분류에 속해야 합니다.")
    return major, minor


def observation(item, *, observed=None, classification=None):
    """사진 관찰값을 공식 분류와 별도로 전달합니다."""
    if classification is None:
        major = next((row for row in taxonomy()["majors"] if key(row["name"]) == key(item.get("category"))), None)
        minor = next((row for row in major["minors"] if key(row["name"]) == key(item.get("subcategory"))), None) if major else None
        classification = classification_view(major, minor)
    image_item = observed if observed is not None else item
    return {
        **classification,
        "observed_item_name": image_item.get("name"),
        "observed_item_type": image_item.get("subcategory"),
        "attributes": {field: value for field, value in image_item.items()
                       if value is not None and field not in {"category", "name", "subcategory"}},
    }


def classification_view(major, minor, *, source="observed", candidates=()):
    """확정된 공식 코드와 검토 후보를 구분합니다."""
    return {
        "major_category_code": major["code"] if major else None,
        "major_category_name": major["name"] if major else None,
        "minor_category_code": minor["code"] if minor else None,
        "minor_category_name": minor["name"] if minor else None,
        "category_status": "mapped" if major and minor else "unmapped",
        "category_source": source,
        "category_candidates": list(candidates),
    }
