"""True-shape nesting (gerçek kontur yerleşimi).

How it works
------------
1. Every unique part is rotated to each allowed angle. Congruent rotations
   (e.g. 0° and 180° of a symmetric part) are removed.
2. A *conservative raster* of the sheet is kept. A grid cell is occupied when
   its centre lies within ``gap/2 + cell*sqrt(2)/2`` of a placed part. A part
   kernel uses the same inflation. Proof sketch: if two kernels do not share
   a cell, the true parts are at least ``gap`` apart, so every raster
   candidate is geometrically valid. Holes of placed parts stay free, which
   gives part-in-part (delik içine parça) automatically.
3. Feasible positions for one angle are found for the whole sheet at once with
   an FFT correlation. After a placement only a local window is updated.
4. The best position over all angles is chosen (gravity toward the sheet
   origin along the long axis, leaving one clean remnant strip).
5. The chosen part is then *compacted with exact geometry* (shapely distance
   checks), sliding it until it really touches its neighbours at ``gap``.
   This removes the raster's safety slack.
6. Several part orders / scoring rules are tried within a time budget; the
   plan with fewer sheets and the shortest used length on the last sheet wins.
7. The final layout is verified with exact geometry: inside the usable sheet
   and every pair of parts at least ``gap`` apart.
"""
from __future__ import annotations

import collections
import math
import random
import time
from dataclasses import dataclass, field

import numpy as np
import shapely
from scipy import fft as sfft
from scipy.signal import fftconvolve
from shapely.affinity import rotate, translate
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

SQRT2_2 = math.sqrt(2) / 2

QUALITY = {
    # cells: raster size of one sheet, budget: seconds per thickness group
    # search: share of the budget spent on iterative improvement (local search)
    "fast": {"cells": 150_000, "budget": 6.0, "strategies": 3, "search": 0.0},
    "balanced": {"cells": 320_000, "budget": 35.0, "strategies": 10, "search": 0.3},
    "max": {"cells": 500_000, "budget": 180.0, "strategies": 24, "search": 0.55},
}

ROTATIONS = {
    "none": [0],
    "180": [0, 180],
    "90": [0, 90, 180, 270],
    "45": list(range(0, 360, 45)),
    "15": list(range(0, 360, 15)),
    "5": list(range(0, 360, 5)),
}


class NestCancelled(Exception):
    pass


@dataclass
class Variant:
    angle: float
    shape: Polygon          # rotated, moved so that bounds start at (0, 0)
    off: tuple              # (minx, miny) of the rotated original shape
    width: float
    height: float
    kernel: np.ndarray | None = None   # (rows=y, cols=x) bool
    kernel_f: np.ndarray | None = None
    ring_f: np.ndarray | None = None
    ring: np.ndarray | None = None     # 1-cell ring around the kernel (contact)
    ring_len: float = 0.0


@dataclass
class Unique:
    key: str
    shape: Polygon
    quantity: int
    area: float
    variants: list = field(default_factory=list)
    coarse: list = field(default_factory=list)   # 0/90/180/270 subset


@dataclass
class Placement:
    key: str
    angle: float
    tx: float
    ty: float
    geom: Polygon


class Sheet:
    def __init__(self, ny, nx):
        self.occ = np.zeros((ny, nx), dtype=np.float32)
        # Occupancy with a one-cell wall: sheet edges count as contact.
        self.occ_pad = np.ones((ny + 2, nx + 2), dtype=np.float32)
        self.occ_pad[1:-1, 1:-1] = 0
        self.placed: list[Placement] = []
        self.bounds = np.empty((0, 4))
        self.geoms: list[Polygon] = []
        self.net_area = 0.0

    def add(self, placement: Placement):
        self.placed.append(placement)
        self.geoms.append(placement.geom)
        self.bounds = np.vstack([self.bounds, np.array(placement.geom.bounds)[None, :]])
        self.net_area += placement.geom.area


def _congruent(a: Polygon, b: Polygon, tol: float):
    if abs(a.area - b.area) > max(1e-6, a.area * 1e-4):
        return False
    ab, bb = a.bounds, b.bounds
    if abs((ab[2] - ab[0]) - (bb[2] - bb[0])) > tol or abs((ab[3] - ab[1]) - (bb[3] - bb[1])) > tol:
        return False
    return a.symmetric_difference(b).area <= max(a.area * 2e-4, tol * a.length)


def _variants(shape: Polygon, angles, tol):
    out = []
    for angle in angles:
        g = rotate(shape, angle, origin=(0, 0)) if angle else shape
        minx, miny, maxx, maxy = g.bounds
        g0 = translate(g, -minx, -miny)
        if any(_congruent(g0, v.shape, tol) for v in out):
            continue
        out.append(Variant(angle, g0, (minx, miny), maxx - minx, maxy - miny))
    return out


def _raster(geom, origin_x, origin_y, r, ny, nx):
    """Cells (within an ny x nx window at origin) whose centres are inside geom."""
    xs = origin_x + (np.arange(nx) + 0.5) * r
    ys = origin_y + (np.arange(ny) + 0.5) * r
    gx, gy = np.meshgrid(xs, ys)
    shapely.prepare(geom)
    return shapely.contains_xy(geom, gx.ravel(), gy.ravel()).reshape(ny, nx)


def _solid(g):
    """Geometry with holes filled (part-in-part disabled)."""
    if g.geom_type == "Polygon":
        return Polygon(g.exterior)
    return unary_union([Polygon(p.exterior) for p in g.geoms])


def _bbox_density(g):
    rect = g.minimum_rotated_rectangle
    return g.area / rect.area if rect.area > 0 else 0.0


def make_pair(shape: Polygon, gap: float, safety: float):
    """Best 180° partner of a part (Metalix-style pair / cluster nesting).

    The rotated copy B is placed around A on a small conservative raster;
    the offset giving the smallest common bounding box wins, then B is slid
    toward A with exact geometry until they are exactly ``gap`` apart.
    Returns (MultiPolygon, components) or None when pairing brings no gain.
    """
    A = shape
    B = rotate(shape, 180, origin=(0, 0))
    ax0, ay0, ax1, ay1 = A.bounds
    bx0, by0, bx1, by1 = B.bounds
    w, h = ax1 - ax0, ay1 - ay0
    rc = max(0.5, max(w, h) / 90)
    d = gap / 2 + rc * SQRT2_2 + safety / 2
    # canvas: A at the centre, room for B on every side
    pad = max(w, h) + 2 * d + 2 * rc
    cx0, cy0 = ax0 - pad, ay0 - pad
    nx = int(math.ceil((w + 2 * pad) / rc))
    ny = int(math.ceil((h + 2 * pad) / rc))
    occ = _raster(A.buffer(d, quad_segs=8), cx0, cy0, rc, ny, nx).astype(np.float32)
    Bb = B.buffer(d, quad_segs=8)
    kx0, ky0 = bx0 - d, by0 - d
    kw = int(math.ceil((bx1 - bx0 + 2 * d) / rc))
    kh = int(math.ceil((by1 - by0 + 2 * d) / rc))
    ker = _raster(Bb, kx0, ky0, rc, kh, kw).astype(np.float32)
    feas = fftconvolve(occ, ker[::-1, ::-1], mode="valid") < 0.5
    if not feas.any():
        return None
    ii, jj = np.nonzero(feas)
    # kernel cell (0,0) at canvas cell (i, j) -> B shifted by:
    sx = cx0 + jj * rc - kx0
    sy = cy0 + ii * rc - ky0
    ux0 = np.minimum(ax0, bx0 + sx); ux1 = np.maximum(ax1, bx1 + sx)
    uy0 = np.minimum(ay0, by0 + sy); uy1 = np.maximum(ay1, by1 + sy)
    area = (ux1 - ux0) * (uy1 - uy0)
    k = int(np.argmin(area + 1e-6 * (np.abs(sx) + np.abs(sy))))
    dx, dy = float(sx[k]), float(sy[k])
    Bp = translate(B, dx, dy)
    if A.distance(Bp) < gap + safety:
        return None
    # exact slide toward A, x then y, repeated
    ca = A.centroid
    for _ in range(2):
        for axis in ("x", "y"):
            cb = Bp.centroid
            sign = -1.0 if (cb.x > ca.x if axis == "x" else cb.y > ca.y) else 1.0
            lo, hi = 0.0, rc * 4
            def moved(t):
                return translate(Bp, xoff=sign * t) if axis == "x" else translate(Bp, yoff=sign * t)
            if moved(hi).distance(A) >= gap + safety:
                lo = hi
            else:
                for _ in range(18):
                    mid = (lo + hi) / 2
                    if moved(mid).distance(A) >= gap + safety:
                        lo = mid
                    else:
                        hi = mid
            if lo > 0:
                Bp = moved(lo)
                if axis == "x":
                    dx += sign * lo
                else:
                    dy += sign * lo
    pair = MultiPolygon([A, Bp])
    # Only pairs that become a near-rectangular block are worth it (triangles,
    # gussets, wedges). L/U pairs form hollow frames that pack worse than
    # interlocked singles, even if their bounding-box density looks higher.
    dens = _bbox_density(pair)
    if dens < _bbox_density(A) * 1.10 or dens < 0.80:
        return None
    return pair, [(0.0, 0.0, 0.0), (180.0, dx, dy)]


class Nester:
    def __init__(self, items, sheet_w, sheet_h, margin, gap, rotation="90",
                 part_in_part=True, quality="balanced", tol=0.05,
                 progress=None, should_stop=None, seed=7):
        self.items = items
        self.W, self.H = float(sheet_w), float(sheet_h)
        self.margin = float(margin)
        self.gap = max(float(gap), 0.01)
        self.part_in_part = part_in_part
        self.q = QUALITY.get(quality, QUALITY["balanced"])
        self.angles = ROTATIONS.get(str(rotation), ROTATIONS["90"])
        self.tol = tol
        self.safety = max(0.02, tol)
        self.progress = progress
        self.should_stop = should_stop
        self.rng = random.Random(seed)
        uw, uh = self.W - 2 * self.margin, self.H - 2 * self.margin
        self.usable = (self.margin, self.margin, self.W - self.margin, self.H - self.margin)
        dom_w, dom_h = uw + self.gap, uh + self.gap
        self.r = max(0.5, math.sqrt(dom_w * dom_h / self.q["cells"]))
        self.x0 = self.margin - self.gap / 2
        self.y0 = self.margin - self.gap / 2
        self.nx = int(math.floor(dom_w / self.r))
        self.ny = int(math.floor(dom_h / self.r))
        self.d = self.gap / 2 + self.r * SQRT2_2 + max(0.02, tol) / 2 + 1e-9
        self.long_y = self.H >= self.W   # true long axis of the sheet
        self.y_primary = self.long_y     # fill axis of the current strategy

    # ------------------------------------------------------------- set-up
    def _kernelize(self, u: "Unique"):
        u.variants = _variants(u.shape, self.angles, self.tol)
        for v in u.variants:
            body = v.shape if self.part_in_part else _solid(v.shape)
            buf = body.buffer(self.d, quad_segs=8)
            kw = int(math.ceil((v.width + 2 * self.d) / self.r))
            kh = int(math.ceil((v.height + 2 * self.d) / self.r))
            v.kernel = _raster(buf, -self.d, -self.d, self.r, kh, kw)
            grown = np.zeros((kh + 2, kw + 2), dtype=bool)
            for di in range(3):
                for dj in range(3):
                    grown[di:di + kh, dj:dj + kw] |= v.kernel
            core = np.zeros_like(grown)
            core[1:-1, 1:-1] = v.kernel
            v.ring = (grown & ~core)
            v.ring_len = float(v.ring.sum()) * self.r
            v.kernel_f = v.kernel[::-1, ::-1].astype(np.float32)
            v.ring_f = v.ring[::-1, ::-1].astype(np.float32)
        u.coarse = [v for v in u.variants if abs(v.angle % 90) < 1e-9] or u.variants[:1]

    def _fits(self, u):
        return any(v.kernel.shape[0] <= self.ny and v.kernel.shape[1] <= self.nx for v in u.variants)

    def _prepare(self):
        """Builds two unit sets: single parts, and 180° pairs where they help."""
        self.components = {}
        base = {}
        for key, shape, qty in self.items:
            if qty <= 0:
                continue
            u = Unique(key, shape, qty, shape.area)
            self._kernelize(u)
            base[key] = u
            self.components[key] = [(key, 0.0, 0.0, 0.0)]
        oversized = [u.key for u in base.values() if not self._fits(u)]
        self.unit_sets = {"single": base}
        if oversized or 180 not in self.angles:
            return oversized
        paired = {}
        any_pair = False
        for key, u in base.items():
            pr = make_pair(u.shape, self.gap, self.safety) if u.quantity >= 2 else None
            if pr is not None:
                geom, comps = pr
                pu = Unique("PAIR|" + key, geom, u.quantity // 2, geom.area)
                self._kernelize(pu)
                if self._fits(pu):
                    any_pair = True
                    paired[pu.key] = pu
                    self.components[pu.key] = [(key, a, dx, dy) for a, dx, dy in comps]
                    if u.quantity % 2:
                        rest = Unique(key, u.shape, 1, u.area, u.variants, u.coarse)
                        paired[key] = rest
                    continue
            paired[key] = u
        if any_pair:
            self.unit_sets["pair"] = paired
        return oversized

    # ------------------------------------------------------------ helpers
    def _group_maps(self, sheet, variants):
        """Feasibility + contact maps for every variant of one part.

        Both correlations are taken against the padded occupancy, whose FFT
        is computed once for the whole group (instead of once per variant).
        """
        P = sheet.occ_pad
        N, M = P.shape
        fits = [v.kernel.shape[0] <= N - 2 and v.kernel.shape[1] <= M - 2 for v in variants]
        if not any(fits):
            return [None] * len(variants)
        kh_max = max(v.ring.shape[0] for v, f in zip(variants, fits) if f)
        kw_max = max(v.ring.shape[1] for v, f in zip(variants, fits) if f)
        shape = (sfft.next_fast_len(N + kh_max, True), sfft.next_fast_len(M + kw_max, True))
        PF = sfft.rfft2(P, shape)
        out = []
        for v, ok in zip(variants, fits):
            if not ok:
                out.append(None)
                continue
            kh, kw = v.kernel.shape
            # full linear convolution with the flipped kernel == correlation
            feas = sfft.irfft2(PF * sfft.rfft2(v.kernel_f, shape), shape)
            cont = sfft.irfft2(PF * sfft.rfft2(v.ring_f, shape), shape)
            rows, cols = N - 2 - kh + 1, M - 2 - kw + 1
            # kernel at occ (i, j) == pad (i+1, j+1); full-conv index = start + k - 1
            m = feas[kh:kh + rows, kw:kw + cols] < 0.5
            c = cont[kh + 1:kh + 1 + rows, kw + 1:kw + 1 + cols].astype(np.float32)
            out.append([m, c])
        return out

    CONTACT_WEIGHT = 0.35
    LOW_BIAS = 0.15   # inside the used length: mild preference for low positions

    def _best(self, maps, variants, rule, used=0.0):
        """Lowest score over all variants and feasible cells.

        score = far edge along the fill axis - w * contact length (mm)
        ("bl" rule uses the near edge instead of the far edge). Contact with
        already placed parts and sheet walls rewards interlocking and filling
        pockets/holes. Only a window of rows/cols that can still win is scored.
        """
        best = None
        w = getattr(self, "cw", self.CONTACT_WEIGHT)
        for v, mc in zip(variants, maps):
            if mc is None:
                continue
            m, contact = mc
            if rule == "diag":
                # corner gravity: nearest to the sheet origin corner (x + y)
                rows = self.y0 + np.arange(m.shape[0]) * self.r
                cols = self.x0 + np.arange(m.shape[1]) * self.r
                score = rows[:, None] + cols[None, :] - w * self.r * contact
                score = np.where(m, score, np.inf)
                flat = int(np.argmin(score))
                val = float(score.flat[flat])
                if not math.isfinite(val):
                    continue
                i, j = np.unravel_index(flat, score.shape)
                key = (round(val, 6), i * self.r, j * self.r)
                if best is None or key < best[0]:
                    best = (key, v, int(i), int(j))
                continue
            if self.y_primary:
                any_axis = m.any(axis=1)
                first = int(np.argmax(any_axis))
                if not any_axis[first]:
                    continue
                span = int(math.ceil(w * v.ring_len / self.r)) + 2
                # every row whose part stays inside the already used length is a candidate
                inside = int(max(0, (used - self.y0 - v.height) / self.r)) + 1
                last = min(m.shape[0], max(first + span, inside + span))
                sub, csub = m[first:last], contact[first:last]
                pos = self.y0 + (np.arange(first, last) * self.r)
                edge = pos + (v.height if rule == "edge" else 0.0)
                far = pos + v.height
                score = (np.maximum(far, used) + self.LOW_BIAS * edge)[:, None] - w * self.r * csub
            else:
                any_axis = m.any(axis=0)
                first = int(np.argmax(any_axis))
                if not any_axis[first]:
                    continue
                span = int(math.ceil(w * v.ring_len / self.r)) + 2
                inside = int(max(0, (used - self.x0 - v.width) / self.r)) + 1
                last = min(m.shape[1], max(first + span, inside + span))
                sub, csub = m[:, first:last], contact[:, first:last]
                pos = self.x0 + (np.arange(first, last) * self.r)
                edge = pos + (v.width if rule == "edge" else 0.0)
                far = pos + v.width
                score = (np.maximum(far, used) + self.LOW_BIAS * edge)[None, :] - w * self.r * csub
            score = np.where(sub, score, np.inf)
            flat = int(np.argmin(score))
            val = float(score.flat[flat])
            if not math.isfinite(val):
                continue
            i, j = np.unravel_index(flat, score.shape)
            if self.y_primary:
                i += first
            else:
                j += first
            key = (round(val, 6), i * self.r, j * self.r)
            if best is None or key < best[0]:
                best = (key, v, int(i), int(j))
        return best

    def _valid(self, geom, sheet: Sheet, near_idx):
        minx, miny, maxx, maxy = geom.bounds
        ux0, uy0, ux1, uy1 = self.usable
        if minx < ux0 - 1e-6 or miny < uy0 - 1e-6 or maxx > ux1 + 1e-6 or maxy > uy1 + 1e-6:
            return False
        if len(near_idx) == 0:
            return True
        others = [sheet.geoms[k] for k in near_idx]
        # + safety: true arcs bulge up to ~tol/2 outside their flattened polygon
        return not bool(np.any(shapely.dwithin(geom, others, self.gap + self.safety)))

    def _compact(self, geom, sheet: Sheet):
        """Slide toward the origin (primary axis, then secondary, twice)."""
        reach = self.r * 3.5
        b = geom.bounds
        # four slide passes can move the part up to 4 x reach in total
        pad = 4 * reach + self.gap + 1
        if len(sheet.geoms):
            sb = sheet.bounds
            near = np.flatnonzero((sb[:, 2] >= b[0] - pad) & (sb[:, 0] <= b[2] + pad) &
                                  (sb[:, 3] >= b[1] - pad) & (sb[:, 1] <= b[3] + pad))
        else:
            near = np.array([], dtype=int)
        if not self._valid(geom, sheet, near):
            return geom, 0.0, 0.0  # raster guarantee; keep as is
        total_dx = total_dy = 0.0
        axes = ("y", "x", "y", "x") if self.y_primary else ("x", "y", "x", "y")
        for axis in axes:
            step = self.r / 2
            moved = 0.0
            while moved + step <= reach:
                cand = translate(geom, xoff=-(moved + step)) if axis == "x" else translate(geom, yoff=-(moved + step))
                if self._valid(cand, sheet, near):
                    moved += step
                else:
                    break
            lo, hi = moved, min(moved + step, reach)
            for _ in range(7):
                mid = (lo + hi) / 2
                cand = translate(geom, xoff=-mid) if axis == "x" else translate(geom, yoff=-mid)
                if self._valid(cand, sheet, near):
                    lo = mid
                else:
                    hi = mid
            if lo > 0:
                geom = translate(geom, xoff=-lo) if axis == "x" else translate(geom, yoff=-lo)
                if axis == "x":
                    total_dx += lo
                else:
                    total_dy += lo
        return geom, total_dx, total_dy

    def _mark(self, sheet: Sheet, geom, maps, variants):
        body = geom if self.part_in_part else _solid(geom)
        buf = body.buffer(self.d, quad_segs=8)
        minx, miny, maxx, maxy = buf.bounds
        c0 = max(0, int(math.floor((minx - self.x0) / self.r)))
        r0 = max(0, int(math.floor((miny - self.y0) / self.r)))
        c1 = min(self.nx, int(math.ceil((maxx - self.x0) / self.r)) + 1)
        r1 = min(self.ny, int(math.ceil((maxy - self.y0) / self.r)) + 1)
        if c1 <= c0 or r1 <= r0:
            return
        foot = _raster(buf, self.x0 + c0 * self.r, self.y0 + r0 * self.r, self.r, r1 - r0, c1 - c0)
        before = sheet.occ[r0:r1, c0:c1].copy()
        sheet.occ[r0:r1, c0:c1] = np.maximum(before, foot)
        sheet.occ_pad[r0 + 1:r1 + 1, c0 + 1:c1 + 1] = sheet.occ[r0:r1, c0:c1]
        # Only newly occupied cells change the contact maps (linear update).
        footf = (sheet.occ[r0:r1, c0:c1] - before).astype(np.float32)
        # Incremental feasibility update for the maps being used right now.
        for v, mc in zip(variants, maps):
            if mc is None:
                continue
            m, contact = mc
            k = v.kernel
            kh, kw = k.shape
            hit = fftconvolve(footf, v.kernel_f, mode="full") > 0.5
            i0, j0 = r0 - kh + 1, c0 - kw + 1
            a0, b0 = max(0, i0), max(0, j0)
            a1, b1 = min(m.shape[0], i0 + hit.shape[0]), min(m.shape[1], j0 + hit.shape[1])
            if a1 > a0 and b1 > b0:
                m[a0:a1, b0:b1] &= ~hit[a0 - i0:a1 - i0, b0 - j0:b1 - j0]
            # contact uses the padded grid: occ cell (r, c) is pad cell (r+1, c+1)
            ring = v.ring
            rh, rw = ring.shape
            add = fftconvolve(footf, v.ring_f, mode="full")
            i0, j0 = r0 + 1 - rh + 1, c0 + 1 - rw + 1
            a0, b0 = max(0, i0), max(0, j0)
            a1, b1 = min(contact.shape[0], i0 + add.shape[0]), min(contact.shape[1], j0 + add.shape[1])
            if a1 > a0 and b1 > b0:
                contact[a0:a1, b0:b1] += add[a0 - i0:a1 - i0, b0 - j0:b1 - j0]

    # --------------------------------------------------------------- run
    def _run_once(self, uset, order, rule, along_long, total, done_before, budget_end, coarse=False, cw=None):
        """One greedy layout. ``order`` items are keys or (key, limit) chunks:
        a chunk places at most ``limit`` copies per sheet visit, so a group can
        be split and its rest placed later (lets small parts fill gaps early)."""
        self.y_primary = self.long_y if along_long else not self.long_y
        self.cw = self.CONTACT_WEIGHT if cw is None else cw
        items = [it if isinstance(it, tuple) else (it, None) for it in order]
        remaining = {k: uset[k].quantity for k in {k for k, _ in items}}
        unit_total = max(1, sum(remaining.values()))
        sheets: list[Sheet] = []
        usable_area = (self.W - 2 * self.margin) * (self.H - 2 * self.margin)
        placed_count = 0
        while any(remaining.values()):
            sheet = Sheet(self.ny, self.nx)
            for key, limit in items:
                if remaining[key] == 0:
                    continue
                u = uset[key]
                if u.area > usable_area - sheet.net_area * 1.02:
                    continue
                variants = u.coarse if coarse else u.variants
                maps = self._group_maps(sheet, variants)
                visit = 0
                while remaining[key] > 0 and (limit is None or visit < limit):
                    if self.should_stop and self.should_stop():
                        raise NestCancelled()
                    best = self._best(maps, variants, rule, self._used_length(sheet))
                    if best is None:
                        break
                    _, v, i, j = best
                    X = self.x0 + j * self.r + self.d
                    Y = self.y0 + i * self.r + self.d
                    geom = translate(v.shape, X, Y)
                    geom, dx, dy = self._compact(geom, sheet)
                    tx = X - dx - v.off[0]
                    ty = Y - dy - v.off[1]
                    sheet.add(Placement(key, v.angle, tx, ty, geom))
                    self._mark(sheet, geom, maps, variants)
                    remaining[key] -= 1
                    visit += 1
                    placed_count += 1
                    if self.progress and placed_count % 5 == 0:
                        self.progress(done_before + int(1000 * placed_count / unit_total), total)
            if not sheet.placed:
                raise RuntimeError("Parça boş plakaya yerleştirilemedi.")
            sheets.append(sheet)
            if time.monotonic() > budget_end and sheets and done_before > 0:
                return None  # a later strategy ran out of time: abandon it
        return sheets

    def _used_length(self, sheet: Sheet, along_y=None):
        if not sheet.placed:
            return 0.0
        y = self.y_primary if along_y is None else along_y
        return float(sheet.bounds[:, 3].max() if y else sheet.bounds[:, 2].max())

    def _orders(self, U):
        keys = list(U)

        def dims(k):
            v = U[k].variants[0]
            return v.width, v.height
        by_area = sorted(keys, key=lambda k: (-U[k].area, k))
        by_long = sorted(keys, key=lambda k: (-max(dims(k)), -U[k].area, k))
        by_perim = sorted(keys, key=lambda k: (-U[k].shape.length, k))
        # (label, scoring rule, fill along the long side?, order)
        orders = [
            ("Alan ↓, uzun kenar boyunca", "edge", True, by_area),
            ("Alan ↓, köşeye çekim", "diag", True, by_area),
            ("Alan ↓, kısa kenar boyunca", "edge", False, by_area),
            ("Uzun kenar ↓", "edge", True, by_long),
            ("Alan ↓, alt-sol", "bl", True, by_area),
            ("Çevre ↓", "edge", True, by_perim),
            ("Uzun kenar ↓, kısa kenar boyunca", "edge", False, by_long),
        ]
        for n in range(40):
            perturbed = by_area[:]
            for _ in range(max(1, len(perturbed) // 3)):
                a = self.rng.randrange(len(perturbed))
                b = min(len(perturbed) - 1, a + self.rng.randint(1, 3))
                perturbed[a], perturbed[b] = perturbed[b], perturbed[a]
            orders.append((f"Karışık sıra #{n + 1}", self.rng.choice(["edge", "bl", "diag"]), self.rng.random() < .7, perturbed))
        return orders

    def run(self):
        with sfft.set_workers(-1):
            return self._run()

    def _run(self):
        start = time.monotonic()
        oversized = self._prepare()
        if oversized:
            return {"oversized": oversized}
        sets = self.unit_sets
        per_set = {name: self._orders(U) for name, U in sets.items()}
        # interleave: pairs first (usually best for brackets/triangles), then singles
        plan = []
        names = ["pair", "single"] if "pair" in sets else ["single"]
        for i in range(max(len(o) for o in per_set.values())):
            for name in names:
                if i < len(per_set[name]):
                    plan.append((name, *per_set[name][i], self.CONTACT_WEIGHT))
        plan = plan[: max(1, self.q["strategies"] * len(names))]
        budget = self.q["budget"]
        fine = any(len(u.variants) > len(u.coarse) for U in sets.values() for u in U.values())
        best = None
        tried = []
        runs_planned = len(plan) + (2 if fine else 0)
        grand_total = 1000 * runs_planned
        done_runs = 0

        def consider(sheets, label):
            nonlocal best
            score = (len(sheets), self._used_length(sheets[-1], self.long_y))
            tried.append({"strategy": label, "sheets": len(sheets), "lastUsedMm": round(score[1], 1)})
            if best is None or score < best[0]:
                best = (score, sheets, label, current_set[0])
            return score

        current_set = ["single"]

        def name_of(set_name, label):
            return label + (", çiftli" if set_name == "pair" else "")

        # Phase 1: many part orders / fill rules / unit sets. With fine angle
        # steps this phase uses only 0/90/180/270, which is several times
        # faster, so far more strategies fit in the time budget.
        search = self.q.get("search", 0.0)
        phase1_share = 0.35 if fine else max(0.3, 1.0 - search)
        phase1_end = start + budget * phase1_share
        ranking = []
        for n, (set_name, label, rule, along_long, order, cw) in enumerate(list(plan)):
            if n > 0 and time.monotonic() > phase1_end:
                break
            current_set[0] = set_name
            sheets = self._run_once(sets[set_name], order, rule, along_long, grand_total, done_runs * 1000, phase1_end, coarse=fine, cw=cw)
            done_runs += 1
            if sheets is None:
                break
            score = consider(sheets, name_of(set_name, label) + (", 90° tarama" if fine else ""))
            ranking.append((score, n))
        # Phase 1b: local search from the best strategy. Mutations: swap / move
        # part groups, split a group into two chunks, change fill rule, fill
        # axis or contact weight. Only improvements are kept.
        if search > 0 and ranking:
            ranking.sort()
            search_end = start + budget * min(0.95, phase1_share + search)
            cur_score, n0 = ranking[0]
            cur = plan[n0]
            it = 0
            while time.monotonic() < search_end:
                cand = self._mutate(cur, sets)
                it += 1
                current_set[0] = cand[0]
                sheets = self._run_once(sets[cand[0]], cand[4], cand[2], cand[3], grand_total, done_runs * 1000,
                                        search_end, coarse=fine, cw=cand[5])
                if sheets is None:
                    break
                score = (len(sheets), self._used_length(sheets[-1], self.long_y))
                if score < cur_score:
                    cand = (cand[0], f"Optimizasyon adımı {it}", *cand[2:])
                    cur, cur_score = cand, score
                    plan.append(cand)
                    ranking.append((score, len(plan) - 1))
                    consider(sheets, name_of(cand[0], cand[1]) + (", 90° tarama" if fine else ""))
                if self.progress:
                    frac = (time.monotonic() - start) / budget
                    self.progress(int(grand_total * min(0.9, frac)), grand_total)
            self.search_iterations = it
        # Phase 2: the two best strategies again with every allowed angle.
        if fine:
            ranking.sort()
            budget_end = start + budget
            for k, (_, n) in enumerate(ranking[:2]):
                if k > 0 and time.monotonic() > budget_end:
                    break
                set_name, label, rule, along_long, order, cw = plan[n]
                current_set[0] = set_name
                sheets = self._run_once(sets[set_name], order, rule, along_long, grand_total, done_runs * 1000,
                                        budget_end if k > 0 else float("inf"), cw=cw)
                done_runs += 1
                if sheets is None:
                    break
                consider(sheets, name_of(set_name, label) + ", tüm açılar")
        unit_sheets, label = self._improve_tail(best, start + budget * 1.3, tried)
        sheets = self._expand(unit_sheets)
        self._verify(sheets)
        if self.progress:
            self.progress(grand_total, grand_total)
        return {"sheets": sheets, "strategy": label, "tried": tried,
                "seconds": round(time.monotonic() - start, 2), "cell": round(self.r, 2),
                "pairs": sorted(k.split("|", 1)[1] for k in sets.get("pair", {}) if k.startswith("PAIR|")),
                "anglesPerPart": {k: [v.angle for v in u.variants] for k, u in sets["single"].items()}}

    def _mutate(self, entry, sets):
        set_name, label, rule, along_long, order, cw = entry
        U = sets[set_name]
        items = [it if isinstance(it, tuple) else (it, None) for it in order]
        rng = self.rng
        r = rng.random()
        n = len(items)
        if r < 0.28 and n > 1:                       # swap two groups
            a, b = rng.sample(range(n), 2)
            items[a], items[b] = items[b], items[a]
        elif r < 0.50 and n > 1:                     # move one group
            a = rng.randrange(n)
            it = items.pop(a)
            items.insert(rng.randrange(n), it)
        elif r < 0.70:                               # split a group into chunks
            cands = [i for i, (k, lim) in enumerate(items) if lim is None and U[k].quantity >= 4]
            if cands:
                i = rng.choice(cands)
                k = items[i][0]
                first = rng.randint(1, max(1, U[k].quantity // 2))
                items[i] = (k, first)
                items.insert(rng.randint(i + 1, len(items)), (k, None))
        elif r < 0.82:                               # fill rule
            rule = rng.choice([x for x in ("edge", "bl", "diag") if x != rule])
        elif r < 0.94:                               # contact weight
            cw = rng.choice([x for x in (0.15, 0.25, 0.35, 0.5, 0.75, 1.0) if x != cw])
        else:                                        # fill axis
            along_long = not along_long
        # merge consecutive chunks of the same group
        merged = []
        for k, lim in items:
            if merged and merged[-1][0] == k:
                pl = merged[-1][1]
                merged[-1] = (k, None if pl is None or lim is None else pl + lim)
            else:
                merged.append((k, lim))
        # every group needs a final open chunk so all copies get placed
        keys_with_open = {k for k, lim in merged if lim is None}
        for k in {k for k, _ in merged} - keys_with_open:
            merged.append((k, None))
        return (set_name, label, rule, along_long, merged, cw)

    def _improve_tail(self, best, deadline, tried):
        """Re-nest the last two sheets together to free a long remnant strip.

        Sheet count is already fixed by the best strategy; here the parts of the
        last two sheets are placed again with several rules and all angles.
        A result that still needs two sheets but uses less of the last one
        wins, so the first sheets get denser and the off-cut becomes reusable.
        """
        (count, used), unit_sheets, label, set_name = best
        if len(unit_sheets) < 2 or time.monotonic() > deadline:
            return unit_sheets, label
        head, tail = unit_sheets[:-2], unit_sheets[-2:]
        qty = collections.Counter(pl.key for sh in tail for pl in sh.placed)
        best_tail, best_used = None, used
        for name in ([set_name, "single"] if set_name != "single" else ["single"]):
            uset_all = self.unit_sets[name]
            # rebuild quantities for this unit set from the real parts of the tail
            parts = collections.Counter()
            for key, n in qty.items():
                for comp in self.components[key]:
                    parts[comp[0]] += n
            sub = {}
            for key, u in uset_all.items():
                if key.startswith("PAIR|"):
                    base = key.split("|", 1)[1]
                    k = parts.get(base, 0) // 2
                    if k:
                        sub[key] = Unique(key, u.shape, k, u.area, u.variants, u.coarse)
                        parts[base] -= 2 * k
            for key, n in parts.items():
                if n > 0:
                    u = self.unit_sets["single"][key]
                    sub[key] = Unique(key, u.shape, n, u.area, u.variants, u.coarse)
            for o_label, rule, along_long, order in self._orders(sub)[:8]:
                self.cw = self.CONTACT_WEIGHT
                if time.monotonic() > deadline:
                    break
                res = self._run_once(sub, order, rule, along_long, 1, 0, deadline)
                if res is None or len(res) > 2:
                    continue
                u_len = self._used_length(res[-1], self.long_y)
                if len(res) == 2 and u_len < best_used - 1:
                    best_tail, best_used = res, u_len
                    tried.append({"strategy": f"son iki plaka: {o_label}", "sheets": len(head) + 2, "lastUsedMm": round(u_len, 1)})
        if best_tail is None:
            return unit_sheets, label
        return head + best_tail, label + " + son plaka iyileştirmesi"

    def fill(self, obstacle, order=None):
        """Place as many units as possible on ONE sheet that already holds
        ``obstacle`` (a shapely geometry in sheet coordinates).

        Used for (a) part-in-part kits: the obstacle is everything outside a
        host's hole, and (b) filling the gaps a strip/sheet layout left open.
        Returns {unit_key: count} placed and the part-level Placements.
        """
        with sfft.set_workers(-1):
            self._prepare_units()
            sheet = Sheet(self.ny, self.nx)
            if obstacle is not None and not obstacle.is_empty:
                buf = obstacle.buffer(self.d, quad_segs=8)
                occ = _raster(buf, self.x0, self.y0, self.r, self.ny, self.nx).astype(np.float32)
                sheet.occ = np.maximum(sheet.occ, occ)
                sheet.occ_pad[1:-1, 1:-1] = sheet.occ
                sheet.geoms.append(obstacle)
                sheet.bounds = np.vstack([sheet.bounds, np.array(obstacle.bounds)[None, :]])
            uset = self.unit_sets["single"]
            keys = order or sorted(uset, key=lambda k: -uset[k].area)
            placed = collections.Counter()
            self.cw = self.CONTACT_WEIGHT
            self.y_primary = self.long_y
            for key in keys:
                u = uset[key]
                if u.quantity <= 0:
                    continue
                maps = self._group_maps(sheet, u.variants)
                while placed[key] < u.quantity:
                    if self.should_stop and self.should_stop():
                        raise NestCancelled()
                    best = self._best(maps, u.variants, "edge", self._used_length(sheet))
                    if best is None:
                        break
                    _, v, i, j = best
                    X = self.x0 + j * self.r + self.d
                    Y = self.y0 + i * self.r + self.d
                    geom, dx, dy = self._compact(translate(v.shape, X, Y), sheet)
                    pl = Placement(key, v.angle, X - dx - v.off[0], Y - dy - v.off[1], geom)
                    sheet.geoms.append(geom)
                    sheet.bounds = np.vstack([sheet.bounds, np.array(geom.bounds)[None, :]])
                    sheet.placed.append(pl)
                    self._mark(sheet, geom, maps, u.variants)
                    placed[key] += 1
            return placed, sheet.placed

    def _prepare_units(self):
        """Kernels for single units only (no pairing) - cheap set-up for fill()."""
        self.components = {}
        base = {}
        for key, shape, qty in self.items:
            if qty <= 0:
                continue
            u = Unique(key, shape, qty, shape.area)
            self._kernelize(u)
            if self._fits(u):
                base[key] = u
            self.components[key] = [(key, 0.0, 0.0, 0.0)]
        self.unit_sets = {"single": base}

    def _expand(self, unit_sheets):
        """Unit placements (singles and pairs) -> one placement per real part."""
        shapes = {u.key: u.shape for u in self.unit_sets["single"].values()}
        out = []
        for us in unit_sheets:
            sheet = Sheet(1, 1)
            for pl in us.placed:
                th = math.radians(pl.angle)
                c, s_ = math.cos(th), math.sin(th)
                for part_key, a, dx, dy in self.components[pl.key]:
                    angle = (a + pl.angle) % 360
                    tx = pl.tx + c * dx - s_ * dy
                    ty = pl.ty + s_ * dx + c * dy
                    g = translate(rotate(shapes[part_key], angle, origin=(0, 0)), tx, ty)
                    sheet.add(Placement(part_key, angle, tx, ty, g))
            out.append(sheet)
        return out

    def _verify(self, sheets):
        ux0, uy0, ux1, uy1 = self.usable
        for s_index, sheet in enumerate(sheets, 1):
            if not sheet.geoms:
                continue
            b = sheet.bounds
            if (b[:, 0] < ux0 - 1e-5).any() or (b[:, 1] < uy0 - 1e-5).any() or (b[:, 2] > ux1 + 1e-5).any() or (b[:, 3] > uy1 + 1e-5).any():
                raise RuntimeError(f"Doğrulama: plaka {s_index} sınır dışı parça")
            tree = STRtree(sheet.geoms)
            left, right = tree.query(sheet.geoms, predicate="dwithin", distance=self.gap - 1e-4)
            if np.any(left != right):
                raise RuntimeError(f"Doğrulama: plaka {s_index} parça aralığı ihlali")


def nest(items, sheet_w, sheet_h, margin, gap, **kw):
    """items: list of (key, shapely Polygon in original coordinates, quantity)."""
    return Nester(items, sheet_w, sheet_h, margin, gap, **kw).run()
