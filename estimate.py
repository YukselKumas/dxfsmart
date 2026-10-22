"""Conservative bounding-box packing estimate for DXF prototypes.

This is *not* polygon nesting and may overstate requirements significantly for
L-shaped, round or irregular contours. It does produce valid non-overlapping
axis-aligned bounding rectangle placements at chosen rotation and spacing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from engine import Part, ProcessingError

MAX_TOTAL_PIECES = 2400
MAX_PREVIEW_SHEETS = 1000


@dataclass
class RectangleSheet:
    width: float
    height: float
    margin: float
    free: list[tuple[float, float, float, float]] = field(default_factory=list)
    placements: list[dict] = field(default_factory=list)
    net_area: float = 0.0

    def __post_init__(self):
        self.free = [(self.margin, self.margin,
                      self.width-2*self.margin, self.height-2*self.margin)]

    def position(self, w: float, h: float, gap: float, allow_rotation: bool, heuristic='short'):
        candidates = ((w,h,False), (h,w,True)) if allow_rotation and abs(w-h)>1e-7 else ((w,h,False),)
        best = None
        for r, (fx,fy,fw,fh) in enumerate(self.free):
            for aw,ah,rotated in candidates:
                aw2,ah2=aw+gap,ah+gap
                if aw2<=fw+1e-7 and ah2<=fh+1e-7:
                    score = (min(fw-aw2,fh-ah2), fw*fh-aw2*ah2, fy, fx)
                    if heuristic=='area': score=(fw*fh-aw2*ah2,min(fw-aw2,fh-ah2),fy,fx)
                    elif heuristic=='bottom': score=(fy+ah,fx,min(fw-aw2,fh-ah2),fw*fh-aw2*ah2)
                    elif heuristic=='long': score=(max(fw-aw2,fh-ah2),min(fw-aw2,fh-ah2),fy,fx)
                    if best is None or score<best[0]:
                        best=(score,fx,fy,aw,ah,rotated)
        return best

    def insert(self, part: Part, option, gap: float):
        _,x,y,w,h,rotated = option
        self.placements.append({'id':part.id,'name':f'P{part.index:03d}',
            'x':round(x,3),'y':round(y,3),'w':round(w,3),'h':round(h,3),
            'rotated':rotated})
        self.net_area += float(part.shape.area)
        self._split(x, y, w + gap, h + gap)

    def _split(self, px,py,pw,ph):
        kept=[];created=[]
        for fx,fy,fw,fh in self.free:
            if px>=fx+fw-1e-7 or px+pw<=fx+1e-7 or py>=fy+fh-1e-7 or py+ph<=fy+1e-7:
                kept.append((fx,fy,fw,fh));continue
            if px>fx+1e-7: created.append((fx,fy,px-fx,fh))
            if px+pw<fx+fw-1e-7: created.append((px+pw,fy,fx+fw-(px+pw),fh))
            if py>fy+1e-7: created.append((fx,fy,fw,py-fy))
            if py+ph<fy+fh-1e-7: created.append((fx,py+ph,fw,fy+fh-(py+ph)))
        # v0.8: untouched rectangles were already maximal and cannot be contained
        # by pieces cut from another rectangle, so only new pieces are pruned.
        # This turns the O(F^2) cleanup into O(new x F).
        def inside(a,b):
            return a[0]>=b[0]-1e-6 and a[1]>=b[1]-1e-6 and a[0]+a[2]<=b[0]+b[2]+1e-6 and a[1]+a[3]<=b[1]+b[3]+1e-6
        created=[a for a in created if a[2]>=1e-6 and a[3]>=1e-6]
        survivors=[]
        for i,a in enumerate(created):
            if any(inside(a,b) for b in kept): continue
            if any(j!=i and inside(a,b) and (a!=b or j<i) for j,b in enumerate(created)): continue
            survivors.append(a)
        self.free=kept+survivors

    def to_dict(self):
        return {'quantity':len(self.placements),
                'netUtilization':round(100*self.net_area/(self.width*self.height),1),
                'placements':self.placements}


def finite_in_range(name, val, low, high):
    if isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or not low<=val<=high:
        raise ProcessingError(f'{name}: {low:g}–{high:g} aralığında sayısal bir değer girin.')
    return float(val)


class Cancelled(ProcessingError):
    """Raised when a newer nesting request supersedes this one."""


def estimate_sheets(parts_by_id: dict[str,Part], req: dict, progress=None, should_stop=None) -> dict:
    try:
        width=finite_in_range('Plaka genişliği',req['sheetWidth'],100,10000)
        height=finite_in_range('Plaka uzunluğu',req['sheetHeight'],100,20000)
        gap=finite_in_range('Parça boşluğu',req.get('gap',5),0,50)
        margin=finite_in_range('Kenar payı',req.get('margin',10),0,100)
        efficiency=finite_in_range('Hedef verimlilik',req.get('targetEfficiency',80),50,95)/100
        rotate = req.get('allowRotation',True)
        if not isinstance(rotate,bool): raise ProcessingError('Döndürme seçeneği geçersiz.')
        entries=req['items']
        if not isinstance(entries,list) or len(entries)>MAX_TOTAL_PIECES:
            raise ProcessingError('Çok fazla parça grubu.')
    except (KeyError, TypeError, ValueError) as exc:
        raise ProcessingError('Plaka hesabı için geçerli ölçü ve adetler girin.') from exc
    if width-2*margin<=0 or height-2*margin<=0:
        raise ProcessingError('Kenar payı plakanın kullanılabilir alanını tüketiyor.')
    pieces=[]
    quantities={}
    for entry in entries:
        if not isinstance(entry,dict) or not isinstance(entry.get('id'),str) or entry['id'] not in parts_by_id:
            raise ProcessingError('Parça listesi güncel değil; DXF dosyasını tekrar analiz edin.')
        qty=entry.get('quantity')
        if type(qty) is not int or not 0<=qty<=1200:
            raise ProcessingError('Adet 0 ile 1200 arasında tam sayı olmalı.')
        if entry['id'] in quantities:
            raise ProcessingError('Tekrarlı parça id tespit edildi.')
        quantities[entry['id']]=qty
        if len(pieces)+qty>MAX_TOTAL_PIECES:
            raise ProcessingError(f'Bu ilk prototip tek hesaplamada en fazla {MAX_TOTAL_PIECES} adet işler.')
        part=parts_by_id[entry['id']]
        pieces.extend([part]*qty)
    if not pieces:
        return {'totalQuantity':0,'recommendedSheets':0,'theoreticalSheets':0,
                'packingSheets':0,'totalNetAreaM2':0,'estimatedScrap':None,
                'oversized':[],'sheets':[]}
    if any(part.units != 4 for part in pieces):
        raise ProcessingError('Plaka hesabı için DXF birimi mm ($INSUNITS=4) olmalı. Birimsiz DXF ölçülerini üretim programında doğrulayın.')
    usable_width=width-2*margin;usable_height=height-2*margin
    net_area=sum(part.shape.area for part in pieces)
    usable_area=usable_width*usable_height
    lower_limit=math.ceil(net_area/usable_area)
    theoretical=math.ceil(net_area/(usable_area*efficiency))
    oversized=[]
    for p in pieces:
        x1,y1,x2,y2 = p.shape.bounds
        pw,ph=x2-x1,y2-y1
        fits=(pw<=usable_width+1e-7 and ph<=usable_height+1e-7) or (
            rotate and ph<=usable_width+1e-7 and pw<=usable_height+1e-7)
        if not fits:
            oversized.append({'id':p.id,'name':f'{p.filename} / Parça {p.index:03d}',
                'width':round(pw,2),'height':round(ph,2)})
    if oversized:
        return {'totalQuantity':len(pieces),'recommendedSheets':None,
           'theoreticalSheets':max(lower_limit,theoretical),'packingSheets':None,
           'totalNetAreaM2':round(net_area/1e6,4),'estimatedScrap':None,
           'oversized':oversized[:30],'sheets':[]}
    # Retain the original pass as a baseline; compare independent order/fit strategies.
    sheets, trials = optimize_rectangles(pieces,width,height,margin,gap,rotate,progress,should_stop)
    rect_count=len(sheets)
    recommended=rect_count
    scrap=100*(1-net_area/(recommended*width*height))
    return {'totalQuantity':len(pieces),'recommendedSheets':recommended,
        'theoreticalSheets':max(theoretical,lower_limit),'packingSheets':rect_count,
        'areaLowerBound':lower_limit,'strategiesTested':trials,'method':'MaxRects / çoklu strateji',
        'totalNetAreaM2':round(net_area/1e6,4),'estimatedScrap':round(max(0,scrap),1),'oversized':[],
        'sheets':[s.to_dict() for s in sheets[:MAX_PREVIEW_SHEETS]],'previewTruncated':len(sheets)>MAX_PREVIEW_SHEETS}


def pack_order(pieces,width,height,margin,gap,rotate,heuristic,should_stop=None):
    sheets=[]
    for number,part in enumerate(pieces):
        if should_stop and number%40==0 and should_stop():
            raise Cancelled('Yerleşim iptal edildi.')
        x1,y1,x2,y2=part.shape.bounds
        pw,ph=x2-x1,y2-y1
        best=None
        for index,sheet in enumerate(sheets):
            place=sheet.position(pw,ph,gap,rotate,heuristic)
            if place and (best is None or (place[0],index)<(best[0][0],best[1])):
                best=(place,index)
        if best is None:
            sheet=RectangleSheet(width,height,margin)
            # Expand the virtual container by one gap: only adjacent pieces need
            # clearance, not the outside of the already reserved edge margin.
            sheet.free=[(margin,margin,width-2*margin+gap,height-2*margin+gap)]
            option=sheet.position(pw,ph,gap,rotate,heuristic)
            if option is None:  # guarded above
                raise ProcessingError('Dikdörtgen yerleşimi tamamlanamadı.')
            sheets.append(sheet)
        else:
            option,index=best
            sheet=sheets[index]
        sheet.insert(part,option,gap)
    return sheets


def optimize_rectangles(pieces,width,height,margin,gap,rotate,progress=None,should_stop=None):
    def dims(p):
        a,b,c,d=p.shape.bounds;return c-a,d-b
    orders=[lambda p: (-dims(p)[0]*dims(p)[1],-max(dims(p))),
            lambda p: (-max(dims(p)),-min(dims(p))),
            lambda p: (-dims(p)[1],-dims(p)[0]),
            lambda p: (-dims(p)[0],-dims(p)[1])]
    best=None;best_score=None;trial=0;count=len(orders)*4
    for order in orders:
        ordered=sorted(pieces,key=order)
        for heuristic in ('short','area','bottom','long'):
            trial+=1
            if progress: progress(trial,count)
            candidate=pack_order(ordered,width,height,margin,gap,rotate,heuristic,should_stop)
            # Fewer sheets first, then concentrate unused area in the last sheet.
            score=(len(candidate),candidate[-1].net_area if candidate else 0)
            if best_score is None or score<best_score: best,best_score=candidate,score
    return best,count
