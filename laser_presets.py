"""Typical fiber-laser cutting speeds by source power (kW), material and thickness.

Values are mid-points of published reference ranges, in m/min:
  * 1-6 kW: IPG fiber source parameter table (carbon steel O2, stainless N2,
    aluminium N2) as published by MachineMFG. For 2 kW the 100 µm core column
    is used (typical cutting head).
  * 6 kW thick carbon steel (8-25 mm): EMP Laser carbon steel table.
  * 8 / 10 / 12 kW: MachineMFG power chart points at 1, 3, 5, 10 mm,
    other thicknesses log-interpolated between those points.
  * 20 kW: sparse public data (e.g. stainless 16 mm 5-6 m/min) - extrapolated,
    marked as estimate.
With O2, thin carbon steel speed is limited by the oxidation process, not by
power (~8-12 m/min at 1 mm); with air/N2 thin steel can be cut several times
faster. Real average speed on small contours is lower than nominal (machine
acceleration), which the 'Ek süre %' setting accounts for.
Speeds are references only: every user can and should overwrite them with
their own machine's tested values.
"""
from __future__ import annotations

import math

SOURCES = [
    {"title": "MachineMFG - Fiber laser cutting speed (IPG laser source)",
     "url": "https://www.machinemfg.com/fiber-laser-cutting-speed-ipg-laser-source/"},
    {"title": "MachineMFG - Laser cutting thickness & speed chart",
     "url": "https://www.machinemfg.com/laser-cutting-thickness-speed-chart/"},
    {"title": "EMP Laser - Carbon steel cutting parameters",
     "url": "https://emplaser.com/carbon-steel"},
    {"title": "GYC Laser - Fiber laser cutting speed chart",
     "url": "https://gyclaser.com/de/2024/03/07/fiber-laser-cutting-speed-chart/"},
]

POWERS_KW = [1, 1.5, 2, 3, 4, 6, 8, 10, 12, 20]

# material -> gas label, thickness columns, rows by power (None = not cuttable / no data)
TABLE = {
    "steel": {
        "gas": "O₂",
        "thickness": [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 25],
        "rows": {
            1:   [8.0, 5.5, 2.75, 2.2, 1.65, 1.5, 1.1, 0.8, 0.65, None, None, None],
            1.5: [9.0, 5.75, 3.3, 2.85, 2.15, 1.9, 1.3, 1.1, 0.95, None, None, None],
            2:   [9.5, 5.75, 3.8, 3.4, 2.7, 2.4, 1.7, 1.25, 1.05, 0.68, 0.45, None],
            3:   [9.5, 6.0, 4.25, 3.6, 3.05, 2.7, 2.1, 1.5, 1.25, 0.78, 0.68, 0.5],
            4:   [10.0, 6.25, 4.4, 4.0, 3.4, 2.9, 2.25, 1.95, 1.25, 0.78, 0.68, 0.5],
            6:   [10.0, 6.5, 4.8, 4.2, 3.6, 3.0, 2.5, 2.2, 1.9, 1.25, 0.63, 0.53],
            8:   [10.0, 6.5, 5.5, 4.6, 3.9, 3.4, 2.8, 2.45, 2.1, 1.5, 1.0, 0.75],
            10:  [10.0, 6.5, 5.8, 4.7, 3.9, 3.4, 2.8, 2.45, 2.2, 1.7, 1.2, 0.9],
            12:  [10.0, 6.5, 6.2, 4.9, 4.0, 3.5, 2.9, 2.45, 2.3, 1.8, 1.4, 1.0],
            20:  [10.0, 6.5, 6.2, 5.0, 4.2, 3.7, 3.1, 2.6, 2.4, 2.0, 1.6, 1.2],
        },
    },
    "stainless": {
        "gas": "N₂",
        "thickness": [1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20],
        "rows": {
            1:   [16.5, 5.25, 2.2, 1.25, 0.8, None, None, None, None, None, None, None],
            1.5: [18.0, 6.0, 3.5, 2.4, 1.45, 0.85, 0.6, None, None, None, None, None],
            2:   [24.0, 11.0, 5.25, 3.75, 2.15, 1.5, 0.85, 0.55, None, None, None, None],
            3:   [42.5, 19.0, 9.0, 5.0, 3.25, 2.6, 1.5, 0.8, 0.35, None, None, None],
            4:   [50.0, 22.5, 12.5, 6.25, 4.5, 3.5, 2.05, 1.0, 0.5, 0.4, 0.4, None],
            6:   [50.0, 27.5, 17.5, 12.0, 10.0, 7.0, 3.75, 2.0, 1.35, 1.0, 0.7, 0.35],
            8:   [58.0, 38.0, 25.0, 18.0, 14.5, 10.0, 5.5, 3.0, 2.0, 1.5, 1.1, 0.6],
            10:  [67.0, 45.0, 32.0, 23.0, 18.5, 13.0, 7.5, 4.2, 2.8, 2.1, 1.6, 0.9],
            12:  [65.0, 50.0, 38.0, 28.0, 21.5, 16.0, 9.5, 5.5, 3.8, 2.8, 2.2, 1.3],
            20:  [70.0, 55.0, 45.0, 36.0, 30.0, 24.0, 16.0, 11.0, 8.5, 6.8, 5.5, 3.5],
        },
    },
    "aluminum": {
        "gas": "N₂",
        "thickness": [1, 2, 3, 4, 5, 6, 8, 10, 12, 16],
        "rows": {
            1:   [8.0, 3.0, 1.1, None, None, None, None, None, None, None],
            1.5: [12.5, 5.5, 3.0, 1.7, 0.9, 0.55, None, None, None, None],
            2:   [19.0, 8.5, 5.0, 3.0, 1.6, 1.0, 0.7, None, None, None],
            3:   [34.0, 14.0, 7.25, 4.25, 3.0, 2.4, 1.1, 0.55, 0.38, None],
            4:   [37.5, 17.5, 10.0, 4.75, 3.5, 2.85, 1.25, 0.78, 0.5, 0.35],
            6:   [50.0, 25.0, 15.5, 11.0, 6.5, 5.0, 2.5, 1.45, 1.1, 0.75],
            8:   [57.0, 38.0, 25.0, 15.0, 10.5, 8.0, 4.2, 2.5, 1.8, 1.1],
            10:  [67.0, 45.0, 30.0, 21.0, 16.5, 12.0, 6.5, 3.8, 2.6, 1.5],
            12:  [75.0, 52.0, 35.0, 26.0, 20.0, 15.0, 8.5, 5.0, 3.5, 2.0],
            20:  [80.0, 60.0, 45.0, 35.0, 28.0, 22.0, 13.0, 8.0, 5.5, 3.2],
        },
    },
}

ROW_NOTES = {
    1: "IPG tablosu", 1.5: "IPG tablosu", 2: "IPG tablosu (100 µm)", 3: "IPG tablosu",
    4: "IPG tablosu", 6: "IPG + EMP Laser (kalın sac)", 8: "1/3/5/10 mm noktalarından enterpolasyon",
    10: "1/3/5/10 mm noktalarından enterpolasyon", 12: "1/3/5/10 mm noktalarından enterpolasyon",
    20: "Tahmini: kamuya açık veri sınırlı",
}


def speed_m_min(material: str, power_kw: float, thickness_mm: float):
    """Typical speed (m/min) for a thickness, log-interpolated; None if out of range."""
    table = TABLE.get(material)
    if table is None or power_kw not in table["rows"]:
        return None
    th = table["thickness"]
    row = table["rows"][power_kw]
    points = [(t, v) for t, v in zip(th, row) if v]
    if not points or thickness_mm > points[-1][0] + 1e-9:
        return None
    if thickness_mm <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= thickness_mm <= t1:
            f = (math.log(thickness_mm) - math.log(t0)) / (math.log(t1) - math.log(t0))
            return round(math.exp(math.log(v0) + f * (math.log(v1) - math.log(v0))), 3)
    return None


def presets_payload():
    return {"powers": POWERS_KW, "table": TABLE, "notes": {str(k): v for k, v in ROW_NOTES.items()},
            "sources": SOURCES,
            "disclaimer": "Yayınlanmış referans tabloların orta değerleridir. Makine, kafa, gaz basıncı ve kesim kalitesine göre değişir; kendi test değerlerinizle güncelleyin."}
