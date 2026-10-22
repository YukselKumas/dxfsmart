"""sparrow multi-sheet optimiser (bundled binary) – skipped when not built."""
import collections
import tempfile

import pytest
from shapely.geometry import Polygon, box

import sparrow_nest
from sparrow_nest import SparrowRun, SparrowSheetNester

pytestmark = pytest.mark.skipif(not sparrow_nest.available(), reason="sparrow binary not built")


def test_barriers_keep_parts_inside_their_sheet():
    run = SparrowRun(tempfile.mkdtemp())
    rect = box(0, 0, 100, 50)
    length, placed = run.solve([("R", rect, 7)], 52, 10, "none", 4, "t", sheet_length=322)
    per = collections.Counter(p[4] for p in placed)
    assert len(placed) == 7 and max(per.values()) <= 3          # 3 x 100 + 2 x 10 = 320 <= 322
    for key, a, tx, ty, k in placed:
        assert -0.05 <= tx and tx + 100 <= 322 + 0.05
        assert -0.05 <= ty and ty + 50 <= 52 + 0.05
    assert len(per) == 3 and length <= 100 + 1


def test_sheet_nester_places_everything_and_verifies():
    L = Polygon([(0, 0), (300, 0), (300, 60), (60, 60), (60, 200), (0, 200)])
    flange = Polygon(box(0, 0, 160, 160).exterior, [box(40, 40, 120, 120).exterior.coords])
    washer = box(0, 0, 50, 50)
    items = [("L", L, 12), ("F", flange, 4), ("W", washer, 8)]
    res = SparrowSheetNester(items, 1000, 600, 10, 5, rotation="90", quality="fast").run()
    placed = collections.Counter(p.key for s in res["sheets"] for p in s.placed)
    assert placed == {"L": 12, "F": 4, "W": 8}
    assert "F" in res["kits"]       # washers went into the flange holes
