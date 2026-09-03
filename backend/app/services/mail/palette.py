"""The app's colour tokens, transcoded for email.

``frontend/src/app/globals.css`` authors the palette in ``oklch()``, which no
email client supports, so these are hex copies converted at authoring time.

**They will not follow globals.css.** A build step that regenerated them is more
machinery than nine colours justify; the oklch source sits in the comment beside
each value, and ``tests/test_mail_palette.py`` re-runs the conversion so a typo
is caught. Drift from the app's real palette is not caught by anything — if you
change a token there, change it here.
"""

from __future__ import annotations

LIGHT: dict[str, str] = {
    "background": "#f0f9fb",  # oklch(0.975 0.01 220)
    "card": "#ffffff",  # oklch(1 0 0)
    "foreground": "#1b1630",  # oklch(0.22 0.05 290)
    "primary": "#007789",  # oklch(0.50 0.14 205)
    "primary_foreground": "#f4fefe",  # oklch(0.99 0.01 200)
    "muted_foreground": "#4d5e7a",  # oklch(0.48 0.05 260)
    "border": "#b4d9e0",  # oklch(0.86 0.04 210)
    "destructive": "#d7002d",  # oklch(0.54 0.24 20)
    "success": "#00791b",  # oklch(0.50 0.16 145)
}

DARK: dict[str, str] = {
    "background": "#0a0917",  # oklch(0.15 0.03 285)
    "card": "#121123",  # oklch(0.19 0.035 285)
    "foreground": "#e0f3f4",  # oklch(0.95 0.02 200)
    "primary": "#00edee",  # oklch(0.85 0.16 195)
    "primary_foreground": "#0a0917",  # oklch(0.15 0.03 285)
    "muted_foreground": "#89abb4",  # oklch(0.72 0.04 215)
    "border": "#2b2a46",  # oklch(0.30 0.05 285)
    "destructive": "#ff426d",  # oklch(0.70 0.24 15)
    "success": "#8aec41",  # oklch(0.85 0.22 135)
}

__all__ = ["DARK", "LIGHT"]
