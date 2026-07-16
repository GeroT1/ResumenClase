"""Genera los iconos de distribución a partir de la marca de ResumenClase."""
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SIZE = 1024


def rounded_line(draw: ImageDraw.ImageDraw, points, fill, width):
    draw.line(points, fill=fill, width=width, joint="curve")
    radius = width // 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def build() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Fondo graphite con una transición cálida muy sutil.
    gradient = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(32, 992):
        t = (y - 32) / 960
        color = tuple(
            round(a + (b - a) * t)
            for a, b in zip((23, 26, 32), (42, 28, 22))
        ) + (255,)
        gradient_draw.line((32, y, 992, y), fill=color)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, 992, 992), radius=224, fill=255)
    image.paste(gradient, (0, 0), mask)
    draw.rounded_rectangle((32, 32, 992, 992), radius=224, outline="#F2A65A", width=24)

    # Hoja de apuntes.
    draw.rounded_rectangle((246, 142, 778, 882), radius=80, fill="#FFF3E8")
    draw.polygon([(650, 142), (698, 142), (778, 222), (778, 270)], fill="#F2A65A")

    # Micrófono: una silueta gruesa que sigue legible a 16 px.
    draw.rounded_rectangle((427, 250, 597, 550), radius=85, fill="#F2A65A")
    draw.arc((372, 342, 652, 602), start=0, end=180, fill="#171A20", width=48)
    rounded_line(draw, [(512, 602), (512, 674)], "#171A20", 48)
    rounded_line(draw, [(432, 674), (592, 674)], "#171A20", 48)

    # Líneas del resumen.
    rounded_line(draw, [(348, 746), (676, 746)], "#4FC6B4", 36)
    rounded_line(draw, [(348, 816), (568, 816)], "#A993FF", 36)

    png = ASSETS / "icon.png"
    windows = ASSETS / "icon_windows.ico"
    image.save(png, optimize=True)
    image.save(
        windows,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(png)
    print(windows)


if __name__ == "__main__":
    build()
