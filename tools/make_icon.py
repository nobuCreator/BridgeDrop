"""Draws the app icon. Needs Pillow: python tools/make_icon.py writes assets/icon.png."""
import pathlib

from PIL import Image, ImageDraw

SIZE = 1024
SCALE = 2
canvas = SIZE * SCALE


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


gradient = Image.new("RGB", (canvas, canvas))
top, bottom = (56, 168, 248), (10, 92, 186)
for y in range(canvas):
    gradient.paste(lerp(top, bottom, y / canvas), (0, y, canvas, y + 1))

margin, radius = 90 * SCALE, 210 * SCALE
mask = Image.new("L", (canvas, canvas), 0)
ImageDraw.Draw(mask).rounded_rectangle((margin, margin, canvas - margin, canvas - margin), radius, fill=255)
icon = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
icon.paste(gradient, mask=mask)

# A soft sheen across the top half, for the same glass feel as the app.
sheen = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
sheen_mask = Image.new("L", (canvas, canvas), 0)
for y in range(margin, canvas // 2):
    sheen_mask.paste(round(46 * (1 - (y - margin) / (canvas // 2 - margin))), (0, y, canvas, y + 1))
sheen.paste((255, 255, 255, 255), mask=sheen_mask)
icon = Image.composite(Image.alpha_composite(icon, sheen), icon, mask)

draw = ImageDraw.Draw(icon)
stroke = 74 * SCALE
white = (255, 255, 255, 255)


def line(points):
    draw.line(points, fill=white, width=stroke, joint="curve")
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=white)


c = canvas // 2
line([(c, 270 * SCALE), (c, 610 * SCALE)])
line([(c - 170 * SCALE, 450 * SCALE), (c, 620 * SCALE), (c + 170 * SCALE, 450 * SCALE)])
line([(c - 270 * SCALE, 640 * SCALE), (c - 270 * SCALE, 730 * SCALE), (c + 270 * SCALE, 730 * SCALE), (c + 270 * SCALE, 640 * SCALE)])

out = pathlib.Path(__file__).resolve().parent.parent / "assets" / "icon.png"
out.parent.mkdir(exist_ok=True)
icon.resize((SIZE, SIZE), Image.LANCZOS).save(out)
print(out)
