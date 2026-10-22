"""DXF Akıllı Ayırıcı: conservative 2D DXF analysis and part extraction.

IMPORTANT: This is a pre-production prototype. Every export must be checked in CAM.
Source CAD entities are preserved for extracted parts (not exported as sampled polygons).
"""
from __future__ import annotations

import hashlib
import csv
import io
import math
import os
import re
import tempfile
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ezdxf
import ezdxf.transform
from ezdxf import path as ezpath
from ezdxf.entities import Line as DxfLine, LWPolyline as DxfLWPolyline
from ezdxf.math import Matrix44
from shapely import set_precision
from shapely.affinity import rotate, scale, translate
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
from shapely.ops import polygonize_full, unary_union

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_TOTAL_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_DXF_FILES = 80
MAX_ENTITIES_PER_FILE = 30000
SUPPORTED = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "INSERT"}


class ProcessingError(Exception):
    pass


@dataclass
class SourceGeometry:
    entity: Any
    line: LineString
    label: str
    fingerprint: str


@dataclass
class Part:
    id: str
    filename: str
    index: int
    shape: Polygon
    entity_refs: list
    units: int = 4
    duplicate_group: int | None = None
    duplicate_kind: str | None = None
    needs_review: bool = False
    quantity: int = 1
    thickness_mm: float | None = None
    from_package: bool = False
    export_mode: str = "native"
    mirror_note: str | None = None

    def to_dict(self) -> dict:
        minx, miny, maxx, maxy = self.shape.bounds
        exterior = simplify_preview(list(self.shape.exterior.coords))
        holes = [simplify_preview(list(r.coords)) for r in self.shape.interiors]
        return {
            "id": self.id,
            "file": self.filename,
            "number": self.index,
            "name": f"Parça {self.index:03d}",
            "width": round(maxx - minx, 2),
            "height": round(maxy - miny, 2),
            "area": round(self.shape.area, 2),
            "cutLength": round(self.shape.boundary.length, 2),
            "holes": len(self.shape.interiors),
            "bounds": [round(v, 3) for v in self.shape.bounds],
            "center": [round(self.shape.centroid.x, 3), round(self.shape.centroid.y, 3)],
            "outline": exterior,
            "inner": holes,
            "duplicateGroup": self.duplicate_group,
            "duplicateKind": self.duplicate_kind,
            "review": self.needs_review,
            "units": self.units,
            "quantity": self.quantity,
            "thicknessMm": self.thickness_mm,
            "fromPackage": self.from_package,
            "exportMode": self.export_mode,
            "mirrorNote": self.mirror_note,
        }


@dataclass
class FileResult:
    filename: str
    parts: list[Part] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    entities: int = 0
    unit: int = 4
    orphan_count: int = 0

    def to_dict(self):
        return {
            "name": self.filename,
            "parts": len(self.parts),
            "entities": self.entities,
            "issues": self.issues,
            "orphanCount": self.orphan_count,
            "units": self.unit,
        }


def issue(file: str, severity: str, code: str, message: str, count: int | None = None) -> dict:
    result = {"file": file, "severity": severity, "code": code, "message": message}
    if count is not None:
        result["count"] = count
    return result


def round_xy(pt, digits=4):
    return (round(float(pt[0]), digits), round(float(pt[1]), digits))


def simplify_preview(coords, max_points=420):
    if len(coords) <= max_points:
        return [[round(x, 3), round(y, 3)] for x, y in coords]
    gap = max(1, math.ceil((len(coords) - 1) / (max_points - 1)))
    pts = coords[::gap]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    return [[round(x, 3), round(y, 3)] for x, y in pts]


def sample_entity(entity, tol: float):
    """Flatten an entity to WCS XY points.

    v0.8: ezdxf.path is used for every curve type. It applies the OCS/extrusion
    transform, so arcs/circles/polylines with extrusion (0,0,-1) - common in
    SolidWorks/Inventor flat-pattern exports and in mirrored blocks - land at
    their true position instead of being mirrored around the Y axis.
    """
    kind = entity.dxftype()
    if kind == "LINE":
        return [(entity.dxf.start.x, entity.dxf.start.y),
                (entity.dxf.end.x, entity.dxf.end.y)]
    if kind == "POLYLINE" and not entity.is_2d_polyline:
        raise ValueError("3B POLYLINE: yalnızca 2B kontur destekleniyor")
    if kind in ("ARC", "CIRCLE") and entity.dxf.radius <= 0:
        raise ValueError("Geçersiz yarıçap")
    if kind not in ("LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"):
        raise ValueError(f"Desteklenmeyen geometri: {kind}")
    extrusion = entity.dxf.get("extrusion", None)
    if extrusion is not None and abs(abs(float(extrusion[2])) - 1.0) > 1e-6:
        raise ValueError("eğik düzlemde çizilmiş (extrusion Z ekseninde değil)")
    p = ezpath.make_path(entity)
    if len(p) == 0 and kind not in ("CIRCLE",):
        return []
    points = list(p.flattening(max(0.005, tol / 2), segments=16))
    coords = [(float(v.x), float(v.y)) for v in points]
    cleaned = coords[:1]
    for pt in coords[1:]:
        if math.dist(pt, cleaned[-1]) > 1e-9:
            cleaned.append(pt)
    if kind == "CIRCLE" and cleaned and cleaned[0] != cleaned[-1]:
        cleaned.append(cleaned[0])
    return cleaned


def entity_fingerprint(coords):
    rounded = [round_xy(p, 3) for p in coords]
    forward = tuple(rounded)
    backward = tuple(reversed(rounded))
    # Full-entity duplicate checks; NOTE: different tessellations may evade this check.
    return hashlib.sha1(repr(min(forward, backward)).encode()).hexdigest()


def name_clean(original: str):
    text = os.path.basename(original.replace("\\", "/"))
    return re.sub(r"[^\w.()\- ]", "_", text, flags=re.UNICODE)[:100] or "dosya.dxf"


def collect_sources(msp, tol: float):
    sources = []
    skipped = defaultdict(int)
    invalid_self_intersections = 0
    zero_lines = 0
    def append_entity(ent):
        nonlocal invalid_self_intersections, zero_lines
        kind = ent.dxftype()
        if kind == "INSERT":
            try:
                for child in ent.virtual_entities():
                    append_entity(child)
            except Exception:
                skipped["INSERT: çözümlenemedi"] += 1
            return
        if kind not in SUPPORTED:
            skipped[kind] += 1
            return
        try:
            coords = sample_entity(ent, tol)
            if len(coords) < 2:
                zero_lines += 1
                return
            line = LineString(coords)
            if line.length < 1e-7:
                zero_lines += 1
                return
            if not line.is_simple:
                # Self-intersecting entities cannot be allocated safely to distinct
                # contours: exporting their full CAD object into each polygon would
                # silently produce invalid manufacturing files.
                invalid_self_intersections += 1
                return
            sources.append(SourceGeometry(ent, line, kind, entity_fingerprint(coords)))
        except Exception as exc:
            skipped[f"{kind}: {str(exc)[:55]}"] += 1

    for i, ent in enumerate(msp):
        if i >= MAX_ENTITIES_PER_FILE:
            raise ProcessingError(f"Bir DXF en fazla {MAX_ENTITIES_PER_FILE:,} ana nesne içerebilir.")
        append_entity(ent)
    return sources, skipped, invalid_self_intersections, zero_lines


def build_contours(lines, tol):
    if not lines:
        return [], []
    try:
        # Coordinate snap reduces floating-point inaccuracies at shared endpoints.
        # This does NOT repair arbitrary gaps or guarantee manufacturing validity.
        snapped = [set_precision(line, max(tol / 2, 0.0001)) for line in lines]
        union = unary_union(snapped)
        polygons, cuts, dangles, invalid = polygonize_full(union)
    except Exception as exc:
        raise ProcessingError(f"Kontur analizi başarısız: {str(exc)[:160]}") from exc
    ring_shapes = []
    # v0.9: duplicate rings (a hole ring is both a face interior and a face
    # exterior) are matched by a spatial grid + tolerance instead of a rounded
    # key. Rounding put near-identical rings in different buckets (e.g. after
    # rotating a drawing), which turned a hole into a phantom "island" part.
    cell = max(tol * 4, 0.01)
    seen = defaultdict(list)
    for poly in polygons.geoms:
        for ring in [poly.exterior, *poly.interiors]:
            try:
                candidate = Polygon(ring)
                if candidate.area < max(tol * tol * 0.1, 1e-6) or not candidate.is_valid:
                    continue
                bx0, by0, bx1, by1 = candidate.bounds
                gx, gy = int(round((bx0 + bx1) / 2 / cell)), int(round((by0 + by1) / 2 / cell))
                limit = max(candidate.length * tol * 0.5, 1e-6)
                duplicate = False
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for existing in seen[(gx + dx, gy + dy)]:
                            if abs(existing.area - candidate.area) <= limit and existing.symmetric_difference(candidate).area <= limit:
                                duplicate = True
                                break
                        if duplicate:
                            break
                    if duplicate:
                        break
                if duplicate:
                    continue
                seen[(gx, gy)].append(candidate)
                ring_shapes.append(candidate)
            except Exception:
                continue
    ring_shapes.sort(key=lambda x: x.area, reverse=True)
    parent = [None] * len(ring_shapes)
    depth = [0] * len(ring_shapes)
    for index, geom in enumerate(ring_shapes):
        center = geom.representative_point()
        for parent_idx in range(index - 1, -1, -1):
            outer = ring_shapes[parent_idx]
            if outer.area <= geom.area:
                continue
            if outer.covers(center) and outer.buffer(tol).covers(geom):
                parent[index] = parent_idx
                depth[index] = depth[parent_idx] + 1
                break
    grouped = []
    for idx, geom in enumerate(ring_shapes):
        if depth[idx] % 2 != 0:
            continue
        holes = [list(ring_shapes[n].exterior.coords)
                 for n in range(len(ring_shapes)) if parent[n] == idx and depth[n] % 2 == 1]
        group_poly = Polygon(list(geom.exterior.coords), holes)
        if group_poly.area > 0 and group_poly.is_valid:
            grouped.append(group_poly)
    residuals = list(cuts.geoms) + list(dangles.geoms) + list(invalid.geoms)
    return grouped, residuals


def _snap_to_vertices(point, vertices_tree, vertices, limit):
    if vertices_tree is None:
        return point
    idx = vertices_tree.nearest(Point(point))
    if idx is None:
        return point
    candidate = vertices[int(idx)]
    return candidate if math.dist(candidate, point) <= limit else point


def _rings(poly: Polygon):
    return [LineString(poly.exterior.coords)] + [LineString(h.coords) for h in poly.interiors]


def _covered_ratio(poly: Polygon, lines, width):
    if not lines:
        return 0.0
    cover = unary_union([ln.buffer(width) for ln in lines])
    total = sum(r.length for r in _rings(poly))
    inside = sum(r.intersection(cover).length for r in _rings(poly))
    return inside / total if total else 0.0


def _rebuilds_shape(poly: Polygon, lines, tol):
    """True when the selected entities, on their own, form exactly this part."""
    try:
        shapes, _ = build_contours(lines, tol)
    except ProcessingError:
        return False
    if not shapes:
        return False
    rebuilt = unary_union(shapes)
    allowed = max(poly.length * tol * 1.5, poly.area * 1e-4)
    return rebuilt.symmetric_difference(poly).area <= allowed


def _sampled_entities(poly: Polygon, layer: str = "0"):
    """Last-resort export: closed LWPOLYLINEs from the analysed contour."""
    output = []
    for ring in [poly.exterior, *poly.interiors]:
        pts = list(ring.coords)
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        e = DxfLWPolyline.new(dxfattribs={"layer": layer})
        e.set_points([(x, y) for x, y in pts], format="xy")
        e.closed = True
        output.append(e)
    return output


def source_index(sources):
    return STRtree([s.line for s in sources]) if sources else None


def resolve_part_entities(poly: Polygon, sources, tol, tree=None):
    """Select CAD entities for one part and prove that they close the part.

    Returns (entities, mode). mode is
      "native"  - original CAD entities, unchanged
      "trimmed" - original entities plus LINEs trimmed to a shared edge
      "sampled" - closed LWPOLYLINE(s) taken from the analysed contour
      "empty"   - nothing could be matched
    """
    width = max(tol * 1.6, 0.001)
    rings = _rings(poly)
    buffers = [r.buffer(width) for r in rings]
    zone = unary_union(buffers)
    if tree is None:
        tree = source_index(sources)
    if tree is None:
        return [], "empty"
    candidates = [sources[int(i)] for i in tree.query(poly.envelope.buffer(width * 2))]
    full, partial = [], []
    for src in candidates:
        try:
            if src.line.length <= 0:
                continue
            best = max(src.line.intersection(buf).length for buf in buffers)
            ratio = best / src.line.length
            if ratio >= 0.94:
                full.append(src)
            elif ratio > 0.0 and best > tol * 4:
                partial.append(src)
        except Exception:
            continue
    if not full and not partial:
        return [], "empty"
    entities = [s.entity for s in full]
    lines = [s.line for s in full]
    if _covered_ratio(poly, lines, width) >= 0.995 and _rebuilds_shape(poly, lines, tol):
        return entities, "native"
    # Shared / over-long LINEs: export only the portion that lies on this part.
    vertices = [pt for r in rings for pt in r.coords]
    vtree = STRtree([Point(v) for v in vertices]) if vertices else None
    trimmed_any = False
    for src in partial:
        if src.label != "LINE":
            continue
        piece = src.line.intersection(zone)
        pieces = [piece] if piece.geom_type == "LineString" else [g for g in getattr(piece, "geoms", []) if g.geom_type == "LineString"]
        for seg in pieces:
            # Ignore stubs where a crossing line (e.g. a bend line) merely passes
            # through the boundary band; real shared edges run along it.
            if seg.length <= max(tol * 10, width * 6):
                continue
            a = _snap_to_vertices(seg.coords[0], vtree, vertices, width * 2.5)
            b = _snap_to_vertices(seg.coords[-1], vtree, vertices, width * 2.5)
            if math.dist(a, b) <= tol:
                continue
            attribs = {"start": (a[0], a[1], 0), "end": (b[0], b[1], 0)}
            for key in ("layer", "color", "linetype"):
                if src.entity.dxf.hasattr(key):
                    attribs[key] = src.entity.dxf.get(key)
            entities.append(DxfLine.new(dxfattribs=attribs))
            lines.append(LineString([a, b]))
            trimmed_any = True
    if trimmed_any and _covered_ratio(poly, lines, width) >= 0.995 and _rebuilds_shape(poly, lines, tol):
        return entities, "trimmed"
    layer = full[0].entity.dxf.get("layer", "0") if full else "0"
    return _sampled_entities(poly, layer), "sampled"


def matching_source_entities(poly: Polygon, sources, tol):
    """Backward compatible helper: entities only."""
    return resolve_part_entities(poly, sources, tol)[0]


def analyze_dxf(data: bytes, filename: str, tol: float):
    filename = name_clean(filename)
    with tempfile.TemporaryDirectory() as temp:
        location = Path(temp) / "drawing.dxf"
        location.write_bytes(data)
        recovered = False
        try:
            doc = ezdxf.readfile(str(location))
        except Exception:
            # Many CAM/CAD exports are slightly malformed; ezdxf.recover repairs
            # common structural faults instead of rejecting the whole file.
            try:
                from ezdxf import recover
                doc, auditor = recover.readfile(str(location))
                if auditor.has_errors:
                    raise ProcessingError(f"'{filename}' onarılamayacak kadar bozuk.")
                recovered = True
            except ProcessingError:
                raise
            except Exception as exc:
                raise ProcessingError(f"'{filename}' okunamadı: {str(exc)[:160]}") from exc
    file_result = FileResult(filename=filename)
    if recovered:
        file_result.issues.append(issue(filename, "warning", "RECOVERED", "DXF yapısında hata vardı; dosya otomatik onarılarak okundu. Sonuçları kaynak çizimle karşılaştırın."))
    units = int(doc.header.get("$INSUNITS", 0))
    file_result.unit = units
    if units not in (4,):
        unit_labels = {0: "tanımsız", 1: "inç", 5: "cm", 6: "metre"}
        file_result.issues.append(issue(filename, "warning", "UNITS", f"Birim {unit_labels.get(units, str(units))}; ölçüler DXF koordinatlarıdır, mm olduğu varsayılmamalı."))
    msp = doc.modelspace()
    file_result.entities = len(msp)
    from package_drawing import drawing_parts
    package = drawing_parts(msp, tol, collect_sources, build_contours, resolve_part_entities, source_index)
    if package is not None:
        chosen = package["chosen"]
        for index, (shape, refs, mode, qty, thick) in enumerate(chosen, 1):
            file_result.parts.append(Part(id=str(uuid.uuid4()), filename=filename, index=index,
                shape=shape, entity_refs=refs, units=units, quantity=qty, thickness_mm=thick,
                needs_review=True, from_package=True, export_mode=mode))
        file_result.issues.append(issue(filename, 'warning', 'PACKAGE_DRAWING',
            f'Çerçeveli teknik resim: {package["labels"]} adet etiketi, {len(chosen)} ana görünüm adayı. Yan görünümler ve büküm çizgileri kesimden çıkarıldı. Her adayın açınım olduğunu doğrulayın.'))
        if package["unresolved"]:
            n = len(package["unresolved"])
            file_result.issues.append(issue(filename, 'danger', 'UNRESOLVED_LABELS',
                f'{n} adet etiketi kapalı geometriyle eşleştirilemedi; eksik parçaları kontrol edin.', n))
        missing = sum(p.thickness_mm is None for p in file_result.parts)
        if missing:
            file_result.issues.append(issue(filename, 'warning', 'THICKNESS_UNKNOWN',
                f'{missing} adayın kalınlığı eksik veya çelişkili; otomatik kalınlık atanmadı.', missing))
        skipped, self_crosses, zero_lines = package["diagnostics"]
        _geometry_issues(file_result, skipped, self_crosses, zero_lines)
        _export_issues(file_result)
        return file_result
    sources, skipped, self_crosses, zero_lines = collect_sources(msp, tol)
    _geometry_issues(file_result, skipped, self_crosses, zero_lines)
    fingerprints = defaultdict(list)
    for src in sources:
        fingerprints[src.fingerprint].append(src)
    repeats = sum(len(v) - 1 for v in fingerprints.values() if len(v) > 1)
    if repeats:
        file_result.issues.append(issue(filename, "warning", "OVERLAPPING", f"{repeats} birebir üst üste binmiş çizim nesnesi bulundu.", repeats))
    contours, residuals = build_contours([src.line for src in sources], tol)
    if residuals:
        file_result.issues.append(issue(filename, "danger", "OPEN", f"{len(residuals)} açık / kapalı parçaya bağlanmamış çizgi grubu bulundu.", len(residuals)))
    file_result.orphan_count = len(residuals)
    tree = source_index(sources)
    for index, contour in enumerate(sorted(contours, key=lambda p: (p.bounds[1], p.bounds[0])), 1):
        selected, mode = resolve_part_entities(contour, sources, tol, tree)
        if not selected:
            file_result.issues.append(issue(filename, "danger", "EMPTY", f"{index}. konturun kaynak DXF nesneleri güvenle eşleştirilemedi; dışa aktarıma alınmadı."))
            continue
        file_result.parts.append(Part(id=str(uuid.uuid4()), filename=filename, index=index,
                                      shape=contour, entity_refs=selected, units=units, export_mode=mode))
    if not file_result.parts:
        file_result.issues.append(issue(filename, "danger", "NO_PARTS", "Kapalı parça bulunamadı; DXF içeriğini kontrol edin."))
    _export_issues(file_result)
    file_danger = any(x["severity"] == "danger" for x in file_result.issues)
    for part in file_result.parts:
        # v0.8: never clear a part-level review flag set earlier.
        part.needs_review = part.needs_review or file_danger or part.export_mode == "sampled"
    return file_result


def _geometry_issues(file_result, skipped, self_crosses, zero_lines):
    name = file_result.filename
    for key, count in skipped.items():
        file_result.issues.append(issue(name, "warning", "UNSUPPORTED", f"Atlanan nesne: {key} ({count} adet). Kontrol edin.", count))
    if self_crosses:
        file_result.issues.append(issue(name, "danger", "SELF_INTERSECT", f"{self_crosses} geometride kendi üzerine kesişme tespit edildi.", self_crosses))
    if zero_lines:
        file_result.issues.append(issue(name, "warning", "ZERO_LENGTH", f"{zero_lines} sıfır uzunluklu / geçersiz nesne var.", zero_lines))


def _export_issues(file_result):
    name = file_result.filename
    trimmed = [p.index for p in file_result.parts if p.export_mode == "trimmed"]
    sampled = [p.index for p in file_result.parts if p.export_mode == "sampled"]
    def labels(indexes):
        return ", ".join(f"P{i:03d}" for i in indexes[:8]) + (" …" if len(indexes) > 8 else "")
    if trimmed:
        file_result.issues.append(issue(name, "warning", "SHARED_EDGE",
            f"{len(trimmed)} parçada başka parçayla ortak çizgi vardı; çizgi bu parçaya ait kısmından kırpılarak aktarıldı ({labels(trimmed)}).", len(trimmed)))
    if sampled:
        file_result.issues.append(issue(name, "danger", "SAMPLED_EXPORT",
            f"{len(sampled)} parçanın orijinal CAD nesneleri kapalı kontur oluşturmadı; parça analiz konturundan polyline olarak aktarıldı, yaylar segmentlidir ({labels(sampled)}).", len(sampled)))


def _rectangle_angle(poly):
    rectangle = poly.minimum_rotated_rectangle
    edges = list(rectangle.exterior.coords)
    lines = [(math.dist(edges[i], edges[i+1]), edges[i], edges[i+1]) for i in range(4)]
    _, a, b = max(lines, key=lambda item: item[0])
    return math.degrees(math.atan2(b[1]-a[1], b[0]-a[0]))


def equal_shape(a: Polygon, b: Polygon, tol: float):
    if len(a.interiors) != len(b.interiors):
        return None
    area_tol = max(tol * tol * 2, max(a.area,b.area) * 0.001)
    perim_tol = max(tol * 3, max(a.length,b.length) * 0.001)
    if abs(a.area-b.area) > area_tol or abs(a.length-b.length) > perim_tol:
        return None
    # Normalize translation and principal orientation, then try quarter turns and mirror.
    ac, bc = a.centroid, b.centroid
    aa = rotate(translate(a, xoff=-ac.x, yoff=-ac.y), -_rectangle_angle(a), origin=(0,0))
    bb = rotate(translate(b, xoff=-bc.x, yoff=-bc.y), -_rectangle_angle(b), origin=(0,0))
    deviation = max(tol * 1.5, 0.025)
    rect = a.minimum_rotated_rectangle
    e = list(rect.exterior.coords)
    s1, s2 = math.dist(e[0], e[1]), math.dist(e[1], e[2])
    roundish = min(s1, s2) > 0 and max(s1, s2) / min(s1, s2) <= 1.05
    a0 = translate(a, xoff=-ac.x, yoff=-ac.y)
    b0 = translate(b, xoff=-bc.x, yoff=-bc.y)

    def features(g):
        pts = [Polygon(h).centroid for h in g.interiors]
        pts = [(p.x, p.y) for p in pts] or list(g.exterior.coords)[:-1]
        if not pts:
            return 0.0, []
        far = max(math.hypot(x, y) for x, y in pts)
        return far, [math.degrees(math.atan2(y, x)) for x, y in pts if abs(math.hypot(x, y) - far) <= max(tol * 3, far * 0.002)]

    # Exact (non-mirrored) candidates are always tried before mirrored ones, so a
    # symmetric part is never reported as its own mirror image.
    for is_mirrored in (False, True):
        label = "Aynı geometri (ayna)" if is_mirrored else "Aynı geometri"
        test = scale(bb, xfact=-1, yfact=1, origin=(0,0)) if is_mirrored else bb
        for angle in (0, 90, 180, 270):
            if aa.hausdorff_distance(rotate(test, angle, origin=(0,0))) <= deviation:
                return label
        if roundish:
            # Round / square-ish parts (flanges, bolt circles) have no stable
            # bounding-box orientation: align by the farthest feature instead.
            ra, angs_a = features(a0)
            t0 = scale(b0, xfact=-1, yfact=1, origin=(0,0)) if is_mirrored else b0
            rb, angs_b = features(t0)
            if angs_a and abs(ra - rb) <= max(tol * 3, ra * 0.002):
                for ang in angs_b[:72]:
                    # area-based: robust to different arc tessellation phases
                    if a0.symmetric_difference(rotate(t0, angs_a[0] - ang, origin=(0,0))).area <= a.length * deviation:
                        return label
    return None


def group_duplicates(parts: list[Part], tol: float):
    """Group identical parts. Mirrored parts are NOT merged (v0.8).

    A mirrored (left/right hand) part may only be cut by flipping the blank,
    which is wrong for bends, foil-coated or brushed sheet and engravings.
    They stay separate groups and receive a mirror note instead.
    """
    next_group = 1
    for i, source in enumerate(parts):
        if source.duplicate_group is not None:
            continue
        matches = []
        for target in parts[i+1:]:
            if source.thickness_mm != target.thickness_mm:
                continue
            if target.duplicate_group is not None:
                continue
            if equal_shape(source.shape, target.shape, tol) == "Aynı geometri":
                matches.append(target)
        if not matches:
            continue
        source.duplicate_group = next_group
        source.duplicate_kind = "Referans parça"
        for found in matches:
            found.duplicate_group = next_group
            found.duplicate_kind = "Aynı geometri"
        next_group += 1
    # Mirror detection between distinct classes (one representative each).
    classes = defaultdict(list)
    for p in parts:
        classes[("G", p.duplicate_group) if p.duplicate_group else ("P", p.id)].append(p)
    reps = [members[0] for members in classes.values()]
    keys = list(classes.keys())
    for a in range(len(reps)):
        for b in range(a + 1, len(reps)):
            ra, rb = reps[a], reps[b]
            if ra.thickness_mm != rb.thickness_mm:
                continue
            if equal_shape(ra.shape, rb.shape, tol) == "Aynı geometri (ayna)":
                for x, other in ((keys[a], rb), (keys[b], ra)):
                    note = f"{other.filename} / Parça {other.index:03d} ile ayna simetrik; ayrı parça olarak tutuldu"
                    for member in classes[x]:
                        member.mirror_note = member.mirror_note or note
    return next_group - 1


def make_response(results: list[FileResult], tol: float, job_id: str):
    all_parts = [p for file_result in results for p in file_result.parts]
    groups = group_duplicates(all_parts, tol)
    all_issues = [i for f in results for i in f.issues]
    kinds = defaultdict(int)
    for i in all_issues:
        kinds[i["severity"]] += 1
    return {
        "jobId": job_id,
        "files": [f.to_dict() for f in results],
        "parts": [p.to_dict() for p in all_parts],
        "issues": all_issues,
        "summary": {"files": len(results), "parts": len(all_parts), "duplicateGroups": groups,
                    "duplicateParts": sum(bool(p.duplicate_group) for p in all_parts),
                    "mirrorParts": sum(bool(p.mirror_note) for p in all_parts),
                    "warnings": kinds["danger"] + kinds["warning"], "critical": kinds["danger"]},
        "tolerance": tol,
        "disclaimer": "Operatör kontrolü zorunlu. Otomatik analiz/ayırma üretim onayı değildir.",
    }


def part_to_bytes(part: Part, move_to_origin: bool = True) -> bytes:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = part.units
    msp = doc.modelspace()
    copied = []
    for entity in part.entity_refs:
        try:
            added = entity.copy()
            msp.add_foreign_entity(added, copy=False)
            copied.append(added)
        except Exception as exc:
            raise ProcessingError(f"{part.filename} parça {part.index}: CAD nesnesi aktarılamadı: {exc}") from exc
    if move_to_origin:
        minx, miny, _, _ = part.shape.bounds
        # Preserve native entities (ARC/CIRCLE/bulge/SPLINE), never export tessellated shape.
        try:
            ezdxf.transform.inplace(copied, Matrix44.translate(-minx, -miny, 0))
        except Exception as exc:
            raise ProcessingError(f"Parçayı sıfıra taşıma başarısız: {exc}") from exc
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "part.dxf"
        doc.saveas(target)
        return target.read_bytes()


def pack_parts(parts: list[Part], move_to_origin=True) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        used = defaultdict(int)
        rows = [["Dosya", "Kaynak", "Adet", "Kalinlik_mm", "Kontrol"]]
        for part in parts:
            name = part_filename(part)
            used[name] += 1
            if used[name] > 1:
                name = f"{Path(name).stem}_{used[name]}.dxf"
            output.writestr(name, part_to_bytes(part, move_to_origin))
            checks = ['Ana gorunum adayi - acinimi dogrulayin' if part.from_package else 'Geometri kontrolu']
            if part.export_mode == 'trimmed':
                checks.append('ortak kenar kirpildi')
            if part.export_mode == 'sampled':
                checks.append('polyline olarak aktarildi')
            if part.mirror_note:
                checks.append('ayna esi var')
            thick = '' if part.thickness_mm is None else f'{part.thickness_mm:g}'.replace('.', ',')
            rows.append([name, part.filename, part.quantity, thick, ' / '.join(checks)])
        if parts:
            report=io.StringIO(newline='')
            csv.writer(report,delimiter=';').writerows(rows)
            output.writestr('Parca_Listesi.csv','\ufeff'+report.getvalue())
            notes = ['Bu dosyalar DXFsmart Studio ile ayrilmistir. Kesimden once CAD/CAM uzerinde kontrol edin.',
                     'Adet ve kalinliklar Parca_Listesi.csv icindedir. Bos kalinlik bilinmiyor demektir.',
                     'Her parca bir kez kaydedilir; uretim adedi kadar kopyalanmaz.']
            if any(p.from_package for p in parts):
                notes.insert(1, 'Cerceveli teknik resimden secilen parcalar ana gorunum ADAYLARIDIR; kesim acinimi oldugunu dogrulayin.')
            if any(p.export_mode == 'sampled' for p in parts):
                notes.insert(1, 'Kontrol sutununda "polyline" yazan parcalarda yaylar segmentlere bolunmustur.')
            output.writestr('ONCE_OKUYUN.txt', '\n'.join(notes) + '\n')
    return stream.getvalue()


def part_filename(part):
    thickness=f'{part.thickness_mm:g}mm' if part.thickness_mm is not None else 'kalinlik_belirsiz'
    stem=name_clean(Path(part.filename).stem)[:55]
    return f'{stem}_P{part.index:03d}_{thickness}_{part.quantity}adet.dxf'


def extract_uploads(upload_pairs: list[tuple[str,bytes]]):
    """Return safe DXF byte/name pairs; never write untrusted ZIP paths."""
    if sum(len(blob) for _, blob in upload_pairs) > MAX_UPLOAD_BYTES:
        raise ProcessingError("Toplam yükleme boyutu 40 MB sınırını aşıyor.")
    output = []
    unpacked_bytes = 0
    for filename, data in upload_pairs:
        ext = Path(filename).suffix.lower()
        if ext == ".dxf":
            unpacked_bytes += len(data)
            output.append((name_clean(filename), data))
        elif ext == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir() or Path(entry.filename).suffix.lower() != ".dxf":
                            continue
                        if entry.flag_bits & 1:
                            raise ProcessingError("Parolalı ZIP desteklenmiyor.")
                        if entry.file_size > MAX_UPLOAD_BYTES or entry.file_size < 1:
                            raise ProcessingError("ZIP içinde aşırı büyük veya boş DXF bulundu.")
                        unpacked_bytes += entry.file_size
                        if unpacked_bytes > MAX_TOTAL_UNPACKED_BYTES:
                            raise ProcessingError("ZIP açılmış boyutu 100 MB sınırını aşıyor.")
                        if len(output) >= MAX_DXF_FILES:
                            raise ProcessingError("En fazla 80 DXF işlenebilir.")
                        raw = archive.read(entry)
                        output.append((name_clean(entry.filename), raw))
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise ProcessingError("ZIP arşivi okunamadı veya bozuk.") from exc
        else:
            raise ProcessingError("Yalnızca .dxf ve .zip uzantılı dosyalar kabul edilir.")
        if len(output) > MAX_DXF_FILES or unpacked_bytes > MAX_TOTAL_UNPACKED_BYTES:
            raise ProcessingError("İşlem sınırı: 80 DXF ve toplam 100 MB açılmış veri.")
    if not output:
        raise ProcessingError("İşlenebilir DXF bulunamadı. ZIP içinde .dxf olması gerekir.")
    # Duplicate filenames are made distinct for UI/export traceability.
    counts = defaultdict(int)
    unique = []
    for name, blob in output:
        counts[name] += 1
        if counts[name] > 1:
            unique_name = f"{Path(name).stem} ({counts[name]}){Path(name).suffix}"
        else:
            unique_name = name
        unique.append((unique_name, blob))
    return unique


# --------------------------------------------------------------------------
# v0.9: part inspection (ölçüm / delik çapları)
# --------------------------------------------------------------------------
def _circle_fit(coords):
    """Algebraic least-squares circle fit -> (cx, cy, r, max_radial_error)."""
    import numpy as np
    pts = np.asarray(coords[:-1] if len(coords) > 3 and coords[0] == coords[-1] else coords, dtype=float)
    if len(pts) < 3:
        return None
    x, y = pts[:, 0], pts[:, 1]
    A = np.column_stack([x, y, np.ones_like(x)])
    b = x * x + y * y
    try:
        c, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    cx, cy = c[0] / 2, c[1] / 2
    r = math.sqrt(max(c[2] + cx * cx + cy * cy, 0))
    err = float(np.max(np.abs(np.hypot(x - cx, y - cy) - r))) if r > 0 else float("inf")
    return cx, cy, r, err


def _entity_snaps_and_circles(entities):
    snaps, circles = [], []

    def add(kind, v):
        snaps.append({"x": round(float(v[0]), 4), "y": round(float(v[1]), 4), "kind": kind})
    for e in entities:
        kind = e.dxftype()
        try:
            if kind == "LINE":
                a, b = e.dxf.start, e.dxf.end
                add("uç", a); add("uç", b); add("orta", ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
            elif kind in ("CIRCLE", "ARC"):
                ocs = e.ocs()
                c = ocs.to_wcs(e.dxf.center)
                r = float(e.dxf.radius)
                add("merkez", c)
                if kind == "CIRCLE":
                    circles.append((float(c[0]), float(c[1]), r))
                    for ang in (0, 90, 180, 270):
                        add("çeyrek", (c[0] + r * math.cos(math.radians(ang)), c[1] + r * math.sin(math.radians(ang))))
                else:
                    for p in e.vertices([e.dxf.start_angle, e.dxf.end_angle]):
                        add("uç", p)
            elif kind in ("LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
                p = ezpath.make_path(e)
                add("uç", p.start)
                for cmd in p.commands():
                    add("köşe", cmd.end)
        except Exception:
            continue
    # de-duplicate snap points (same place, keep first kind)
    seen, unique = set(), []
    for s in snaps:
        key = (round(s["x"], 3), round(s["y"], 3))
        if key not in seen:
            seen.add(key); unique.append(s)
    return unique[:6000], circles


def part_detail(part: "Part", tol: float = 0.05) -> dict:
    """Full-resolution geometry and measurements for the inspector."""
    shape = part.shape
    minx, miny, maxx, maxy = shape.bounds
    mrr = shape.minimum_rotated_rectangle
    edges = list(mrr.exterior.coords)
    sides = sorted([math.dist(edges[i], edges[i + 1]) for i in range(2)])
    snaps, circles = _entity_snaps_and_circles(part.entity_refs)
    holes = []
    for idx, ring in enumerate(shape.interiors, 1):
        hp = Polygon(ring)
        coords = list(ring.coords)
        info = {"index": idx, "area": round(hp.area, 3), "perimeter": round(ring.length, 3),
                "bounds": [round(v, 3) for v in hp.bounds], "center": [round(hp.centroid.x, 3), round(hp.centroid.y, 3)]}
        native = [c for c in circles if hp.buffer(tol * 4).contains(Point(c[0], c[1]))
                  and abs(math.pi * c[2] ** 2 - hp.area) <= max(hp.area * 0.02, tol * ring.length * 2)]
        fit = _circle_fit(coords)
        if native:
            cx, cy, r = native[0]
            info.update(type="circle", diameter=round(2 * r, 3), center=[round(cx, 3), round(cy, 3)], source="CAD dairesi", exact=True)
        elif fit and fit[2] > 0 and fit[3] <= max(tol * 3, fit[2] * 0.01):
            cx, cy, r, _ = fit
            info.update(type="circle", diameter=round(2 * r, 3), center=[round(cx, 3), round(cy, 3)], source="kontur uydurma", exact=False)
        else:
            hr = hp.minimum_rotated_rectangle
            he = list(hr.exterior.coords)
            hs = sorted([math.dist(he[i], he[i + 1]) for i in range(2)])
            slot_area = (hs[1] - hs[0]) * hs[0] + math.pi * (hs[0] / 2) ** 2
            if hs[1] > hs[0] * 1.15 and abs(slot_area - hp.area) <= hp.area * 0.03:
                info.update(type="slot", width=round(hs[0], 3), length=round(hs[1], 3), source="kontur")
            elif abs(hs[0] * hs[1] - hp.area) <= hp.area * 0.01:
                info.update(type="rect", width=round(hs[0], 3), length=round(hs[1], 3), source="kontur")
            else:
                info.update(type="free", width=round(hs[0], 3), length=round(hs[1], 3), source="kontur")
        holes.append(info)
        snaps.append({"x": info["center"][0], "y": info["center"][1], "kind": "delik merkezi"})
    return {
        "id": part.id, "name": f"Parça {part.index:03d}", "file": part.filename,
        "units": part.units, "thicknessMm": part.thickness_mm, "quantity": part.quantity,
        "bounds": [round(v, 4) for v in (minx, miny, maxx, maxy)],
        "width": round(maxx - minx, 3), "height": round(maxy - miny, 3),
        "minRect": {"length": round(sides[1], 3), "width": round(sides[0], 3)},
        "area": round(shape.area, 3), "outerPerimeter": round(shape.exterior.length, 3),
        "cutLength": round(shape.boundary.length, 3),
        "outline": [[round(x, 4), round(y, 4)] for x, y in shape.exterior.coords],
        "inner": [[[round(x, 4), round(y, 4)] for x, y in r.coords] for r in shape.interiors],
        "holes": holes, "snaps": snaps,
        "exportMode": part.export_mode, "mirrorNote": part.mirror_note, "fromPackage": part.from_package,
    }


def nest_to_dxf(layout_sheets, parts_by_id, width, height, margin, thickness, units=4) -> bytes:
    """One DXF per sheet with native part entities placed (rotation + translation)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n, sheet in enumerate(layout_sheets, 1):
            doc = ezdxf.new("R2010")
            doc.header["$INSUNITS"] = units
            for name, color in (("SAC", 8), ("KENAR_PAYI", 9), ("PARCA", 7)):
                if name not in doc.layers:
                    doc.layers.add(name, color=color)
            msp = doc.modelspace()
            msp.add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)], close=True, dxfattribs={"layer": "SAC"})
            if margin > 0:
                msp.add_lwpolyline([(margin, margin), (width - margin, margin), (width - margin, height - margin), (margin, height - margin)],
                                   close=True, dxfattribs={"layer": "KENAR_PAYI", "linetype": "CONTINUOUS"})
            for pl in sheet["placements"]:
                part = parts_by_id[pl["id"]]
                m = Matrix44.chain(Matrix44.z_rotate(math.radians(pl["angle"])), Matrix44.translate(pl["tx"], pl["ty"], 0))
                copies = []
                for entity in part.entity_refs:
                    c = entity.copy()
                    c.dxf.layer = "PARCA"
                    msp.add_foreign_entity(c, copy=False)
                    copies.append(c)
                ezdxf.transform.inplace(copies, m)
            stream = io.StringIO()
            doc.write(stream)
            z.writestr(f"Plaka_{thickness:g}mm_{n:02d}.dxf", stream.getvalue().encode("utf-8"))
    return out.getvalue()
