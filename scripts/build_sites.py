"""Wind and solar farm locations from OpenStreetMap, clustered and weighted by capacity.

    python scripts/build_sites.py turkey-latest.osm.pbf   # needs osmium

Plants tagged ``power=plant`` with ``plant:source=wind|solar`` give locations and, where tagged,
capacity; single turbines (``generator:source=wind``) fill in farms mapped only as turbines.
Sites are merged into ~12 clusters per technology so a weather request stays small.
Writes src/puant/data/sites.json.
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import osmium

OUT = Path(__file__).resolve().parents[1] / "src" / "puant" / "data" / "sites.json"
BOX = (25.5, 35.7, 45.0, 42.3)  # Turkey


def mw(tag: str | None) -> float | None:
    """'50 MW', '3.6 MW', '1500 kW', '12 MWp' -> MW."""
    if not tag:
        return None
    m = re.match(r"\s*([\d.,]+)\s*(k|M|G)?W", tag.replace(",", "."))
    if not m:
        return None
    v = float(m.group(1))
    return v * {"k": 1e-3, "M": 1.0, "G": 1e3, None: 1e-6}[m.group(2)]


def collect(pbf: str):
    sites = {"wind": [], "solar": []}
    turbines = []
    fp = osmium.FileProcessor(pbf).with_locations().with_filter(osmium.filter.KeyFilter("power"))
    for o in fp:
        t = o.tags
        src = t.get("plant:source") or t.get("generator:source")
        if src not in ("wind", "solar"):
            continue
        if o.is_node():
            lon, lat = o.location.lon, o.location.lat
        elif o.is_way():
            try:
                pts = [(n.lon, n.lat) for n in o.nodes]
            except osmium.InvalidLocationError:
                continue
            lon, lat = float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))
        else:
            continue
        if not (BOX[0] <= lon <= BOX[2] and BOX[1] <= lat <= BOX[3]):
            continue
        if t.get("power") == "plant":
            cap = mw(t.get("plant:output:electricity"))
            sites[src].append((lon, lat, cap or (30.0 if src == "wind" else 5.0), cap is not None))
        elif t.get("power") == "generator" and src == "wind":
            turbines.append((lon, lat, mw(t.get("generator:output:electricity")) or 3.0))
    return sites, turbines


def cluster(
    points: np.ndarray, weights: np.ndarray, k: int, iters: int = 30, seed: int = 0
) -> list[dict]:
    """Weighted k-means on lon/lat; returns cluster centres with total capacity."""
    rng = np.random.default_rng(seed)
    k = min(k, len(points))
    c = points[rng.choice(len(points), k, replace=False, p=weights / weights.sum())]
    for _ in range(iters):
        lab = np.argmin(((points[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        for j in range(k):
            m = lab == j
            if m.any():
                c[j] = np.average(points[m], axis=0, weights=weights[m])
    lab = np.argmin(((points[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
    return [
        {
            "lon": round(float(c[j, 0]), 3),
            "lat": round(float(c[j, 1]), 3),
            "mw": round(float(weights[lab == j].sum()), 1),
        }
        for j in range(k)
        if (lab == j).any()
    ]


def main(pbf: str):
    sites, turbines = collect(pbf)
    out = {"source": "OpenStreetMap contributors (ODbL)", "note": "capacity-weighted clusters"}
    for src in ("wind", "solar"):
        pts = [(x, y, w) for x, y, w, _ in sites[src]]
        if src == "wind":
            # turbines more than ~3 km from any mapped wind plant are farms mapped only as turbines
            plants = np.array([(x, y) for x, y, _ in pts]) if pts else np.zeros((0, 2))
            for x, y, w in turbines:
                if len(plants) == 0 or np.min(np.hypot(plants[:, 0] - x, plants[:, 1] - y)) > 0.03:
                    pts.append((x, y, w))
        a = np.array(pts)
        out[src] = cluster(a[:, :2].copy(), a[:, 2], 12)
        tagged = sum(1 for s in sites[src] if s[3])
        print(
            f"{src}: {len(sites[src])} plants ({tagged} with capacity), "
            f"{len(turbines) if src == 'wind' else 0} turbines, "
            f"{a[:, 2].sum():,.0f} MW in {len(out[src])} clusters"
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
