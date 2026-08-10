"""Render a self-contained coverage badge SVG.

Deliberately not shields.io: an external badge service would make the README
depend on a third party staying up, and would report whatever a CI provider last
told it rather than what this repository actually measures. This reads the real
coverage.json produced by the test run.

Usage:  python docs/evidence/make_badge.py coverage.json docs/assets/coverage.svg
"""

from __future__ import annotations

import json
import pathlib
import sys

# Same thresholds a reader expects from the usual badge colours.
COLOURS = ((95, "#4c1"), (90, "#97ca00"), (75, "#dfb317"), (0, "#e05d44"))

LABEL = "coverage"
CHAR_WIDTH = 6.6  # Verdana 11px, close enough for a badge.
PADDING = 10


def colour_for(percent: float) -> str:
    return next(colour for threshold, colour in COLOURS if percent >= threshold)


def render(percent: float) -> str:
    value = f"{percent:.0f}%"
    label_width = round(len(LABEL) * CHAR_WIDTH) + PADDING * 2
    value_width = round(len(value) * CHAR_WIDTH) + PADDING * 2
    total = label_width + value_width

    # Text is drawn at 10x and scaled down, which is how shields keeps glyph
    # positioning crisp at this size.
    label_mid = label_width * 5
    value_mid = (label_width + value_width / 2) * 10

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20"
     role="img" aria-label="{LABEL}: {value}">
  <title>{LABEL}: {value}</title>
  <linearGradient id="smooth" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="round"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#round)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{value_width}" height="20" fill="{colour_for(percent)}"/>
    <rect width="{total}" height="20" fill="url(#smooth)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-size="110"
     font-family="Verdana,Geneva,DejaVu Sans,sans-serif">
    <text x="{label_mid}" y="150" fill="#010101" fill-opacity=".3"
          transform="scale(.1)">{LABEL}</text>
    <text x="{label_mid}" y="140" transform="scale(.1)">{LABEL}</text>
    <text x="{value_mid}" y="150" fill="#010101" fill-opacity=".3"
          transform="scale(.1)">{value}</text>
    <text x="{value_mid}" y="140" transform="scale(.1)">{value}</text>
  </g>
</svg>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2

    report = json.loads(pathlib.Path(sys.argv[1]).read_text())
    percent = report["totals"]["percent_covered"]

    destination = pathlib.Path(sys.argv[2])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(percent))

    print(f"{destination}: {percent:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
