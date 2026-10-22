"""Regression tests for the silent-wrong-output bugs fixed in v0.8."""
import io
import time
import threading
import zipfile

import ezdxf
from ezdxf.math import OCS
from fastapi.testclient import TestClient

from app import app
from engine import analyze_dxf, build_contours, collect_sources, group_duplicates, pack_parts, part_to_bytes
from estimate import Cancelled, estimate_sheets


def to_bytes(doc):
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode()


def new_doc():
    d = ezdxf.new('R2010')
    d.header['$INSUNITS'] = 4
    return d


def exported_shapes(part):
    """Re-read an exported part DXF and rebuild its closed contours."""
    doc = ezdxf.read(io.StringIO(part_to_bytes(part, move_to_origin=False).decode()))
    sources, *_ = collect_sources(doc.modelspace(), 0.05)
    shapes, residuals = build_contours([s.line for s in sources], 0.05)
    return shapes, residuals


def assert_export_closes(part):
    shapes, residuals = exported_shapes(part)
    assert len(shapes) == 1, f'beklenen tek kapalı parça, bulunan {len(shapes)}'
    assert not residuals
    assert abs(shapes[0].area - part.shape.area) <= part.shape.area * 1e-3
    assert len(shapes[0].interiors) == len(part.shape.interiors)


def test_negative_extrusion_circle_stays_a_hole():
    d = new_doc(); m = d.modelspace()
    m.add_lwpolyline([(0, 0), (200, 0), (200, 100), (0, 100)], close=True)
    extr = (0, 0, -1)
    m.add_circle(OCS(extr).from_wcs((50, 50, 0)), 10, dxfattribs={'extrusion': extr})
    r = analyze_dxf(to_bytes(d), 'ocs.dxf', 0.05)
    assert len(r.parts) == 1
    assert len(r.parts[0].shape.interiors) == 1
    assert r.parts[0].export_mode == 'native'
    assert_export_closes(r.parts[0])


def test_negative_extrusion_arc_corner_closes():
    d = new_doc(); m = d.modelspace()
    m.add_line((0, 0), (90, 0)); m.add_line((100, 10), (100, 50))
    m.add_line((100, 50), (0, 50)); m.add_line((0, 50), (0, 0))
    m.add_arc(OCS((0, 0, -1)).from_wcs((90, 10, 0)), 10, 180, 270, dxfattribs={'extrusion': (0, 0, -1)})
    r = analyze_dxf(to_bytes(d), 'arc.dxf', 0.05)
    assert len(r.parts) == 1 and not any(i['code'] == 'OPEN' for i in r.issues)
    assert_export_closes(r.parts[0])


def test_mirrored_block_with_arc():
    d = new_doc(); m = d.modelspace()
    b = d.blocks.new('P')
    b.add_line((0, 0), (90, 0)); b.add_line((100, 10), (100, 50))
    b.add_line((100, 50), (0, 50)); b.add_line((0, 50), (0, 0)); b.add_arc((90, 10), 10, 270, 360)
    m.add_blockref('P', (0, 0))
    m.add_blockref('P', (400, 0), dxfattribs={'xscale': -1})
    r = analyze_dxf(to_bytes(d), 'mirror_block.dxf', 0.05)
    assert len(r.parts) == 2
    for p in r.parts:
        assert_export_closes(p)


def test_shared_edge_is_trimmed_and_exports_closed():
    d = new_doc(); m = d.modelspace()
    m.add_line((0, 0), (200, 0)); m.add_line((200, 0), (200, 100))
    m.add_line((200, 100), (0, 100)); m.add_line((0, 100), (0, 0))
    m.add_line((100, 0), (100, 100))
    r = analyze_dxf(to_bytes(d), 'shared.dxf', 0.05)
    assert len(r.parts) == 2
    assert {p.export_mode for p in r.parts} == {'trimmed'}
    assert any(i['code'] == 'SHARED_EDGE' for i in r.issues)
    for p in r.parts:
        assert_export_closes(p)
        out = ezdxf.read(io.StringIO(part_to_bytes(p).decode()))
        assert {e.dxftype() for e in out.modelspace()} == {'LINE'}


def test_shared_edge_with_arc_falls_back_to_closed_polyline():
    # Common edge made of one ARC that spans both parts cannot be trimmed as a
    # LINE: the exporter must still produce a closed contour and flag it.
    d = new_doc(); m = d.modelspace()
    m.add_arc((100, 0), 100, 0, 180)                 # half circle top
    m.add_line((0, 0), (200, 0))                     # diameter
    m.add_line((100, 0), (100, 100))                 # splits into two quarter discs
    r = analyze_dxf(to_bytes(d), 'arc_shared.dxf', 0.05)
    assert len(r.parts) == 2
    for p in r.parts:
        assert p.export_mode in ('trimmed', 'sampled')
        assert_export_closes(p)
    assert all(p.needs_review for p in r.parts if p.export_mode == 'sampled')


def test_mirror_parts_are_not_merged():
    d = new_doc(); m = d.modelspace()
    L = [(0, 0), (100, 0), (100, 20), (20, 20), (20, 60), (0, 60)]
    m.add_lwpolyline(L, close=True)
    m.add_lwpolyline([(300 - x, y) for x, y in L], close=True)
    m.add_lwpolyline([(x + 500, y) for x, y in L], close=True)   # true duplicate
    r = analyze_dxf(to_bytes(d), 'hand.dxf', 0.05)
    group_duplicates(r.parts, 0.05)
    by_x = sorted(r.parts, key=lambda p: p.shape.bounds[0])
    left, mirrored, copy = by_x
    assert left.duplicate_group is not None and left.duplicate_group == copy.duplicate_group
    assert mirrored.duplicate_group is None
    assert mirrored.mirror_note and left.mirror_note


def test_symmetric_part_still_groups():
    d = new_doc(); m = d.modelspace()
    for ox in (0, 300):
        m.add_lwpolyline([(ox, 0), (ox + 100, 0), (ox + 100, 50), (ox, 50)], close=True)
    r = analyze_dxf(to_bytes(d), 'sym.dxf', 0.05)
    group_duplicates(r.parts, 0.05)
    assert r.parts[0].duplicate_group == r.parts[1].duplicate_group == 1
    assert not any(p.mirror_note for p in r.parts)


def test_package_readme_only_mentions_candidates_for_package_parts():
    d = new_doc(); d.modelspace().add_circle((0, 0), 20)
    r = analyze_dxf(to_bytes(d), 'c.dxf', 0.05)
    with zipfile.ZipFile(io.BytesIO(pack_parts(r.parts))) as z:
        text = z.read('ONCE_OKUYUN.txt').decode()
    assert 'ADAYLARIDIR' not in text


def test_nesting_can_be_cancelled_quickly():
    from shapely.geometry import box
    from engine import Part
    parts = {str(i): Part(str(i), 't.dxf', i, box(0, 0, 20 + i, 15 + i), [], units=4) for i in range(6)}
    req = {'sheetWidth': 1500, 'sheetHeight': 3000, 'gap': 5, 'margin': 10,
           'items': [{'id': k, 'quantity': 400} for k in parts]}
    started = time.time()
    try:
        estimate_sheets(parts, req, should_stop=lambda: time.time() - started > 0.05)
        assert False, 'iptal edilmedi'
    except Cancelled:
        pass
    assert time.time() - started < 1.5


def test_new_nesting_request_cancels_previous():
    client = TestClient(app)
    d = client.get('/api/demo').json()
    body = {'sheetWidth': 1500, 'sheetHeight': 3000, 'gap': 5, 'margin': 10, 'confirmMillimeters': True,
            'defaultThickness': 2, 'items': [{'id': p['id'], 'quantity': 400} for p in d['parts']]}
    first = client.post(f"/api/nesting-jobs/{d['jobId']}", json=body).json()['taskId']
    second = client.post(f"/api/nesting-jobs/{d['jobId']}", json=body).json()['taskId']
    deadline = time.time() + 60
    states = {}
    while time.time() < deadline:
        states = {t: client.get(f'/api/nesting-jobs/{t}').json()['stage'] for t in (first, second)}
        if states[second] in ('complete', 'error'):
            break
        time.sleep(0.1)
    assert states[first] in ('cancelled', 'complete')
    assert states[second] == 'complete'


def test_cross_site_post_is_rejected():
    client = TestClient(app)
    r = client.post('/api/analyze', files={'files': ('a.dxf', b'0\nEOF\n')}, headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403
    ok = client.post('/api/analyze', files={'files': ('a.dxf', b'0\nEOF\n')}, headers={'Origin': 'http://127.0.0.1:8000'})
    assert ok.status_code != 403
