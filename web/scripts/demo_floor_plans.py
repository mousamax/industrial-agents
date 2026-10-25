"""Draw MUCL's floors as paper floor-plan sheets for the facility-manager demo's upload view.

The demo only shows these as "photos the facility manager attached"; nothing reads them back.
Run from the repo root: python3 web/scripts/demo_floor_plans.py
"""

import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web/public/demo/mucl-plans"
W, H, PAD = 1200, 520, 40  # sheet in px; the building is 100.7 x 29.8 m


def sheet(title: str, subtitle: str, body: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<rect width="{W}" height="{H}" fill="#fbfaf5"/>
<rect x="14" y="14" width="{W - 28}" height="{H - 28}" fill="none" stroke="#3a3a3a" stroke-width="1.5"/>
{body}
<g font-family="Helvetica, Arial, sans-serif" fill="#2a2a2a">
<rect x="{W - 330}" y="{H - 92}" width="316" height="78" fill="#fbfaf5" stroke="#3a3a3a" stroke-width="1.2"/>
<text x="{W - 318}" y="{H - 66}" font-size="15" font-weight="700">{escape(title)}</text>
<text x="{W - 318}" y="{H - 46}" font-size="12">{escape(subtitle)}</text>
<text x="{W - 318}" y="{H - 28}" font-size="10" fill="#666">Munich Urban Colab · Freddie-Mercury-Str. 5 · M 1:200</text>
</g></svg>"""


def main() -> None:
    b = json.loads((ROOT / "data/MUCL/building.json").read_text())
    xs = [p[0] for s in b["spaces"] for p in s["polygon"]]
    ys = [p[1] for s in b["spaces"] for p in s["polygon"]]
    x0, y0 = min(xs), min(ys)
    k = min((W - 2 * PAD) / (max(xs) - x0), (H - 2 * PAD - 90) / (max(ys) - y0))

    def pt(p: list[float]) -> str:  # plans are y-down
        return f"{PAD + (p[0] - x0) * k:.1f},{PAD + (max(ys) - p[1]) * k:.1f}"

    OUT.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(b["floors"]):
        parts = []
        for s in (s for s in b["spaces"] if s["floor_id"] == f["id"]):
            fill = "#e9e7df" if s["type"] in ("stair", "lift") else "none"
            parts.append(
                f'<polygon points="{" ".join(pt(p) for p in s["polygon"])}" fill="{fill}" '
                f'stroke="#222" stroke-width="2.2" stroke-linejoin="miter"/>'
            )
            if s["type"] == "room" and len(s["name"]) < 28:
                cx = sum(p[0] for p in s["polygon"]) / len(s["polygon"])
                cy = sum(p[1] for p in s["polygon"]) / len(s["polygon"])
                x, y = pt([cx, cy]).split(",")
                parts.append(
                    f'<text x="{x}" y="{y}" font-family="Helvetica, Arial, sans-serif" font-size="8.5" '
                    f'fill="#444" text-anchor="middle">{escape(s["name"])}</text>'
                )
        for o in (o for o in b["openings"] if o["floor_id"] == f["id"] and o["kind"] == "door"):
            x, y = pt(o["position"]).split(",")
            parts.append(f'<circle cx="{x}" cy="{y}" r="2.6" fill="#fbfaf5" stroke="#222" stroke-width="1"/>')
        name = f["name"].split(" (")[0]
        (OUT / f"floor_{i}.svg").write_text(sheet(f"Grundriss {name}", f["name"], "\n".join(parts)))
    print(f"wrote {len(b['floors'])} sheets to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
