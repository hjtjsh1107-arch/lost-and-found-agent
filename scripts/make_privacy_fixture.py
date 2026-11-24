"""가상 카드 이미지를 재현합니다. 인물과 번호는 테스트용입니다."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make_card(path: Path):
    """식별자가 있는 영역을 테스트할 수 있는 합성 카드를 생성합니다."""
    image = Image.new("RGB", (800, 500), "#e8e6e3")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((55, 45, 745, 455), radius=30, fill="#16385c")
    font = ImageFont.load_default(size=36)
    for point, text in [
        ((115, 95), "DEMO BANK - NOT VALID"),
        ((115, 205), "0000 0000 0000 0000"),
        ((115, 260), "NAME: DEMO HOLDER"),
        ((115, 320), "EXP: 01/49   CVC: 000"),
    ]:
        draw.text(point, text, font=font, fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


if __name__ == "__main__":
    make_card(Path("tests/fixtures/privacy-card.png"))
