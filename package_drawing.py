"""Conservative recognition of framed, annotated fabrication drawings."""
import re
from collections import defaultdict
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

GEOMETRY_TYPES = ('INSERT', 'LINE', 'POLYLINE', 'LWPOLYLINE', 'CIRCLE', 'ARC', 'ELLIPSE', 'SPLINE')


def _text_point(e):
    """Visual anchor of TEXT/MTEXT; aligned TEXT uses align_point."""
    if e.dxftype() == 'TEXT':
        anchor = e.dxf.insert
        if (e.dxf.get('halign', 0) or e.dxf.get('valign', 0)) and e.dxf.hasattr('align_point'):
            anchor = e.dxf.align_point
        return Point(anchor.x, anchor.y + e.dxf.get('height', 1) * .5)
    return Point(e.dxf.insert.x, e.dxf.insert.y + e.dxf.get('char_height', 1) * .5)


def drawing_parts(msp, tol, collect, contours, resolve, make_index):
    """Return None for ordinary DXFs, else a dict with chosen candidates.

    chosen: list of (shape, entities, export_mode, quantity, thickness_mm)
    """
    texts = []
    broken_blocks = 0

    def visit(e, depth=0):
        nonlocal broken_blocks
        if depth > 12:
            return
        kind = e.dxftype()
        if kind == 'INSERT':
            try:
                children = list(e.virtual_entities())
            except Exception:
                # v0.8: an unresolvable block no longer aborts the whole analysis.
                broken_blocks += 1
                return
            fragments = [c for c in children if c.dxftype() == 'TEXT']
            if len(fragments) == 2:
                fragments.sort(key=lambda c: c.dxf.insert.x)
                joined = ' '.join(c.dxf.text.strip() for c in fragments).upper()
                if re.fullmatch(r'\d+(?:[.,]\d+)?\s+MM', joined):
                    texts.append((joined, Point(fragments[0].dxf.insert.x, fragments[0].dxf.insert.y)))
            for child in children:
                visit(child, depth + 1)
        elif kind in ('TEXT', 'MTEXT'):
            try:
                text = e.dxf.text if kind == 'TEXT' else e.plain_text()
                texts.append((text.upper(), _text_point(e)))
            except Exception:
                pass

    for e in msp:
        visit(e)
    quantities = [(int(m.group(1)), p) for t, p in texts if (m := re.fullmatch(r'\s*(\d+)\s*ADET\s*', t))]
    thicknesses = [(float(m.group(1).replace(',', '.')), p) for t, p in texts if (m := re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*MM\s*', t))]
    if len(quantities) < 3:
        return None
    frames = []
    for e in msp:
        if e.dxftype() not in ('POLYLINE', 'LWPOLYLINE'):
            continue
        ss, *_ = collect([e], tol)
        if len(ss) != 1 or not ss[0].line.is_ring:
            continue
        poly = Polygon(ss[0].line)
        if poly.is_valid and poly.area > 1 and len(poly.exterior.coords) == 5 and abs(poly.area - poly.envelope.area) < poly.area * .03:
            if any(poly.buffer(15).covers(point) for _, point in quantities):
                frames.append((e, poly))
    # Activate only with strong evidence of a tabular fabrication drawing.
    if len(frames) < 3:
        return None
    frame_ids = {e.dxf.handle for e, _ in frames}
    assignments = []
    for qty, point in quantities:
        nearby = [p for _, p in frames if p.covers(point)]
        if not nearby:
            nearby = [p for _, p in frames if p.buffer(15).covers(point)]
        if not nearby:
            continue
        assignments.append((qty, point, min(nearby, key=lambda p: p.area)))
    if len(assignments) < 3:
        return None
    geometry = [ent for ent in msp if ent.dxf.handle not in frame_ids and ent.dxftype() in GEOMETRY_TYPES]
    ss, skipped, self_crosses, zero_lines = collect(geometry, tol)
    if broken_blocks:
        skipped = defaultdict(int, skipped)
        skipped['INSERT: çözümlenemedi'] += broken_blocks
    shapes, _ = contours([s.line for s in ss], tol)
    merged = unary_union(shapes) if shapes else None
    shapes = [] if merged is None else ([merged] if merged.geom_type == 'Polygon' else list(getattr(merged, 'geoms', [])))
    index = make_index(ss)
    candidates = []
    for shape in shapes:
        if shape.area <= tol * tol:
            continue
        refs, mode = resolve(shape, ss, tol, index)
        if refs:
            candidates.append((shape, refs, mode))
    chosen = []
    used = set()
    unresolved = []
    for qty, point, cell in assignments:
        options = [(i, s, refs, mode) for i, (s, refs, mode) in enumerate(candidates)
                   if i not in used and cell.buffer(tol * 2).covers(s)]
        if not options:
            unresolved.append((qty, point.x, point.y))
            continue
        # Main view has substantially more area than edge / section views.
        i, shape, refs, mode = max(options, key=lambda x: x[1].area)
        used.add(i)
        ts = [(v, p) for v, p in thicknesses if cell.buffer(15).covers(p)]
        values = {v for v, p in ts}
        thick = next(iter(values)) if len(values) == 1 else None
        chosen.append((shape, refs, mode, qty, thick))
    return {'chosen': chosen, 'unresolved': unresolved, 'labels': len(assignments),
            'diagnostics': (skipped, self_crosses, zero_lines)}
