"""Strip-packing powered sheet nesting (sparrow + DXFsmart).

sparrow (MIT, https://github.com/JeroenGar/sparrow, arXiv 2509.13329) is a
state-of-the-art heuristic for 2D irregular *strip* packing: a strip of fixed
height, minimise its length. It cannot handle holes / part-in-part and knows
nothing about separate sheets. This module adds both:

1. Kits (part-in-part): every part type with holes gets its holes filled with
   smaller parts by DXFsmart's raster nester (the area outside the hole is an
   obstacle). A host + its guests become one unit whose outline is the host's
   outer contour.
2. One sparrow run lays out all units on a strip as high as the usable sheet
   width, as short as possible.
3. Sheet windows: the strip is cut into windows of the usable sheet length.
   Each window starts at the left-most part that did not fit the previous
   one, so no part is ever split by a cut. Each window is a feasible sheet.
4. Gap fill: holes left near the window ends are filled with parts taken
   from the last sheets (DXFsmart raster nester, existing parts = obstacle).
5. The last sheet is compressed again by sparrow -> longest re-usable remnant.
6. Exact verification: inside the usable sheet, every pair >= gap apart.

Live view: sparrow (built with the `live_svg` feature) rewrites
``data/live/.live_solution.svg`` in its working directory on every step;
``live_svg()`` returns it so the UI can show the search as it happens.
"""
from __future__ import annotations

import collections
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import shapely
from shapely.affinity import rotate, translate
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from nest_shape import Nester, NestCancelled, Placement, Sheet

ROTATION_MODES = {
    "none": {"mode": "discrete", "angles": [0]},
    "180": {"mode": "discrete", "angles": [0, 180]},
    "90": {"mode": "stepped", "step": 90},
    "45": {"mode": "stepped", "step": 45},
    "15": {"mode": "stepped", "step": 15},
    "5": {"mode": "stepped", "step": 5},
}
BARRIER_W = 2.0       # width of the forbidden band between two sheets in the strip
BARRIER_SAFE = 0.5    # numerical safety kept from the sheet end
SHEET_SHRINK_STEP = 0.03   # multi-sheet strips are long: explore with 3% steps (compression refines)
WORKERS = max(2, min(8, (os.cpu_count() or 4) - 1))
# seconds of sparrow search per thickness group
BUDGET = {"fast": 12, "balanced": 45, "max": 180}


def sparrow_binary():
    """sparrow executable: bundled next to the app / in the PyInstaller dir / on PATH."""
    names = ["sparrow.exe", "sparrow"] if sys.platform == "win32" else ["sparrow"]
    roots = [getattr(sys, "_MEIPASS", None), os.path.dirname(os.path.abspath(__file__)),
             os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")]
    for root in roots:
        if not root:
            continue
        for n in names:
            p = os.path.join(root, n)
            if os.path.isfile(p):
                return p
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def available():
    return sparrow_binary() is not None


# ----------------------------------------------------------------- kits
def build_kits(items, gap, part_in_part=True, tol=0.05):
    """items: [(key, Polygon, qty)] -> units [(unit_key, geom, qty)] + components."""
    shapes = {k: s for k, s, _ in items}
    qty = {k: q for k, _, q in items}
    components = {k: [(k, 0.0, 0.0, 0.0)] for k in shapes}
    units = []
    if not part_in_part:
        return [(k, shapes[k], qty[k]) for k in shapes if qty[k] > 0], components
    hosts = sorted([k for k in shapes if shapes[k].interiors and qty[k] > 0],
                   key=lambda k: -max(Polygon(r).area for r in shapes[k].interiors))
    used_as_host = set()
    for h in hosts:
        host = shapes[h]
        guests_pool = [k for k in shapes if k != h and qty[k] > 0]
        if not guests_pool:
            continue
        kit_guests = []          # (guest_key, angle, tx, ty)
        # guests of each type available per host copy (shared by all its holes)
        allowance = {g: qty[g] // max(1, qty[h]) for g in guests_pool}
        for ring in host.interiors:
            hole = Polygon(ring)
            inner = hole.buffer(-gap)
            if inner.is_empty or inner.area <= 0:
                continue
            # guests that could fit by size
            cand = []
            for g in guests_pool:
                gs = shapes[g]
                gx0, gy0, gx1, gy1 = gs.minimum_rotated_rectangle.bounds
                if gs.area < inner.area and min(gx1 - gx0, gy1 - gy0) <= max(hole.bounds[2] - hole.bounds[0], hole.bounds[3] - hole.bounds[1]):
                    if allowance[g] > 0:
                        cand.append((g, gs, allowance[g]))
            if not cand:
                continue
            x0, y0, x1, y1 = hole.bounds
            pad = gap
            frame = box(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
            obstacle = frame.difference(hole)
            ob_local = translate(obstacle, -(x0 - pad), -(y0 - pad))
            nest = Nester([(g, shapes[g], n) for g, _, n in cand],
                          frame.bounds[2] - frame.bounds[0], frame.bounds[3] - frame.bounds[1], 0, gap,
                          rotation="15", part_in_part=False, quality="fast", tol=tol)
            try:
                placed, pls = nest.fill(ob_local)
            except Exception:
                continue
            for pl in pls:
                kit_guests.append((pl.key, pl.angle, pl.tx + (x0 - pad), pl.ty + (y0 - pad)))
                allowance[pl.key] -= 1
        if not kit_guests:
            continue
        per = collections.Counter(g for g, *_ in kit_guests)
        n_kits = min([qty[h]] + [qty[g] // c for g, c in per.items()])
        if n_kits <= 0:
            continue
        kit_key = f"KIT|{h}"
        geom_parts = [host] + [translate(rotate(shapes[g], a, origin=(0, 0)), tx, ty) for g, a, tx, ty in kit_guests]
        components[kit_key] = [(h, 0.0, 0.0, 0.0)] + [(g, a, tx, ty) for g, a, tx, ty in kit_guests]
        units.append((kit_key, MultiPolygon(geom_parts), n_kits))
        qty[h] -= n_kits
        for g, c in per.items():
            qty[g] -= c * n_kits
        used_as_host.add(h)
    for k in shapes:
        if qty[k] > 0:
            units.append((k, shapes[k], qty[k]))
    return units, components


def outline(geom):
    """Outer contour handed to sparrow (holes are handled by kits)."""
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    return Polygon(geom.exterior)


# ----------------------------------------------------------------- sparrow
class SparrowRun:
    def __init__(self, workdir):
        self.workdir = workdir
        self.proc = None
        os.makedirs(os.path.join(workdir, "data", "live"), exist_ok=True)

    def live_svg(self):
        p = os.path.join(self.workdir, "data", "live", ".live_solution.svg")
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def styled_live_svg(self):
        """Live layout recoloured for DXFsmart's dark UI.

        In multi-sheet mode the strip is cut at the barriers and the sheets are
        stacked under each other, so the user watches real sheets fill up.
        """
        svg = self.live_svg()
        if not svg or "</svg>" not in svg:
            return None
        svg = (svg.replace('fill="#D3D3D3"', 'fill="#151a33"')
                  .replace('stroke="black"', 'stroke="#ffd6a3"')
                  .replace('fill="#7A7A7A"', 'fill="#ff9b3d"').replace('fill-opacity="0.5"', 'fill-opacity="0.82"')
                  .replace('fill="#636363"', 'fill="#ec4899"').replace('stroke="#313131"', 'stroke="#ec4899"')
                  .replace('stroke-opacity="0.3"', 'stroke-opacity="0"')
                  .replace('<text ', '<text fill="#cdd3ff" '))
        P, H, g = getattr(self, "live_period", None), getattr(self, "live_height", None), getattr(self, "live_gap", 0.0)
        m = re.search(r'<g id="container_0">\s*<path d="M0,0 L([0-9.]+),0', svg)
        if not P or not H or not m:
            return svg
        width = float(m.group(1))
        body = svg[svg.index(">", svg.index("<svg")) + 1: svg.rindex("</svg>")]
        body = re.sub(r"<text.*?</text>", "", body, flags=re.S)
        n = max(1, math.ceil((width - g / 2) / P))
        vw, row, pad = P - BARRIER_W + g, H, 0.07 * H
        parts = []
        for k in range(n):
            x0 = k * P + g / 2
            y = k * (row + pad)
            parts.append(f'<svg x="0" y="{y:.2f}" width="{vw:.2f}" height="{row:.2f}" viewBox="{x0:.2f} 0 {vw:.2f} {row:.2f}">'
                         f'<rect x="{x0:.2f}" y="0" width="{vw:.2f}" height="{row:.2f}" fill="#151a33"/>'
                         f'<use href="#dxfs_strip"/></svg>'
                         f'<rect x="0" y="{y:.2f}" width="{vw:.2f}" height="{row:.2f}" fill="none" stroke="#ec4899" stroke-opacity=".55" stroke-width="{0.004 * vw:.2f}"/>')
        total_h = n * row + (n - 1) * pad
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {vw:.2f} {total_h:.2f}">'
                f'<defs><g id="dxfs_strip">{body}</g></defs>{"".join(parts)}</svg>')

    def status(self):
        """(sheets, density %) parsed from the latest feasible step, for the progress text."""
        p = os.path.join(self.workdir, "output", "log.txt")
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-60:]
        except Exception:
            return None
        for line in reversed(tail):
            m = re.search(r"width: ([0-9.]+), dens: ([0-9.]+)%", line)
            if m and ("feasible" in line or "compress" in line or "success" in line):
                w, d = float(m.group(1)), float(m.group(2))
                P, g = getattr(self, "live_period", None), getattr(self, "live_gap", 0.0)
                sheets = max(1, math.ceil((w - g / 2) / P)) if P else None
                return sheets, d
        return None

    def progress_line(self):
        p = os.path.join(self.workdir, "output", "log.txt")
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-40:]
        except Exception:
            return ""
        for line in reversed(lines):
            if "feasible solution found" in line or "compressed" in line or "success at" in line:
                return line.split("]")[-1].strip()
        return ""

    def solve(self, units, strip_height, gap, rotation, seconds, name, should_stop=None, seed=7, sheet_length=None):
        """units: [(key, outline Polygon, qty)] -> (strip_length, [(key, angle_deg, tx, ty)]).

        strip_height is the *usable* height: sparrow keeps `gap` clearance from
        its container edge, so the container is made 2*gap higher and every
        result is shifted back by -gap (parts may touch the usable edge).

        sheet_length: if given, the strip is divided into sheets of that usable
        length by thin forbidden barriers (patched jagua-rs: quality-0 zones).
        Item clearance to a barrier is gap/2 on each side, so with
            Lx' = sheet_length - SAFE, w = barrier width,
            period P = Lx' + gap + w, barrier offset o = gap + Lx' + gap/2,
        sheet k's parts lie in [k*P + gap, k*P + gap + Lx'] (container frame).
        Returned x values are then local to their sheet and an extra sheet
        index is returned per part: (key, angle, tx, ty, k).
        """
        binary = sparrow_binary()
        if binary is None:
            raise RuntimeError("sparrow bulunamadı")
        key_of = {}
        items = []
        for i, (key, poly, q) in enumerate(units):
            poly = shapely.set_precision(poly.simplify(0.02), 0.001)
            pts = [[round(x, 4), round(y, 4)] for x, y in list(poly.exterior.coords)[:-1]]
            items.append({"id": i, "demand": int(q), "orientation": {"rotation": ROTATION_MODES.get(rotation, ROTATION_MODES["90"])},
                          "shape": {"type": "simple_polygon", "data": pts}})
            key_of[i] = key
        inst = {"name": name, "items": items, "strip_height": float(strip_height) + 2 * float(gap),
                "min_item_separation": float(gap)}
        period = None
        self.live_period, self.live_height, self.live_gap = None, float(strip_height) + 2 * float(gap), float(gap)
        if sheet_length is not None:
            lx = float(sheet_length) - BARRIER_SAFE
            period = lx + float(gap) + BARRIER_W
            self.live_period = period
            inst.update({"sheet_period": period, "barrier_offset": float(gap) + lx + float(gap) / 2,
                         "barrier_width": BARRIER_W})
        path = os.path.join(self.workdir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(inst, f)
        out = os.path.join(self.workdir, "output", f"final_{name}.json")
        if os.path.exists(out):
            os.remove(out)
        secs = max(2, int(round(seconds)))
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        cmd = [binary, "-i", path, "-t", str(secs), "-s", str(seed), "--workers", str(WORKERS)]
        if sheet_length is not None:
            cmd += ["--shrink-step", str(SHEET_SHRINK_STEP)]
        self.proc = subprocess.Popen(cmd, cwd=self.workdir,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.time() + secs + 60
        while self.proc.poll() is None:
            if should_stop and should_stop():
                self.proc.kill()
                raise NestCancelled()
            if time.time() > deadline:
                self.proc.kill()
                raise RuntimeError("sparrow zaman aşımı")
            time.sleep(0.2)
        if not os.path.exists(out):
            raise RuntimeError("sparrow sonuç üretmedi")
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        sol = data["solution"]
        g = float(gap)
        placed = [(key_of[p["item_id"]], float(p["transformation"]["rotation"]),
                   float(p["transformation"]["translation"][0]) - g, float(p["transformation"]["translation"][1]) - g)
                  for p in sol["layout"]["placed_items"]]
        length = float(sol["strip_width"]) - 2 * g
        if period is None:
            return length, placed
        # sheet index from each part's left bound (container frame = local + gap)
        polys = {i: Polygon(it["shape"]["data"]) for i, it in enumerate(items)}
        idx_of = {v: k for k, v in key_of.items()}
        out = []
        for key, a, tx, ty in placed:
            b = rotate(polys[idx_of[key]], a, origin=(0, 0)).bounds
            k = int(math.floor((tx + b[0] + 0.5 * BARRIER_SAFE) / period))
            out.append((key, a, tx - k * period, ty, k))
        last = max((o[4] for o in out), default=0)
        return length - last * period, out


# ----------------------------------------------------------------- driver
class SparrowSheetNester:
    def __init__(self, items, sheet_w, sheet_h, margin, gap, rotation="15", part_in_part=True,
                 quality="balanced", tol=0.05, progress=None, should_stop=None, live=None):
        self.items = [(k, s, q) for k, s, q in items if q > 0]
        self.W, self.H = float(sheet_w), float(sheet_h)
        self.margin, self.gap = float(margin), max(float(gap), 0.01)
        self.rotation, self.part_in_part, self.quality, self.tol = str(rotation), part_in_part, quality, tol
        self.progress, self.should_stop, self.live = progress, should_stop, live
        self.long_y = self.H >= self.W
        self.Lx = (max(self.W, self.H)) - 2 * self.margin     # usable sheet length (strip direction)
        self.Hy = (min(self.W, self.H)) - 2 * self.margin     # usable sheet width (strip height)

    # strip frame (x along the sheet length, y across) -> sheet frame
    def _to_sheet(self, angle, tx, ty):
        if self.long_y:
            # rotate the strip frame +90°: (x, y) -> (-y, x), then shift into the margins
            return (angle + 90.0) % 360, self.margin + self.Hy - ty, self.margin + tx
        return angle % 360, self.margin + tx, self.margin + ty

    def _report(self, frac, text):
        if self.progress:
            self.progress(min(0.99, frac), text)

    def run(self):
        t0 = time.time()
        budget = BUDGET.get(self.quality, 45)
        shapes = {k: s for k, s, _ in self.items}
        self._report(0.02, "Delik içi kitleri hazırlanıyor")
        units, comps = build_kits(self.items, self.gap, self.part_in_part, self.tol)
        geoms = {}
        for key, g, _ in units:
            geoms[key] = g
        # oversize check in strip frame
        for key, g, _ in units:
            o = outline(g)
            mrr = o.minimum_rotated_rectangle
            e = list(mrr.exterior.coords)
            a, b = sorted([math.dist(e[0], e[1]), math.dist(e[1], e[2])])
            if a > self.Hy + 1e-6 or (b > self.Lx and a > self.Lx):
                return {"oversized": [key.split("|")[-1]]}
        work = tempfile.mkdtemp(prefix="dxfsmart_sparrow_")
        self.runner = SparrowRun(work)
        if self.live is not None:
            self.live["runner"] = self.runner
        try:
            outl = [(k, outline(g), q) for k, g, q in units]
            first = budget * 0.85
            self._report(0.05, f"sparrow plakaları optimize ediyor ({int(first)} sn)")
            stop_watch = threading.Event()

            def ticker():
                while not stop_watch.wait(0.5):
                    frac = 0.05 + 0.77 * min(1.0, (time.time() - t0) / max(1.0, first))
                    st = self.runner.status()
                    self._report(frac, (f"Geçerli yerleşim bulundu: {st[0]} plaka, şerit doluluğu %{st[1]:.1f}. Daha sıkısı aranıyor"
                                        if st else "sparrow ilk yerleşimi kuruyor"))
            th = threading.Thread(target=ticker, daemon=True)
            th.start()
            self.geoms = geoms
            self.t0, self.budget = t0, budget
            try:
                length, placed = self.runner.solve(outl, self.Hy, self.gap, self.rotation, first, "plakalar",
                                                   self.should_stop, sheet_length=self.Lx)
            finally:
                stop_watch.set()
            n = max(p[4] for p in placed) + 1
            sheets = [[] for _ in range(n)]
            for k, a, tx, ty, si in placed:
                sheets[si].append((k, a, tx, ty, translate(rotate(geoms[k], a, origin=(0, 0)), tx, ty)))
            sheets = [sh for sh in sheets if sh]
            self._report(0.82, f"{len(sheets)} plakaya bölündü, boşluklar dolduruluyor")
            sheets = self._fill_gaps(sheets, geoms)
            remaining = budget - (time.time() - t0)
            if len(sheets) >= 1 and remaining > 3:
                self._report(0.9, "Son plaka sıkıştırılıyor")
                sheets[-1] = self._recompress_last(sheets[-1], geoms, remaining)
            part_sheets = self._expand(sheets, comps, shapes)
            self._verify(part_sheets)
            self._report(1.0, "Tamamlandı")
            return {"sheets": part_sheets, "strategy": "sparrow çok-plakalı (bariyerli şerit) + boşluk doldurma",
                    "tried": [{"strategy": "sparrow", "sheets": len(part_sheets), "lastUsedMm": round(self._used(sheets[-1]), 1)}],
                    "seconds": round(time.time() - t0, 2), "cell": 0.0, "stripLengthMm": round(length, 1),
                    "kits": sorted(k.split("|", 1)[1] for k in geoms if k.startswith("KIT|"))}
        finally:
            if self.live is not None:
                self.live.pop("runner", None)
            if not os.environ.get("DXFSMART_KEEP_SPARROW"):
                shutil.rmtree(work, ignore_errors=True)
            else:
                print("sparrow workdir:", work)

    # --- strip -> sheets (strip frame, every sheet = [0, Lx] x [0, Hy])
    def _place(self, placed):
        g = self.geoms
        return [(k, a, tx, ty, translate(rotate(g[k], a, origin=(0, 0)), tx, ty)) for k, a, tx, ty in placed]

    def _windows(self, strip):
        """Cut the optimised strip into sheets.

        For each sheet: take the parts lying fully inside the next window,
        then try to absorb the parts crossing the cut (plus the next ones in
        strip order, up to a density target) by re-solving that set with
        sparrow. If the re-solved set fits the sheet length it is accepted,
        otherwise the candidate list is halved and tried again.
        """
        rest = sorted(strip, key=lambda t: t[4].bounds[0])
        sheets = []
        sheet_area = self.Lx * self.Hy
        while rest:
            start = min(t[4].bounds[0] for t in rest)
            inside = [t for t in rest if t[4].bounds[2] - start <= self.Lx + 1e-6] or [rest[0]]
            ids = {id(t) for t in inside}
            others = [t for t in rest if id(t) not in ids]
            sheet = [(k, a, tx - start, ty, translate(g, -start, 0)) for k, a, tx, ty, g in inside]
            used_ids = set(ids)
            if others:
                area_in = sum(t[4].area for t in inside)
                cands = []
                acc = area_in
                for t in others:                      # strip order: crossing parts first
                    if acc + t[4].area > sheet_area * 0.92:
                        break
                    cands.append(t)
                    acc += t[4].area
                tries = 0
                while cands and tries < 3 and self._time_left() > 4:
                    tries += 1
                    group = inside + cands
                    counts = collections.Counter(t[0] for t in group)
                    secs = min(max(3.0, self.budget * 0.08), self._time_left() / 2)
                    try:
                        length, placed = self.runner.solve([(k, outline(self.geoms[k]), n) for k, n in counts.items()],
                                                           self.Hy, self.gap, self.rotation, secs, f"plaka{len(sheets) + 1}_{tries}",
                                                           self.should_stop, seed=3 + tries)
                    except NestCancelled:
                        raise
                    except Exception:
                        break
                    if length <= self.Lx + 1e-6:
                        sheet = self._place(placed)
                        used_ids = ids | {id(t) for t in cands}
                        break
                    cands = cands[: len(cands) // 2]
            rest = [t for t in rest if id(t) not in used_ids]
            sheets.append(sheet)
            if self.progress:
                self._report(0.8 + 0.08 * min(1.0, len(sheets) / max(1, len(sheets) + len(rest) / 40)),
                             f"{len(sheets)}. plaka hazır, {len(rest)} birim kaldı")
        return sheets

    def _time_left(self):
        return self.budget - (time.time() - self.t0)

    def _used(self, sheet):
        return max((t[4].bounds[2] for t in sheet), default=0.0)

    def _fill_gaps(self, sheets, geoms):
        """Move units from the last sheets into earlier sheets' free space."""
        if len(sheets) < 2:
            return sheets
        for k in range(len(sheets) - 1):
            donors = [t for later in sheets[k + 1:] for t in later]
            if not donors:
                break
            counts = collections.Counter(t[0] for t in donors)
            items = [(key, geoms[key], n) for key, n in counts.items()]
            obstacle = unary_union([t[4] for t in sheets[k]])
            nest = Nester(items, self.Lx, self.Hy, 0, self.gap, rotation=self.rotation if self.rotation != "5" else "15",
                          part_in_part=self.part_in_part, quality="fast", tol=self.tol, should_stop=self.should_stop)
            placed, pls = nest.fill(obstacle)
            if not pls:
                continue
            for pl in pls:
                sheets[k].append((pl.key, pl.angle, pl.tx, pl.ty, pl.geom))
            # remove the same number of units, preferring the last sheets
            need = collections.Counter(pl.key for pl in pls)
            for later in reversed(sheets[k + 1:]):
                keep = []
                for t in reversed(later):
                    if need[t[0]] > 0:
                        need[t[0]] -= 1
                    else:
                        keep.append(t)
                later[:] = list(reversed(keep))
            sheets = [s for s in sheets if s]
        return sheets

    def _recompress_last(self, sheet, geoms, seconds):
        counts = collections.Counter(t[0] for t in sheet)
        units = [(k, outline(geoms[k]), n) for k, n in counts.items()]
        try:
            length, placed = self.runner.solve(units, self.Hy, self.gap, self.rotation, min(seconds, 60), "son", self.should_stop, seed=11)
        except NestCancelled:
            raise
        except Exception:
            return sheet
        if length > self._used(sheet) - 0.5 or length > self.Lx:
            return sheet
        return [(k, a, tx, ty, translate(rotate(geoms[k], a, origin=(0, 0)), tx, ty)) for k, a, tx, ty in placed]

    def _expand(self, sheets, comps, shapes):
        out = []
        for sh in sheets:
            sheet = Sheet(1, 1)
            for key, a, tx, ty, _ in sh:
                th = math.radians(a)
                c, s_ = math.cos(th), math.sin(th)
                for part_key, ca, dx, dy in comps[key]:
                    ang = (ca + a) % 360
                    ptx = tx + c * dx - s_ * dy
                    pty = ty + s_ * dx + c * dy
                    fa, ftx, fty = self._to_sheet(ang, ptx, pty)
                    g = translate(rotate(shapes[part_key], fa, origin=(0, 0)), ftx, fty)
                    sheet.add(Placement(part_key, fa, ftx, fty, g))
            out.append(sheet)
        return out

    def _verify(self, sheets):
        x0, y0 = self.margin, self.margin
        x1, y1 = self.W - self.margin, self.H - self.margin
        tol = 0.05
        for n, sheet in enumerate(sheets, 1):
            b = sheet.bounds
            if len(b) and ((b[:, 0] < x0 - tol).any() or (b[:, 1] < y0 - tol).any() or (b[:, 2] > x1 + tol).any() or (b[:, 3] > y1 + tol).any()):
                raise RuntimeError(f"Doğrulama: plaka {n} sınır dışı parça")
            tree = STRtree(sheet.geoms)
            left, right = tree.query(sheet.geoms, predicate="dwithin", distance=self.gap - 0.05)
            if np.any(left != right):
                raise RuntimeError(f"Doğrulama: plaka {n} parça aralığı ihlali")
