import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

POLICIES = Path(__file__).with_name("policies")
POLICY = json.loads((POLICIES / "privacy.json").read_text(encoding="utf-8"))
MASKED = "[마스킹]"

RULES = [(entry, re.compile(entry["pattern"], re.IGNORECASE)) for entry in POLICY["rules"]]
PARTIAL = re.compile(r"[\w]○{2,}|\b0\d{1,2}-\*{4}-\*{4}|\*{3}@[\w.-]+\.[a-zA-Z]{2,}")


@dataclass(frozen=True)
class Detection:
    kind: str
    visibility: str
    start: int
    end: int
    replacement: str
    context_start: int
    context_end: int


def normalized(value):
    """형식이 다른 호환 문자와 보이지 않는 구분 문자를 통일합니다."""
    value = unicodedata.normalize("NFKC", value or "")
    return "".join(char for char in value if unicodedata.category(char) != "Cf")


def detect(value, literals=()):
    """규칙 우선순위에 따라 원문 없이 유형, 위치, 치환자만 반환합니다."""
    value = normalized(value)
    hits = []
    for literal in sorted({normalized(entry).strip() for entry in literals if entry}, key=len, reverse=True):
        if len(literal) >= 2 and literal != MASKED:
            regex = re.compile(r"\s*".join(re.escape(char) for char in literal if not char.isspace()), re.I)
            hits.extend(Detection("name", "restricted", *match.span(), literal[0] + "○○", *match.span())
                        for match in regex.finditer(value))
    for rule, pattern in RULES:
        for match in pattern.finditer(value):
            start, end = match.span("value")
            if any(start < hit.end and hit.start < end for hit in hits):
                continue
            original = match["value"]
            replacement = MASKED
            if rule.get("mask") == "email":
                replacement = "***@" + original.split("@", 1)[1]
            elif rule.get("mask") == "phone":
                prefix = re.sub(r"\D", "", original.replace("+82", "0"))
                replacement = (prefix[:2] if prefix.startswith("02") else prefix[:3]) + "-****-****"
            elif rule.get("mask") == "name":
                replacement = original[0] + "○○"
            hits.append(Detection(rule["type"], rule["visibility"], start, end, replacement, *match.span()))
    return sorted(hits, key=lambda hit: hit.start)


def redact(value: str | None, literals=(), *, partial=False) -> str | None:
    """등록자 식별자와 규칙으로 탐지한 민감정보를 문자열에서 가립니다."""
    if value is None:
        return None
    value = normalized(value)
    for hit in reversed(detect(value, literals)):
        value = value[:hit.start] + (hit.replacement if partial else MASKED) + value[hit.end:]
    if not partial:
        value = PARTIAL.sub(MASKED, value)
    return value.strip()


def publishable(snapshot):
    """게시 직전에 공개 허용 항목, 필수 입력, 마스킹을 다시 검사합니다."""
    privacy = snapshot["privacy"]
    if (privacy.get("error") or privacy.get("policy") != POLICY["version"]
            or privacy.get("privacy_check_status") != "passed"):
        return False
    public = privacy["public"]
    if set(public) != set(POLICY["public_fields"]):
        return False
    for field in ("category", "name", "title", "region", "location", "institution"):
        if not public.get(field) or not public[field].replace(MASKED, "").strip():
            return False
    return all(value is None or isinstance(value, str) and redact(value) == value for value in public.values())


def public_text(public, attributes, schema=None):
    """공개 검증을 통과한 속성으로만 제목과 설명을 작성합니다."""
    name = public["name"]
    if not name:
        return None, None
    subtype = attributes.get("subcategory")
    if subtype and subtype != name:
        name = subtype if name in subtype else f"{subtype} {name}"
    item = " ".join(part for part in (public["color"], name) if part)
    title = f"{item} 습득" if len(item) <= 117 else f"{public['name'][:117]} 습득"
    description = [f"{item}입니다."]
    if shape := attributes.get("shape"):
        description.append(f"확인된 형태는 ‘{shape}’입니다.")
    if colors := attributes.get("part_colors"):
        description.append(f"부위별 색상은 {colors}입니다.")
    if handle_color := attributes.get("handle_color"):
        description.append(f"손잡이 색상은 {handle_color}입니다.")
    special = {"subcategory", "shape", "part_colors", "handle_color"}
    for field in (schema or {}).get("fields", []):
        field_key = field["field_key"]
        if field_key in attributes and field_key not in special:
            description.append(f"{field['label']}은 ‘{attributes[field_key]}’입니다.")
    parts = []
    for sentence in description:
        if len(" ".join([*parts, sentence])) <= 300:
            parts.append(sentence)
    return title, " ".join(parts)
