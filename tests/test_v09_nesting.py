"""v0.9: true-shape nesting, inspector data, laser presets, nest DXF export."""
import io
import time
import zipfile

import ezdxf
import pytest
from fastapi.testclient import TestClient
from shapely.affinity import rotate, translate
from shapely.geometry import Point, Polygon, box

from app import app
from engine import analyze_dxf, build_contours, collect_sources, group_duplicates, part_detail
from laser_presets import speed_m_min
from nest_shape import NestCancelled, nest


def placed(shape, pl):
    return translate(rotate(shape, pl.angle, origin=(0, 0)), pl.tx, pl.ty)


def assert_layout_valid(items, res, W, H, margin, gap):
    shapes = {k: s for k, s, _ in items}
    counts = {}
    for sheet in res['sheets']:
        geoms = []
        for pl in sheet.placed:
            g = placed(shapes[pl.key], pl)
            # the transform reported to the UI/DXF must reproduce the geometry
            assert g.symmetric_difference(pl.geom).area < 1e-3 * max(1, g.area)
            b = g.bounds
            assert b[0] >= margin - 1e-6 and b[1] >= margin - 1e-6
            assert b[2] <= W - margin + 1e-6 and b[3] <= H - margin + 1e-6
            geoms.append(g)
            counts[pl.key] = counts.get(pl.key, 0) + 1
        for i, a in enumerate(geoms):
            for b in geoms[i + 1:]:
                assert a.distance(b) >= gap - 1e-6
    assert counts == {k: q for k, _, q in items}


def L(w, h, t):
    return Polygon([(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)])


def test_true_shape_layout_is_valid_and_beats_bounding_boxes():
    tri = Polygon([(0, 0), (400, 0), (0, 250)])
    items = [('L', L(400, 300, 60), 12), ('T', tri, 12)]
    res = nest(items, 1000, 1000, 10, 5, rotation='90', quality='fast')
    assert_layout_valid(items, res, 1000, 1000, 10, 5)
    # bounding boxes alone: 24 boxes ~0.11 m² each ≈ 2.7 m² -> at least 3 sheets
    assert len(res['sheets']) <= 2


def test_free_angles_and_part_in_part():
    ring = Point(0, 0).buffer(150, quad_segs=32).difference(Point(0, 0).buffer(100, quad_segs=32))
    disc = Point(0, 0).buffer(40, quad_segs=24)
    items = [('R', ring, 4), ('D', disc, 4)]
    res = nest(items, 700, 700, 5, 4, rotation='15', quality='fast', part_in_part=True)
    assert_layout_valid(items, res, 700, 700, 5, 4)
    assert len(res['sheets']) == 1
    sheet = res['sheets'][0]
    rings = [Polygon(p.geom.exterior) for p in sheet.placed if p.key == 'R']
    inside = sum(1 for p in sheet.placed if p.key == 'D' and any(r.contains(p.geom) for r in rings))
    assert inside >= 1


def test_no_rotation_keeps_zero_angle():
    items = [('L', L(300, 200, 40), 6)]
    res = nest(items, 1000, 1000, 10, 5, rotation='none', quality='fast')
    assert {p.angle for s in res['sheets'] for p in s.placed} == {0}


def test_oversized_part_reported():
    res = nest([('big', box(0, 0, 2000, 100), 1)], 1000, 1000, 10, 5, rotation='90', quality='fast')
    assert res == {'oversized': ['big']}


def test_nesting_cancel():
    items = [(str(i), box(0, 0, 30 + i, 20 + i), 200) for i in range(4)]
    t = time.time()
    with pytest.raises(NestCancelled):
        nest(items, 1500, 3000, 10, 5, rotation='15', quality='fast', should_stop=lambda: time.time() - t > 0.2)
    assert time.time() - t < 3


def test_part_detail_hole_diameters_and_snaps():
    d = ezdxf.new('R2010'); d.header['$INSUNITS'] = 4; m = d.modelspace()
    m.add_lwpolyline([(0, 0), (200, 0), (200, 100), (0, 100)], close=True)
    m.add_circle((40, 50), 6.5)                         # native CIRCLE -> exact Ø13
    m.add_lwpolyline([(100, 40, 0), (140, 40, 1), (140, 60, 0), (100, 60, 1)], format='xyb', close=True)  # slot 60 x 20
    s = io.StringIO(); d.write(s)
    r = analyze_dxf(s.getvalue().encode(), 'h.dxf', 0.05)
    det = part_detail(r.parts[0])
    assert det['width'] == 200 and det['height'] == 100
    holes = sorted(det['holes'], key=lambda h: h['center'][0])
    assert holes[0]['type'] == 'circle' and holes[0]['exact'] and abs(holes[0]['diameter'] - 13) < 1e-6
    assert holes[1]['type'] == 'slot' and abs(holes[1]['width'] - 20) < 0.2 and abs(holes[1]['length'] - 60) < 0.2
    kinds = {sn['kind'] for sn in det['snaps']}
    assert {'merkez', 'delik merkezi', 'köşe'} & kinds


def test_laser_speed_table_interpolates_and_limits():
    assert speed_m_min('steel', 3, 2) == 6.0
    v = speed_m_min('steel', 3, 7)       # between 6 (2.7) and 8 (2.1)
    assert 2.1 < v < 2.7
    assert speed_m_min('steel', 1, 20) is None    # 1 kW cannot cut 20 mm
    assert speed_m_min('stainless', 12, 1) > speed_m_min('stainless', 3, 1)


def test_flange_bolt_pattern_grouped_not_mirrored():
    from demo import showcase_file
    name, blob = showcase_file()
    r = analyze_dxf(blob, name, 0.05)
    group_duplicates(r.parts, 0.05)
    flanges = [p for p in r.parts if len(p.shape.interiors) == 7]
    assert len(flanges) == 4
    assert len({p.duplicate_group for p in flanges}) == 1
    assert not any(p.mirror_note for p in r.parts)


def test_api_nesting_dxf_export_and_inspector():
    c = TestClient(app)
    d = c.get('/api/demo?full=1').json()
    body = {'sheetWidth': 1500, 'sheetHeight': 3000, 'gap': 5, 'margin': 10, 'defaultThickness': 3,
            'rotation': '45', 'quality': 'fast', 'laserKw': 3, 'material': 'steel',
            'items': [{'id': p['id'], 'quantity': 2} for p in d['parts'] if p['duplicateKind'] in (None, 'Referans parça')]}
    task = c.post(f"/api/nesting-jobs/{d['jobId']}", json=body).json()['taskId']
    deadline = time.time() + 120
    while time.time() < deadline:
        s = c.get(f'/api/nesting-jobs/{task}').json()
        if s['stage'] in ('complete', 'error'):
            break
        time.sleep(0.1)
    assert s['stage'] == 'complete', s
    g = s['result']['groups'][0]
    assert g['speedSource'] == '3 kW tablo' and g['cuttingSpeed'] == 4250
    assert g['recommendedSheets'] <= g['rectSheets']
    z = c.get(f'/api/nesting-jobs/{task}/dxf')
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zz:
        names = zz.namelist()
        assert len(names) == g['recommendedSheets']
        doc = ezdxf.read(io.StringIO(zz.read(names[0]).decode()))
        ents = [e for e in doc.modelspace() if e.dxf.layer == 'PARCA']
        sources, *_ = collect_sources(ents, 0.05)
        shapes, residuals = build_contours([x.line for x in sources], 0.05)
        assert len(shapes) == len(g['sheets'][0]['placements']) and not residuals
        for i, a in enumerate(shapes):
            assert a.bounds[0] >= 9.9 and a.bounds[2] <= 1490.1
            for b in shapes[i + 1:]:
                assert a.distance(b) >= 4.97   # gap minus arc re-tessellation noise
    pid = d['parts'][0]['id']
    det = c.get(f"/api/part/{d['jobId']}/{pid}").json()
    assert det['outline'] and det['snaps']
    assert c.get('/api/laser-presets').json()['powers'][0] == 1


@pytest.mark.parametrize('angle', [0, 30, 45, 90, 135, 200, 315])
def test_rotated_flange_has_no_phantom_island(angle):
    """A hole ring must never come back as a separate 'island' part."""
    import math
    from ezdxf.math import Matrix44
    import ezdxf.transform
    d = ezdxf.new('R2010'); d.header['$INSUNITS'] = 4; m = d.modelspace()
    m.add_arc((0, 0), 190, 0, 180); m.add_arc((0, 0), 190, 180, 360); m.add_circle((0, 0), 110)
    for k in range(6):
        a = math.radians(30 + 60 * k); m.add_circle((150 * math.cos(a), 150 * math.sin(a)), 9)
    ezdxf.transform.inplace(list(m), Matrix44.chain(Matrix44.z_rotate(math.radians(angle)), Matrix44.translate(1159.5, 200, 0)))
    s = io.StringIO(); d.write(s)
    r = analyze_dxf(s.getvalue().encode(), 'f.dxf', 0.05)
    assert len(r.parts) == 1 and len(r.parts[0].shape.interiors) == 7
