from io import BytesIO

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from backend.config import MAX_IMAGE_BYTES

FORMATS = {"JPEG", "PNG", "GIF", "BMP", "WEBP"}


def encode(image: Image.Image, *, masked=False) -> bytes:
    """픽셀만 새 PNG에 저장해 EXIF와 원본 메타데이터를 제거합니다."""
    output = BytesIO()
    clean = Image.new("RGB", image.size)
    clean.paste(image)
    clean.save(output, format="PNG", compress_level=1 if masked else 6)
    return output.getvalue()


def normalize(raw: bytes) -> bytes:
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("사진 한 장의 최대 용량은 8 MB입니다.")
    try:
        with Image.open(BytesIO(raw)) as original:
            if original.format not in FORMATS or original.width * original.height > 25_000_000:
                raise ValueError("지원하지 않는 형식 또는 해상도입니다.")
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((1600, 1600))
            clean = Image.new("RGB", image.size)
            clean.paste(image)
            return encode(clean)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("올바른 이미지 파일을 선택해주세요.") from error


def mask(raw: bytes, polygons: list[list[list[int]]], *, cover: bool = False) -> bytes:
    """감지한 모든 영역을 여백과 불투명 색상으로 덮습니다."""
    with Image.open(BytesIO(raw)) as image:
        if cover:
            return encode(Image.new("RGB", image.size, "#cbd5e1"), masked=True)
        render = ImageDraw.Draw(image)
        for polygon in polygons:
            x, y = zip(*polygon)
            render.rectangle((min(x) - 8, min(y) - 8, max(x) + 8, max(y) + 8), fill="#151c29")
        return encode(image, masked=True)
