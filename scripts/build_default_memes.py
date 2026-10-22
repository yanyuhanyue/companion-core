"""Build the project's small geometric expression pack without external artwork."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "assets" / "memes"
INK = "#313631"


def build(category):
    image = Image.new("RGBA", (512, 512))
    draw = ImageDraw.Draw(image)
    fill = {"happy": "#ffe185", "like": "#ffb8a9", "surprised": "#c4e8d8",
            "morning": "#ffd282", "sleep": "#c9cdf0"}[category]
    if category == "morning":
        for start, end in (
            ((256, 10), (256, 43)), ((256, 469), (256, 502)),
            ((10, 256), (43, 256)), ((469, 256), (502, 256)),
            ((82, 82), (105, 105)), ((407, 407), (430, 430)),
            ((82, 430), (105, 407)), ((407, 105), (430, 82)),
        ):
            draw.line((start, end), fill="#e7a73b", width=15)
    draw.ellipse((59, 59, 453, 453), fill=fill, outline=INK, width=12)
    draw.ellipse((110, 270, 178, 304), fill="#f29b91")
    draw.ellipse((334, 270, 402, 304), fill="#f29b91")
    if category in {"happy", "morning"}:
        draw.arc((143, 190, 207, 247), 190, 350, fill=INK, width=12)
        draw.arc((305, 190, 369, 247), 190, 350, fill=INK, width=12)
        draw.arc((174, 231, 338, 340), 5, 175, fill=INK, width=13)
    elif category == "like":
        for x in (169, 331):
            draw.ellipse((x - 33, 183, x + 4, 220), fill="#bf5364")
            draw.ellipse((x - 4, 183, x + 33, 220), fill="#bf5364")
            draw.polygon(((x - 31, 208), (x + 31, 208), (x, 249)), fill="#bf5364")
        draw.arc((184, 252, 328, 339), 0, 180, fill=INK, width=12)
    elif category == "surprised":
        for x in (177, 335):
            draw.ellipse((x - 13, 196, x + 13, 238), fill=INK)
        draw.ellipse((226, 284, 286, 358), fill=INK)
    else:
        draw.line(((143, 223), (207, 223)), fill=INK, width=12)
        draw.line(((305, 223), (369, 223)), fill=INK, width=12)
        draw.arc((221, 289, 291, 334), 180, 355, fill=INK, width=11)
        for x, y, size in ((340, 64, 38), (396, 28, 28)):
            draw.line(((x, y), (x + size, y), (x, y + size), (x + size, y + size)),
                      fill=INK, width=7)
    folder = ROOT / category
    folder.mkdir(parents=True, exist_ok=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(folder / "companion.png")


if __name__ == "__main__":
    for category in ("happy", "like", "surprised", "morning", "sleep"):
        build(category)
