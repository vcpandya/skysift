"""WCAG contrast check for the dashboard stylesheet: every text and UI colour pair, in both themes.

Reads the colour tokens of `:root` (light) and `:root[data-theme="dark"]` from src/skysift/ui/static/app.css, computes
the WCAG 2 contrast ratio of each pair in PAIRS and lists every pair below its minimum. It also fails when the dark
block that follows the system preference has drifted from the explicit dark block.

    python -m tools.contrast_check      # exit 0 when every pair passes, 1 otherwise
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "src" / "skysift" / "ui" / "static" / "app.css"
TEXT, LARGE = 4.5, 3.0      # AA: body text; large text and UI components
STATUS = ("pass", "fail", "undet", "running")
# (foreground token, background token, minimum ratio). Filled ion and fail buttons and chips carry their label in
# --surface, which is white in the light theme and deep sky in the dark one (white on the dark theme's bright ion
# would read at 1.8:1).
PAIRS: list[tuple[str, str, float]] = [
    ("fg", "bg", TEXT), ("fg", "surface", TEXT), ("fg-2", "surface", TEXT), ("fg-2", "bg", TEXT),
    ("muted", "surface", LARGE), ("ion", "surface", TEXT), ("ion", "bg", TEXT), ("solar", "surface", TEXT),
    *[(s, bg, TEXT) for s in STATUS for bg in ("surface", "bg")],
    *[(c, "raised", TEXT) for c in ("pass", "fail", "undet", "ion", "solar")],      # hover rows, toasts, chip counts
    ("surface", "ion", TEXT), ("surface", "fail", TEXT),
    ("space-fg", "space", TEXT),                    # captions inside the dossier drawings
]
# The planet thumbnail's physical colours (skysift.ui.planet_svg): the four planet discs by equilibrium temperature
# and the four star limbs by effective temperature. They are graphics, so the minimum is the UI one, against the
# drawing's own sky (--space, defined in both themes); on the light theme's white surface they would read at 1.2 to
# 3.5:1, which is why the thumbnail carries that sky.
PLANET_COLOURS = ("#cfe6ff", "#7fb7a3", "#c9a96e", "#e0784a")
STAR_COLOURS = ("#fff3d6", "#ffd9a0", "#ffbe78", "#ff9d5c")
GRAPHICS: list[tuple[str, str, float]] = [(c, "space", LARGE) for c in (*PLANET_COLOURS, *STAR_COLOURS)]
BLOCKS = {"light": r"^:root\s*\{(.*?)\}", "dark": r'^:root\[data-theme="dark"\]\s*\{(.*?)\}'}
SYSTEM_DARK = r'@media \(prefers-color-scheme: dark\)\s*\{\s*:root\[data-theme="system"\]\s*\{(.*?)\}'


def tokens(block: str) -> dict[str, str]:
    """Custom properties of one CSS block, comments removed: {"bg": "#070b14", ...}."""
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    return {name: value.strip() for name, value in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block)}


def luminance(hex_colour: str) -> float:
    """WCAG 2 relative luminance of #rgb or #rrggbb."""
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def check(css: str | None = None) -> list[str]:
    """Every failing pair as a sentence; an empty list means the stylesheet passes."""
    css = CSS.read_text(encoding="utf-8") if css is None else css
    failures = []
    themes = {}
    for theme, pattern in BLOCKS.items():
        m = re.search(pattern, css, flags=re.MULTILINE | re.DOTALL)
        if m is None:
            return [f"{theme}: no token block matching {pattern!r} in the stylesheet"]
        themes[theme] = tokens(m.group(1))
    system = re.search(SYSTEM_DARK, css, flags=re.DOTALL)
    if system is None or tokens(system.group(1)) != themes["dark"]:
        failures.append('dark: the prefers-color-scheme block differs from :root[data-theme="dark"]')
    for theme, t in themes.items():
        for fg, bg, minimum in PAIRS:
            colours = (t.get(fg, ""), t.get(bg, ""))
            if not all(re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", c) for c in colours):
                failures.append(f"{theme}: --{fg} on --{bg} is not a pair of hex colours ({colours[0]!r}, {colours[1]!r})")
                continue
            r = ratio(*colours)
            if r < minimum:
                failures.append(f"{theme}: --{fg} {colours[0]} on --{bg} {colours[1]} is {r:.2f}:1, needs {minimum}:1")
        for colour, bg, minimum in GRAPHICS:
            ground = t.get(bg, "")
            if not re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", ground):
                failures.append(f"{theme}: --{bg} is not a hex colour ({ground!r})")
                continue
            r = ratio(colour, ground)
            if r < minimum:
                failures.append(f"{theme}: thumbnail {colour} on --{bg} {ground} is {r:.2f}:1, needs {minimum}:1")
    return failures


def main() -> int:
    failures = check()
    for line in failures:
        print(line)
    if not failures:
        print(f"contrast ok: {len(PAIRS)} pairs and {len(GRAPHICS)} thumbnail colours in both themes")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
