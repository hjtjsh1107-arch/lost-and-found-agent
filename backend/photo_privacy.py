from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from backend.images import mask
from backend.models import ModelFailure
from backend.privacy import POLICY, detect, normalized


KINDS = {rule["type"] for rule in POLICY["rules"]} | {"face", "personal_image", "unreadable", "manual", "contextual"}


def dimensions(photo):
    """정규화한 원본의 좌표계를 확인합니다."""
    with Image.open(BytesIO(photo)) as image:
        return image.size


def face_regions(photo):
    """로컬 얼굴 검출기로 의심 영역을 찾고 검출기를 쓸 수 없으면 실패로 반환합니다."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    try:
        xml = (Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml").read_text(encoding="utf8")
        storage = cv2.FileStorage(xml, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
        detector = cv2.CascadeClassifier()
        detector.read(storage.getFirstTopLevelNode())
        if detector.empty():
            return None
        with Image.open(BytesIO(photo)) as image:
            gray = np.asarray(image.convert("L"))
        return [[int(x), int(y), int(x + w), int(y + h)]
                for x, y, w, h in detector.detectMultiScale(gray, minNeighbors=5, minSize=(22, 22))]
    except (cv2.error, OSError, RuntimeError, ValueError, AttributeError):
        return None


def text_regions(regions, identifiers, contextual=()):
    """OCR 조각을 사진별로 연결해 맥락 및 구조화 식별자의 영역을 반환합니다."""
    selected = {}
    for entry in contextual:
        if 1 <= entry.region <= len(regions):
            selected.setdefault(entry.region - 1, set()).add(entry.kind if entry.kind in KINDS else "contextual")
    for index, region in enumerate(regions):
        types = {hit.kind for hit in detect(region.text, identifiers)}
        if region.score < POLICY["min_ocr_confidence"]:
            types.add("unreadable")
        if types:
            selected.setdefault(index, set()).update(types)
        for size in (2, 3):
            window = regions[index:index + size]
            if len(window) != size or any(part.photo != region.photo for part in window):
                continue
            parts = [normalized(part.text).strip() for part in window]
            for separator in (" ", ""):
                offsets, end = [], 0
                for part in parts:
                    offsets.append((end, end + len(part)))
                    end += len(part) + len(separator)
                for hit in detect(separator.join(parts), identifiers):
                    for offset, (start, end) in enumerate(offsets):
                        if hit.context_start < end and start < hit.context_end:
                            selected.setdefault(index + offset, set()).add(hit.kind)
    return selected


def bounds(polygon, width, height):
    """OCR 좌표를 이미지 안의 사각형으로 변환합니다."""
    x, y = zip(*polygon)
    return [max(0, min(x)), max(0, min(y)), min(width - 1, max(x)), min(height - 1, max(y))]


def create_photo_checks(photos, regions, identifiers, contextual, manual, read_text, *, retained, blocked, uncertain, mock, check_context):
    """규칙과 사람 지정 영역으로 불투명 이미지를 만들고 재검사 결과와 해시를 결합합니다."""
    selected = text_regions(regions, identifiers, contextual)
    images, checks = [], []
    for position, original in enumerate(photos, 1):
        width, height = dimensions(original)
        invalid = False
        found = [entry for check in retained if check["position"] == position
                 for entry in check["regions"] if entry["source"] != "staff"]
        for index, types in selected.items():
            region = regions[index]
            if region.photo == position:
                try:
                    found.append({"box": bounds(region.polygon, width, height), "types": sorted(types), "source": "ocr"})
                except (ValueError, TypeError):
                    invalid = True
        faces = [] if mock else face_regions(original)
        for box in faces or []:
            found.append({"box": box, "types": ["face"], "source": "visual"})
        for box in manual.get(str(position), []):
            found.append({"box": box, "types": ["manual"], "source": "staff"})
        found = list({(tuple(entry["box"]), tuple(entry["types"]), entry["source"]): entry for entry in found}.values())
        invalid |= any(x1 > x2 or y1 > y2 for x1, y1, x2, y2 in (entry["box"] for entry in found))
        polygons = [[(x1, y1), (x2, y2)] for x1, y1, x2, y2 in (entry["box"] for entry in found)]
        candidate = mask(original, polygons) if not invalid else mask(original, [], cover=True)
        sensitive = blocked or any(set(entry["types"]).intersection(POLICY["hold_image_types"]) for entry in found)
        reason = "policy" if sensitive else None
        residual = True
        if not (mock or uncertain or invalid or faces is None):
            try:
                reread = read_text([candidate])
                residual = bool(text_regions(reread, identifiers) or face_regions(candidate) != [])
                if not residual and reread:
                    residual = check_context(reread)
            except (ModelFailure, ValueError, OSError):
                residual = True
        if mock:
            reason = "mock"
        elif uncertain or invalid or faces is None or residual:
            reason = "unverified"
        safe = reason is None
        # 차단된 민감 유형의 미리보기에는 OCR/시각 영역이 검증된 결과만 사용합니다.
        covered = not safe and (reason != "policy" or not found or residual)
        result = mask(original, [], cover=True) if covered else candidate
        images.append(result)
        checks.append({
            "position": position, "width": width, "height": height, "regions": found,
            "masking_status": "covered" if covered else "masked" if found else "clean",
            "privacy_check_status": "passed" if safe else "blocked", "reason": reason,
            "original_allowed": not (sensitive or uncertain or mock or faces is None or residual or found),
            "sha256": sha256(result).hexdigest(),
        })
    return images, checks


def photo_certificates(privacy, photos):
    """현재 정책으로 인증한 공개 이미지 바이트만 게시 경계에서 통과시킵니다."""
    checks = privacy.get("photo_checks", [])
    if (privacy.get("policy") != POLICY["version"] or not privacy.get("photo_available")
            or not photos or len(checks) != len(photos)):
        return {}
    certified = {}
    for row, check in zip(photos, checks, strict=True):
        if (check["position"] != row.position or check["privacy_check_status"] != "passed"
                or not row.masked or not row.original or row.masked == row.original
                or sha256(row.masked).hexdigest() != check["sha256"]):
            return {}
        certified[str(row.position)] = check["sha256"]
    return certified
