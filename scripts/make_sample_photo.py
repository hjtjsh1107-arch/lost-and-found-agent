import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def umbrella_photo(path: Path):
    image = Image.new("RGB", (520, 760), "#f1f2ee")
    draw = ImageDraw.Draw(image)
    draw.polygon([(246, 74), (273, 74), (306, 384), (276, 540), (244, 540), (209, 384)], fill="#212731")
    draw.rounded_rectangle((242, 99, 278, 631), radius=14, fill="#353a44")
    draw.arc((252, 550, 355, 722), 0, 180, fill="#252933", width=24)
    draw.line((264, 603, 264, 641), fill="#252933", width=24)
    image.save(path, format="PNG")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a synthetic black umbrella photo for PoC tests.")
    parser.add_argument("path", nargs="?", type=Path, default=Path("instance/sample-umbrella.png"))
    arguments = parser.parse_args()
    arguments.path.parent.mkdir(parents=True, exist_ok=True)
    umbrella_photo(arguments.path)
    print(arguments.path)
