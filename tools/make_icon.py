"""Draws the app icon: a bridge whose arch is also a two-way arrow, so one shape says both "bridge" and "exchange".

Kept to that single shape inside a 680px safe zone so it survives at 29px. It hints at the supported systems through
colour only, because their logos are trademarks. Needs Pillow: python tools/make_icon.py writes:
  assets/icon.png        rounded, for the desktop apps
  assets/icon-touch.png  square and opaque, since phones round home screen icons themselves
  assets/icon-32.png     for browser tabs
"""
import math
import pathlib

from PIL import Image, ImageDraw

SIZE = 1024
SCALE = 3
canvas = SIZE * SCALE
WHITE = (255, 255, 255, 255)
# Both ends keep white artwork above the 4.5:1 contrast the icon is checked against.
BLUE, TEAL = (0, 90, 190), (0, 122, 122)
ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"


def s(value):
    return round(value * SCALE)


def background():
    small = Image.new("RGB", (256, 256))
    pixels = small.load()
    for y in range(256):
        for x in range(256):
            t = (x + y) / 510
            pixels[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(BLUE, TEAL))
    return small.resize((canvas, canvas), Image.BICUBIC).convert("RGBA")


def artwork():
    art = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)
    centre, half_span, deck_y, crown_y = 512, 262, 700, 250
    weight = 92

    # The arch, stopping short of the deck to leave room for an arrowhead at each end.
    points = []
    for i in range(121):
        t = i / 120
        # A half ellipse: it meets the arrowheads vertically and keeps a broad, round crown like a real arch.
        x = centre - half_span * math.cos(math.pi * t)
        y = deck_y - 140 - (deck_y - 140 - crown_y) * math.sin(math.pi * t)
        points.append((x, y))
    draw.line([(s(x), s(y)) for x, y in points], fill=WHITE, width=s(weight), joint="curve")
    for x, y in (points[0], points[-1]):
        r = weight / 2
        draw.ellipse((s(x - r), s(y - r), s(x + r), s(y + r)), fill=WHITE)

    # Arrowheads pointing down onto the deck at both ends: data crossing each way.
    for x in (centre - half_span, centre + half_span):
        draw.polygon([(s(x - 92), s(deck_y - 150)), (s(x + 92), s(deck_y - 150)), (s(x), s(deck_y - 34))], fill=WHITE)

    # The deck.
    draw.rounded_rectangle((s(centre - 340), s(deck_y), s(centre + 340), s(deck_y + 74)), s(37), fill=WHITE)
    return art


def main():
    ASSETS.mkdir(exist_ok=True)
    full = Image.alpha_composite(background(), artwork())

    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle((s(40), s(40), s(984), s(984)), s(210), fill=255)
    rounded = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    rounded.paste(full, mask=mask)

    rounded.resize((SIZE, SIZE), Image.LANCZOS).save(ASSETS / "icon.png")
    rounded.resize((32, 32), Image.LANCZOS).save(ASSETS / "icon-32.png")
    full.convert("RGB").resize((180, 180), Image.LANCZOS).save(ASSETS / "icon-touch.png")

    # White artwork against both ends of the gradient, by the WCAG formula.
    def luminance(colour):
        channels = [c / 255 for c in colour]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    for name, colour in (("blue", BLUE), ("teal", TEAL)):
        print(f"contrast on {name}: {1.05 / (luminance(colour) + 0.05):.1f}:1")


if __name__ == "__main__":
    main()
