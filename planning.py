"""Quantity-aware mass, cutting time and separate sheet plans for each thickness."""
from dataclasses import replace

from engine import ProcessingError
from estimate import estimate_sheets, finite_in_range, MAX_TOTAL_PIECES, Cancelled
from laser_presets import speed_m_min
from nest_shape import NestCancelled, QUALITY, ROTATIONS, nest

DENSITIES = {'steel': 7850, 'stainless': 8000, 'aluminum': 2700}
MAX_PREVIEW_SHEETS = 60


def planned_parts(parts, req):
    items = req.get('items')
    if not isinstance(items, list) or len(items) > MAX_TOTAL_PIECES:
        raise ProcessingError('Geçerli parça listesi gerekli.')
    selected = []; seen = set(); total = 0
    fallback = req.get('defaultThickness')
    if fallback is not None:
        fallback = finite_in_range('Varsayılan kalınlık', fallback, .01, 500)
    for item in items:
        if not isinstance(item, dict) or item.get('id') not in parts or item['id'] in seen:
            raise ProcessingError('Parça listesi geçersiz veya tekrarlı.')
        seen.add(item['id']); qty = item.get('quantity')
        if type(qty) is not int or not 0 <= qty <= 1200:
            raise ProcessingError('Adet 0–1200 arasında tam sayı olmalı.')
        total += qty
        if total > MAX_TOTAL_PIECES:
            raise ProcessingError(f'En fazla {MAX_TOTAL_PIECES} adet hesaplanabilir.')
        if qty:
            p = parts[item['id']]
            selected.append(replace(p, quantity=qty, thickness_mm=p.thickness_mm or fallback))
    return selected


def _rect_to_transform(sheet, parts_by_id):
    """MaxRects placement (x, y, w, h, rotated) -> (angle, tx, ty) about the
    part's original DXF coordinates, the same format true-shape nesting uses."""
    out = []
    for pl in sheet['placements']:
        p = parts_by_id[pl['id']]
        x1, y1, x2, y2 = p.shape.bounds
        if pl['rotated']:  # rotate 90° CCW about origin: (x, y) -> (-y, x)
            angle, tx, ty = 90, pl['x'] + y2, pl['y'] - x1
        else:
            angle, tx, ty = 0, pl['x'] - x1, pl['y'] - y1
        out.append({'id': pl['id'], 'name': pl['name'], 'angle': angle,
                    'tx': round(tx, 3), 'ty': round(ty, 3)})
    return out


def _used_length(placements, parts_by_id, width, height):
    from shapely.affinity import rotate, translate
    extent = 0.0
    for pl in placements:
        g = parts_by_id[pl['id']].shape
        g = translate(rotate(g, pl['angle'], origin=(0, 0)) if pl['angle'] else g, pl['tx'], pl['ty'])
        b = g.bounds
        extent = max(extent, b[3] if height >= width else b[2])
    return extent


def _plan_group(batch, req, width, height, method, on_progress, should_stop):
    body = {**req, 'items': [{'id': p.id, 'quantity': p.quantity} for p in batch]}
    rotation = str(req.get('rotation', '15'))
    if rotation not in ROTATIONS:
        raise ProcessingError('Döndürme seçimi geçersiz.')
    body['allowRotation'] = rotation != 'none' and rotation != '180'
    by_id = {p.id: p for p in batch}
    rect = estimate_sheets(by_id, body, (lambda c, t: on_progress(0.1 * c / t)) if on_progress else None, should_stop)
    if rect['recommendedSheets'] is None:
        return {**rect, 'method': 'oversized', 'sheets': []}
    rect_sheets = [{'placements': _rect_to_transform(s, by_id), 'netUtilization': s['netUtilization']}
                   for s in rect.pop('sheets')]
    # The MaxRects preview is truncated upstream; recompute every sheet for export.
    full_rect = rect_sheets
    plan = {**rect, 'method': 'rect', 'methodLabel': 'Dikdörtgen (MaxRects)', 'rectSheets': rect['recommendedSheets']}
    chosen = full_rect
    shape_info = None
    if method == 'shape':
        quality = req.get('quality', 'fast')  # the UI always sends its own choice
        if quality not in QUALITY:
            raise ProcessingError('Optimizasyon seviyesi geçersiz.')
        gap = finite_in_range('Parça boşluğu', req.get('gap', 5), 0, 50)
        margin = finite_in_range('Kenar payı', req.get('margin', 10), 0, 100)
        items = [(p.id, p.shape, p.quantity) for p in batch]
        res = nest(items, width, height, margin, gap, rotation=rotation,
                   part_in_part=bool(req.get('partInPart', True)), quality=quality,
                   progress=(lambda d, t: on_progress(0.1 + 0.9 * d / max(1, t))) if on_progress else None,
                   should_stop=should_stop)
        if 'sheets' in res:
            shape_sheets = []
            for s in res['sheets']:
                shape_sheets.append({'placements': [{'id': pl.key, 'name': f'P{by_id[pl.key].index:03d}',
                                                     'angle': round(pl.angle, 3), 'tx': round(pl.tx, 3), 'ty': round(pl.ty, 3)}
                                                    for pl in s.placed],
                                     'netUtilization': round(100 * s.net_area / (width * height), 1)})
            shape_info = {'strategy': res['strategy'], 'tried': res['tried'], 'seconds': res['seconds'],
                          'cellMm': res['cell'], 'sheets': len(shape_sheets)}
            rect_key = (len(full_rect), _used_length(full_rect[-1]['placements'], by_id, width, height))
            shape_key = (len(shape_sheets), _used_length(shape_sheets[-1]['placements'], by_id, width, height))
            if shape_key <= rect_key:
                chosen = shape_sheets
                plan['method'] = 'shape'
                plan['methodLabel'] = 'Gerçek kontur'
            else:
                plan['methodLabel'] = 'Dikdörtgen (bu grupta daha iyi sonuç verdi)'
    net_area = sum(p.shape.area * p.quantity for p in batch)
    count = len(chosen)
    used = _used_length(chosen[-1]['placements'], by_id, width, height) if chosen else 0
    long_side = height if height >= width else width
    from shapely.affinity import rotate as _rot, translate as _tr
    from shapely.geometry import Polygon as _Poly
    from shapely.ops import unary_union as _union
    solid = {pid: _Poly(p.shape.exterior) for pid, p in by_id.items()}
    for s in chosen:
        s['quantity'] = len(s['placements'])
        # footprint: area covered by part outlines (their own holes included).
        # Net utilization counts e.g. a flange's centre hole as waste; the
        # footprint shows how tightly the layout itself is packed.
        foot = _union([_tr(_rot(solid[pl['id']], pl['angle'], origin=(0, 0)) if pl['angle'] else solid[pl['id']], pl['tx'], pl['ty'])
                       for pl in s['placements']]).area
        s['footprintUtilization'] = round(100 * foot / (width * height), 1)
    plan.update({
        'recommendedSheets': count, 'packingSheets': count,
        'estimatedScrap': round(max(0, 100 * (1 - net_area / (count * width * height))), 1) if count else None,
        'lastSheetUsedMm': round(used, 1), 'lastSheetRemnantMm': round(max(0, long_side - used - req.get('margin', 10)), 1),
        'effectiveUtilization': round(100 * net_area / ((count - 1) * width * height + used * (width if height >= width else height)), 1) if count else None,
        'shapeNesting': shape_info,
        'margin': float(req.get('margin', 10)), 'gap': float(req.get('gap', 5)),
        'sheets': chosen[:MAX_PREVIEW_SHEETS], 'previewTruncated': count > MAX_PREVIEW_SHEETS,
    })
    return plan


def estimate_job(parts, req, progress=None, should_stop=None):
    selected = planned_parts(parts, req)
    speed = finite_in_range('Kesim hızı (mm/dk)', req.get('cuttingSpeed', 3000), 1, 200000)
    pierce = finite_in_range('Delme süresi (sn)', req.get('pierceSeconds', 1.5), 0, 120)
    overhead = finite_in_range('Ek süre (%)', req.get('overheadPercent', 15), 0, 200)
    speeds = req.get('cuttingSpeeds', {})
    if not isinstance(speeds, dict):
        raise ProcessingError('Kalınlık hızları geçersiz.')
    material = req.get('material', 'steel')
    if material not in DENSITIES:
        raise ProcessingError('Malzeme seçimi geçersiz.')
    power = req.get('laserKw')
    if power is not None:
        power = finite_in_range('Lazer gücü (kW)', power, 0.5, 40)
    method = req.get('method', 'shape')
    if method not in ('shape', 'rect'):
        raise ProcessingError('Yerleşim yöntemi geçersiz.')
    width = finite_in_range('Plaka genişliği', req.get('sheetWidth'), 100, 10000)
    height = finite_in_range('Plaka uzunluğu', req.get('sheetHeight'), 100, 20000)
    density = DENSITIES[material]
    groups = {}
    for p in selected:
        if p.units == 0 and req.get('confirmMillimeters') is True:
            p = replace(p, units=4)
        if p.units != 4:
            raise ProcessingError('Çizimde birim tanımsız. Ölçülerin mm olduğunu kontrol edip “Birim tanımsızsa mm olarak hesapla” kutusunu işaretleyin. İnç/cm çizimleri önce mm’ye çevirin.')
        if p.thickness_mm is None:
            raise ProcessingError('Bazı parçaların kalınlığı okunamadı. Eksik kalınlık için varsayılan mm değerini girin.')
        groups.setdefault(p.thickness_mm, []).append(p)
    plans = []; mass = 0; items = []; total_minutes = 0; total_length = 0; total_pierces = 0
    ordered = sorted(groups.items())
    for group_index, (thickness, batch) in enumerate(ordered):
        def on_progress(fraction, gi=group_index, th=thickness):
            if progress:
                pct = round(5 + 90 * (gi + min(1, fraction)) / max(1, len(ordered)))
                progress('placing', pct, f'{th:g} mm kalınlık, yerleşim %{round(100 * min(1, fraction))}')
        try:
            plan = _plan_group(batch, req, width, height, method, on_progress, should_stop)
        except NestCancelled as exc:
            raise Cancelled('Yerleşim iptal edildi.') from exc
        kg = sum(p.shape.area * p.thickness_mm * p.quantity * density / 1e9 for p in batch)
        gross = None if plan['recommendedSheets'] is None else plan['recommendedSheets'] * width * height * thickness * density / 1e9
        mass += kg
        user_speed = speeds.get(f'{thickness:g}')
        table_speed = speed_m_min(material, power, thickness) if power else None
        if user_speed not in (None, ''):
            group_speed = finite_in_range('Kalınlık kesim hızı', user_speed, 1, 200000); speed_source = 'kullanıcı'
        elif table_speed:
            group_speed = table_speed * 1000; speed_source = f'{power:g} kW tablo'
        else:
            group_speed = speed; speed_source = 'genel hız'
        length = sum(p.shape.boundary.length * p.quantity for p in batch)
        pierces = sum((len(p.shape.interiors) + 1) * p.quantity for p in batch)
        minutes = (length / group_speed + pierces * pierce / 60) * (1 + overhead / 100)
        total_minutes += minutes; total_length += length; total_pierces += pierces
        plans.append({**plan, 'thicknessMm': thickness, 'netKg': round(kg, 3), 'grossSheetKg': None if gross is None else round(gross, 3),
                      'cuttingSpeed': round(group_speed, 1), 'speedSource': speed_source,
                      'cutLengthM': round(length / 1000, 3), 'pierceCount': pierces, 'estimatedMinutes': round(minutes, 2)})
        for p in batch:
            unit = p.shape.area * thickness * density / 1e9
            items.append({'id': p.id, 'quantity': p.quantity, 'thicknessMm': thickness, 'unitKg': round(unit, 6), 'totalKg': round(unit * p.quantity, 6)})
    feasible = all(p['recommendedSheets'] is not None for p in plans)
    return {'totalQuantity': sum(p.quantity for p in selected), 'totalNetKg': round(mass, 3),
            'totalSheets': sum(p['recommendedSheets'] for p in plans) if feasible else None,
            'totalRectSheets': sum(p.get('rectSheets') or 0 for p in plans) if feasible else None,
            'totalGrossSheetKg': round(sum(p['grossSheetKg'] for p in plans), 3) if feasible else None,
            'estimatedMinutes': round(total_minutes, 2), 'cutLengthM': round(total_length / 1000, 3), 'pierceCount': total_pierces,
            'pierceSeconds': pierce, 'overheadPercent': overhead, 'laserKw': power, 'method': method,
            'sheetWidth': width, 'sheetHeight': height,
            'material': material, 'densityKgM3': density, 'groups': plans, 'items': items}
