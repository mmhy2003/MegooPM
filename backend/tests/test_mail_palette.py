"""The email palette is a hand-transcoded copy of the app's oklch tokens.

The converter lives here rather than in the package: production never converts
anything, and shipping a colour-space routine that nothing calls is dead weight.
It exists so a mistyped hex constant is caught.
"""

from __future__ import annotations

import math

from app.services.mail.palette import DARK, LIGHT

# The oklch sources, copied from frontend/src/app/globals.css.
LIGHT_SOURCE = {
    "background": (0.975, 0.01, 220),
    "card": (1.0, 0.0, 0.0),
    "foreground": (0.22, 0.05, 290),
    "primary": (0.50, 0.14, 205),
    "primary_foreground": (0.99, 0.01, 200),
    "muted_foreground": (0.48, 0.05, 260),
    "border": (0.86, 0.04, 210),
    "destructive": (0.54, 0.24, 20),
    "success": (0.50, 0.16, 145),
}
DARK_SOURCE = {
    "background": (0.15, 0.03, 285),
    "card": (0.19, 0.035, 285),
    "foreground": (0.95, 0.02, 200),
    "primary": (0.85, 0.16, 195),
    "primary_foreground": (0.15, 0.03, 285),
    "muted_foreground": (0.72, 0.04, 215),
    "border": (0.30, 0.05, 285),
    "destructive": (0.70, 0.24, 15),
    "success": (0.85, 0.22, 135),
}


def oklch_to_hex(lightness: float, chroma: float, hue_deg: float) -> str:
    """Convert an oklch triple to an sRGB hex string."""
    hue = math.radians(hue_deg)
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    long_, med, short = l_**3, m_**3, s_**3
    red = 4.0767416621 * long_ - 3.3077115913 * med + 0.2309699292 * short
    green = -1.2684380046 * long_ + 2.6097574011 * med - 0.3413193965 * short
    blue = -0.0041960863 * long_ - 0.7034186147 * med + 1.7076147010 * short

    def encode(channel: float) -> int:
        srgb = 1.055 * (channel ** (1 / 2.4)) - 0.055 if channel > 0.0031308 else 12.92 * channel
        return max(0, min(255, round(srgb * 255)))

    return "#%02x%02x%02x" % (encode(red), encode(green), encode(blue))


def test_light_palette_matches_its_oklch_source() -> None:
    for name, (lightness, chroma, hue) in LIGHT_SOURCE.items():
        assert LIGHT[name] == oklch_to_hex(lightness, chroma, hue), name


def test_dark_palette_matches_its_oklch_source() -> None:
    for name, (lightness, chroma, hue) in DARK_SOURCE.items():
        assert DARK[name] == oklch_to_hex(lightness, chroma, hue), name


def test_both_themes_define_the_same_tokens() -> None:
    # A token present in one theme and missing in the other renders a template
    # correctly in light mode and raises a KeyError in dark.
    assert LIGHT.keys() == DARK.keys()


def test_every_value_is_a_six_digit_hex_colour() -> None:
    # Email clients accept #rrggbb. Shorthand and named colours are not
    # uniformly supported, and oklch() is supported nowhere.
    for theme in (LIGHT, DARK):
        for name, value in theme.items():
            assert len(value) == 7 and value.startswith("#"), name
            int(value[1:], 16)
