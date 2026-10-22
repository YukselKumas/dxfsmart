"""Standalone Turkish DXF smart separator prototype – FastAPI app."""
from __future__ import annotations

import io
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Body
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from engine import (ProcessingError, analyze_dxf, extract_uploads, make_response,
                    part_to_bytes, pack_parts, MAX_UPLOAD_BYTES)
from demo import sample_files
from estimate import estimate_sheets, Cancelled
from planning import estimate_job, planned_parts
from laser_presets import presets_payload, speed_m_min, POWERS_KW

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="DXF Akıllı Ayırıcı", version="0.9.1")


@app.middleware("http")
async def local_origin_only(request, call_next):
    """Block cross-site writes: another web page open in the same browser
    must not be able to POST files to this local server."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                return Response("Yalnızca yerel arayüzden istek kabul edilir.", status_code=403)
    return await call_next(request)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
CACHE = {}
CACHE_LOCK = threading.Lock()
CACHE_SECONDS = 60 * 30
MAX_CACHED_JOBS = 12
NESTING_TASKS={}
NESTING_LOCK=threading.Lock()
NESTING_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='nesting')


def purge_cache():
    now = time.monotonic()
    with CACHE_LOCK:
        for key in [k for k, v in CACHE.items() if now - v["created"] > CACHE_SECONDS]:
            CACHE.pop(key, None)
        # Analysed DXF documents are memory heavy: keep only the newest jobs.
        for key in sorted(CACHE, key=lambda k: CACHE[k]["created"])[:-MAX_CACHED_JOBS]:
            CACHE.pop(key, None)


def analyze_uploads(pairs, tolerance_mm: float):
    if not 0.005 <= tolerance_mm <= 0.5:
        raise HTTPException(400, "Tolerans 0,005–0,5 koordinat birimi arasında olmalı.")
    try:
        items = extract_uploads(pairs)
        results = []
        for name, data in items:
            results.append(analyze_dxf(data, name, tolerance_mm))
        jobid = str(uuid.uuid4())
        with CACHE_LOCK:
            CACHE[jobid] = {"created": time.monotonic(), "results": results}
        purge_cache()
        return make_response(results, tolerance_mm, jobid)
    except ProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # Clear client error, suppress any internal paths/tracebacks.
        raise HTTPException(500, "İşlem sırasında beklenmeyen hata oluştu. Dosyayı kontrol edin.") from exc


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@app.post("/api/analyze")
async def analyze(files: list[UploadFile] = File(...), tolerance: float = Form(0.05)):
    if len(files) > 80:
        raise HTTPException(413, "En fazla 80 dosya yüklenebilir.")
    pairs = []
    total = 0
    for f in files:
        blob = await f.read(MAX_UPLOAD_BYTES + 1)
        total += len(blob)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Toplam yükleme sınırı 40 MB.")
        pairs.append((f.filename or "dosya.dxf", blob))
    # v0.8: CPU-heavy analysis runs in a worker thread so the server keeps
    # answering (health checks, nesting progress) while a big DXF is parsed.
    return await run_in_threadpool(analyze_uploads, pairs, tolerance)


@app.get("/api/demo")
def demo(full: bool = False):
    """full=1: realistic workshop job used by the UI's sample project."""
    if full:
        from demo import showcase_file
        return analyze_uploads([showcase_file()], 0.05)
    return analyze_uploads(sample_files(), 0.05)


@app.get("/api/demo.zip")
def demo_zip():
    stream = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        for name, blob in sample_files():
            output.writestr(name, blob)
    return Response(stream.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="ornek_dxf_paketi.zip"'
    })


def lookup_job(job_id):
    purge_cache()
    with CACHE_LOCK:
        job = CACHE.get(job_id)
    if not job:
        raise HTTPException(404, "Analiz süresi dolmuş. Dosyaları yeniden analiz edin.")
    return job


@app.get("/api/download/{job_id}")
def download(job_id: str, ids: list[str] = Query(default=[]), origin: bool = True):
    job = lookup_job(job_id)
    parts = [p for res in job["results"] for p in res.parts]
    if ids:
        id_set = set(ids)
        parts = [p for p in parts if p.id in id_set]
        if len(parts) != len(id_set):
            raise HTTPException(404, "Seçilen bazı parçalar bulunamadı.")
    if not parts:
        raise HTTPException(422, "İndirilecek parça bulunamadı.")
    try:
        content = pack_parts(parts, origin)
    except ProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(content, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="DXF_Ayirici_Parcalar.zip"',
        "Cache-Control": "no-store",
    })


@app.get("/api/download/{job_id}/{part_id}")
def download_single(job_id: str, part_id: str, origin: bool = True):
    job = lookup_job(job_id)
    for file_result in job["results"]:
        for part in file_result.parts:
            if part.id == part_id:
                try:
                    blob = part_to_bytes(part, origin)
                except ProcessingError as exc:
                    raise HTTPException(422, str(exc)) from exc
                from engine import part_filename
                from urllib.parse import quote
                title = part_filename(part)
                return Response(blob, media_type="application/dxf", headers={
                    "Content-Disposition": f"attachment; filename=part.dxf; filename*=UTF-8''{quote(title)}",
                    "Cache-Control": "no-store"
                })
    raise HTTPException(404, "Parça bulunamadı.")


@app.post("/api/estimate/{job_id}")
def estimate_layout(job_id: str, request: dict = Body(...)):
    job = lookup_job(job_id)
    all_parts = {p.id: p for f in job["results"] for p in f.parts}
    try:
        return estimate_sheets(all_parts, request)
    except ProcessingError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/download-plan/{job_id}')
def download_plan(job_id: str, request: dict = Body(...)):
    job=lookup_job(job_id)
    parts={p.id:p for f in job['results'] for p in f.parts}
    try:
        selected=planned_parts(parts,request)
        if not selected: raise ProcessingError('İndirilecek parça yok; adetleri kontrol edin.')
        content=pack_parts(selected)
    except ProcessingError as exc:
        raise HTTPException(422,str(exc)) from exc
    return Response(content,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="DXF_Parcalar_Adet_Kalinlik.zip"','Cache-Control':'no-store'})


@app.post('/api/planning/{job_id}')
def planning_endpoint(job_id: str, request: dict = Body(...)):
    job=lookup_job(job_id)
    parts={p.id:p for f in job['results'] for p in f.parts}
    try:
        return estimate_job(parts,request)
    except ProcessingError as exc:
        raise HTTPException(422,str(exc)) from exc


ACTIVE_STAGES = ('queued', 'preparing', 'placing', 'finalizing')


def _run_nesting(task_id,parts,request):
    def stopped():
        with NESTING_LOCK:
            return NESTING_TASKS[task_id].get('cancel', False)
    def update(stage,percent,detail=''):
        with NESTING_LOCK:
            NESTING_TASKS[task_id].update(stage=stage,progress=percent,detail=detail)
    try:
        if stopped(): raise Cancelled('Yerleşim iptal edildi.')
        update('preparing',3,'Parçalar ve adetler kontrol ediliyor')
        result=estimate_job(parts,request,update,stopped)
        update('finalizing',96,'Ağırlık ve süre raporu hazırlanıyor')
        with NESTING_LOCK:
            NESTING_TASKS[task_id].update(stage='complete',progress=100,result=result)
    except Cancelled:
        with NESTING_LOCK: NESTING_TASKS[task_id].update(stage='cancelled',detail='Daha yeni bir istek geldi.')
    except ProcessingError as exc:
        with NESTING_LOCK: NESTING_TASKS[task_id].update(stage='error',error=str(exc))
    except Exception:
        with NESTING_LOCK: NESTING_TASKS[task_id].update(stage='error',error='Yerleşim tamamlanamadı. Ölçüleri ve parça geometrilerini kontrol edin.')


@app.post('/api/nesting-jobs/{job_id}')
def start_nesting(job_id: str, request: dict = Body(...)):
    job=lookup_job(job_id)
    parts={p.id:p for f in job['results'] for p in f.parts}
    with NESTING_LOCK:
        now=time.monotonic()
        expired=[k for k,v in NESTING_TASKS.items() if now-v['created']>CACHE_SECONDS and v['stage'] not in ACTIVE_STAGES]
        for k in expired: del NESTING_TASKS[k]
        # v0.8: a new request supersedes older ones (single local user), so
        # the worker is not left busy computing results nobody will see.
        for v in NESTING_TASKS.values():
            if v['stage'] in ACTIVE_STAGES: v['cancel']=True
        task_id=str(uuid.uuid4())
        NESTING_TASKS[task_id]={'stage':'queued','progress':0,'detail':'Sıraya alındı','created':now,'jobId':job_id}
    NESTING_POOL.submit(_run_nesting,task_id,parts,request)
    return {'taskId':task_id}


@app.delete('/api/nesting-jobs/{task_id}')
def cancel_nesting(task_id: str):
    with NESTING_LOCK:
        task=NESTING_TASKS.get(task_id)
        if task is not None and task['stage'] in ACTIVE_STAGES: task['cancel']=True
    return {'cancelled': task is not None}


@app.get('/api/nesting-jobs/{task_id}')
def nesting_status(task_id: str):
    with NESTING_LOCK:
        task=NESTING_TASKS.get(task_id)
        if task is None: raise HTTPException(404,'Yerleşim oturumu bulunamadı; tekrar başlatın.')
        return {k:v for k,v in task.items() if k not in ('created','cancel','jobId')}


@app.get('/api/nesting-jobs/{task_id}/dxf')
def nesting_dxf(task_id: str):
    """All nested sheets as DXF files (native CAD entities, rotated + moved)."""
    from engine import nest_to_dxf
    with NESTING_LOCK:
        task=NESTING_TASKS.get(task_id)
        result=task.get('result') if task else None
        job_id=task.get('jobId') if task else None
    if not result:
        raise HTTPException(404,'Yerleşim sonucu bulunamadı; optimizasyonu tekrar çalıştırın.')
    job=lookup_job(job_id)
    parts={p.id:p for f in job['results'] for p in f.parts}
    out=io.BytesIO()
    import zipfile
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for group in result['groups']:
            if not group.get('sheets'):
                continue
            blob=nest_to_dxf(group['sheets'],parts,result['sheetWidth'],result['sheetHeight'],
                             float(group.get('margin',0) or 0),group['thicknessMm'])
            with zipfile.ZipFile(io.BytesIO(blob)) as inner:
                for name in inner.namelist():
                    z.writestr(name,inner.read(name))
    return Response(out.getvalue(),media_type='application/zip',headers={
        'Content-Disposition':'attachment; filename="DXFsmart_Yerlesim_Plakalari.zip"','Cache-Control':'no-store'})


@app.get('/api/part/{job_id}/{part_id}')
def part_inspect(job_id: str, part_id: str):
    from engine import part_detail
    job=lookup_job(job_id)
    for f in job['results']:
        for p in f.parts:
            if p.id==part_id:
                return part_detail(p)
    raise HTTPException(404,'Parça bulunamadı.')


@app.get('/api/laser-presets')
def laser_presets():
    return presets_payload()


@app.get('/api/laser-speeds')
def laser_speeds(material: str = 'steel', kw: float = 3, thickness: list[float] = Query(default=[])):
    if kw not in POWERS_KW:
        raise HTTPException(422,'Bu güç için tablo yok.')
    return {'material':material,'kw':kw,'speeds':{f'{t:g}':speed_m_min(material,kw,t) for t in thickness}}


@app.get("/api/health")
def health():
    return {"status": "ok", "engine": "ezdxf + shapely", "version": "0.9.1"}


