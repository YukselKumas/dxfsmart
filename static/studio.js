'use strict';
/* DXFsmart 0.9 — workstation UI */
const $ = (q, el = document) => el.querySelector(q);
const $$ = (q, el = document) => [...el.querySelectorAll(q)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, max = 1, min = 0) => Number(n || 0).toLocaleString('tr-TR', { minimumFractionDigits: min, maximumFractionDigits: max });
const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
const apiError = async r => { try { const j = await r.json(); return j.detail || 'İşlem başarısız.'; } catch (_) { return `Sunucu hatası (${r.status}).`; } };
const duration = m => { const t = Math.max(0, Math.round(m)); return t >= 60 ? `${Math.floor(t / 60)} sa ${t % 60} dk` : `${t} dk`; };
const csvNum = v => typeof v === 'number' ? String(v).replace('.', ',') : v;

const state = {
  data: null, groups: [], filter: 'all', partById: new Map(),
  plan: null, groupIndex: 0, sheetIndex: 0, view: 'nest',
  taskId: null, lastTaskId: null, requestCount: 0, loading: false,
  method: 'shape', presets: null, hoverKey: null,
};

/* ------------------------------------------------------------ status */
function status(msg, type = '') { const s = $('#status'); s.textContent = msg || ''; s.className = 'status ' + type; }

/* ------------------------------------------------------------ geometry helpers */
const pathOf = (rings, flipY = false) => rings.filter(r => r && r.length).map(r =>
  'M' + r.map(p => `${+p[0].toFixed(3)} ${flipY ? +(-p[1]).toFixed(3) : +p[1].toFixed(3)}`).join('L') + 'Z').join('');

function thumbSvg(part) {
  const [x1, y1, x2, y2] = part.bounds, w = Math.max(1e-6, x2 - x1), h = Math.max(1e-6, y2 - y1), pad = Math.max(w, h) * .06;
  return `<svg viewBox="${x1 - pad} ${-y2 - pad} ${w + 2 * pad} ${h + 2 * pad}" preserveAspectRatio="xMidYMid meet" aria-hidden="true"><path d="${pathOf([part.outline, ...part.inner], true)}"/></svg>`;
}

/* ------------------------------------------------------------ upload */
async function analyze(files) {
  if (state.loading || !files.length) return;
  const ok = [...files].filter(f => /\.(dxf|zip)$/i.test(f.name));
  if (!ok.length) { status('Yalnızca .dxf veya .zip dosyası yüklenebilir.', 'error'); return; }
  if (ok.reduce((s, f) => s + f.size, 0) > 40 * 1024 * 1024) { status('Toplam boyut 40 MB sınırını aşıyor. Dosyaları bölerek yükle.', 'error'); return; }
  state.loading = true; status(`${ok.length} dosya ayrıştırılıyor…`);
  try {
    const form = new FormData(); ok.forEach(f => form.append('files', f, f.name)); form.append('tolerance', '0.05');
    const r = await fetch('/api/analyze', { method: 'POST', body: form });
    if (!r.ok) throw Error(await apiError(r));
    showData(await r.json());
  } catch (e) { status('Ayrıştırılamadı: ' + e.message, 'error'); }
  finally { state.loading = false; $('#fileInput').value = ''; }
}
async function demo() {
  if (state.loading) return;
  state.loading = true; status('Örnek proje açılıyor…');
  try {
    const r = await fetch('/api/demo?full=1'); if (!r.ok) throw Error(await apiError(r));
    if ($('#defaultThickness').value === '') $('#defaultThickness').value = '3';
    showData(await r.json());
    // a realistic order size: every drawn part ×5
    state.groups.forEach(g => { g.quantity = g.quantity * 5; }); renderParts();
    const total = state.groups.reduce((n, g) => n + g.quantity, 0);
    $('#projectMeta').textContent = `${state.groups.length} farklı parça, ${fmt(total, 0)} adet`;
    status('Örnek atölye işi açıldı: çizimdeki adetler 5 ile çarpıldı, kalınlık yazmadığı için 3 mm varsayıldı. Soldan ve sağdan değiştirebilirsin.');
  }
  catch (e) { status('Örnek açılamadı: ' + e.message, 'error'); }
  finally { state.loading = false; }
}

function deriveGroups(parts) {
  const buckets = new Map();
  for (const p of parts) {
    const key = p.duplicateGroup ? 'G' + p.duplicateGroup : 'P' + p.id;
    if (!buckets.has(key)) buckets.set(key, { key, primary: p, all: [], quantity: 0 });
    const g = buckets.get(key); g.all.push(p); g.quantity += p.quantity ?? 1;
  }
  return [...buckets.values()].sort((a, b) => a.primary.file.localeCompare(b.primary.file, 'tr') || a.primary.number - b.primary.number);
}

function showData(data) {
  cancelTask();
  state.data = data; state.plan = null; state.lastTaskId = null; state.groupIndex = 0; state.sheetIndex = 0;
  state.groups = deriveGroups(data.parts);
  state.partById = new Map(data.parts.map(p => [p.id, p]));
  state.filter = 'all'; $('#partSearch').value = '';
  $$('#filters button').forEach(b => b.classList.toggle('on', b.dataset.filter === 'all'));
  document.body.classList.add('has-project');
  $('#drop').hidden = true; $('#workspace').hidden = false;
  $('#job').hidden = false; $('#exportMenu').hidden = false; $('#reset').hidden = false;
  $('#projectTitle').textContent = data.files.length === 1 ? data.files[0].name.replace(/\.dxf$/i, '') : `${data.files.length} dosyalık proje`;
  const q = state.groups.reduce((s, g) => s + g.quantity, 0);
  $('#projectMeta').textContent = `${state.groups.length} farklı parça, ${fmt(q, 0)} adet`;
  $('#confirmMillimeters').checked = data.files.every(f => f.units === 4) ? false : $('#confirmMillimeters').checked;
  const crit = data.summary.critical;
  $('#issueBadge').textContent = data.issues.length || '';
  $('#issueBadge').className = 'badge' + (crit ? ' danger' : '');
  $('#calculate').disabled = false;
  $('#downloadNest').disabled = true;
  renderParts(); renderReport(); renderIdle(); buildSpeedRows();
  const units = data.files.some(f => f.units !== 4);
  status(`${data.summary.files} dosya tarandı, ${data.summary.parts} kapalı parça bulundu${data.summary.critical ? `, ${data.summary.critical} kritik bulgu var (Kontrol raporu)` : ''}.${units ? ' Birim tanımsız: ölçüler mm ise sağdaki “Çizim birimi mm” kutusunu işaretle.' : ''}`);
}

/* ------------------------------------------------------------ parts list */
function groupTags(g) {
  const p = g.primary, t = [];
  if (p.exportMode === 'sampled') t.push('<span class="tag danger">Polyline aktarım</span>');
  if (p.review && p.exportMode !== 'sampled') t.push('<span class="tag warn">Kontrol et</span>');
  if (g.all.length > 1) t.push(`<span class="tag repeat">Tekrar ×${g.all.length}</span>`);
  if (g.all.some(x => x.mirrorNote)) t.push('<span class="tag warn">Ayna eşi</span>');
  if (p.exportMode === 'trimmed') t.push('<span class="tag">Ortak kenar</span>');
  return t.join('');
}
function visibleGroups() {
  const q = $('#partSearch').value.trim().toLocaleLowerCase('tr-TR');
  return state.groups.filter(g =>
    (state.filter === 'all' || (state.filter === 'duplicate' && g.all.length > 1) || (state.filter === 'review' && g.all.some(p => p.review || p.mirrorNote))) &&
    (!q || `${g.primary.name} ${g.primary.file}`.toLocaleLowerCase('tr-TR').includes(q)));
}
function renderParts() {
  const gs = visibleGroups();
  $('#partTotal').textContent = state.groups.length ? `${state.groups.length}` : '';
  $('#parts').innerHTML = gs.map(g => {
    const p = g.primary, unit = p.units === 4 ? 'mm' : 'birim';
    const th = p.thicknessMm == null ? 'kalınlık ?' : `${fmt(p.thicknessMm, 2)} mm`;
    return `<li class="part-row" data-key="${esc(g.key)}" tabindex="0" aria-label="${esc(p.name)} ayrıntıları">
      <div class="thumb">${thumbSvg(p)}</div>
      <div class="pr-main"><div class="pr-name">${esc(p.name)} ${groupTags(g)}</div>
        <div class="pr-dims">${fmt(p.width, 1)} × ${fmt(p.height, 1)} ${unit}, ${th}${p.holes ? `, ${p.holes} delik` : ''}</div>
        <div class="pr-dims" title="${esc(g.all.map(x => x.file).join(', '))}">${esc(p.file)}</div></div>
      <div class="qty" data-stop><button type="button" data-qty-minus="${esc(g.key)}" aria-label="Azalt">−</button><input data-qty="${esc(g.key)}" type="number" min="0" max="1200" value="${g.quantity}" aria-label="${esc(p.name)} adedi"><button type="button" data-qty-plus="${esc(g.key)}" aria-label="Artır">+</button></div>
    </li>`;
  }).join('') || '<li class="pane-foot">Bu filtrede parça yok.</li>';
}
function changeQty(key, action, value) {
  const g = state.groups.find(x => x.key === key); if (!g) return;
  if (action === 'set') { if (value === '') return; g.quantity = clamp(parseInt(value, 10) || 0, 0, 1200); }
  if (action === 'plus') g.quantity = clamp(g.quantity + 1, 0, 1200);
  if (action === 'minus') g.quantity = clamp(g.quantity - 1, 0, 1200);
  const input = $$('[data-qty]').find(el => el.dataset.qty === key); if (input) input.value = g.quantity;
  invalidate('Adetler değişti.');
}

/* ------------------------------------------------------------ settings */
function thicknesses() {
  if (!state.data) return [];
  const set = new Set(state.data.parts.map(p => p.thicknessMm).filter(t => t != null));
  const d = parseFloat($('#defaultThickness').value);
  if (state.data.parts.some(p => p.thicknessMm == null) && d > 0) set.add(d);
  return [...set].sort((a, b) => a - b);
}
async function loadPresets() {
  try {
    const r = await fetch('/api/laser-presets'); if (!r.ok) return;
    state.presets = await r.json();
    $('#laserKw').insertAdjacentHTML('beforeend', state.presets.powers.map(k => `<option value="${k}">${fmt(k, 1)} kW fiber lazer</option>`).join(''));
    let saved = null; try { saved = localStorage.getItem('dxfsmart.kw'); } catch (_) { }
    if (saved && state.presets.powers.map(String).includes(saved)) $('#laserKw').value = saved;
    buildSpeedRows();
  } catch (_) { }
}
function buildSpeedRows() {
  const ths = thicknesses();
  const kw = $('#laserKw').value;
  if (!ths.length) { $('#speedTable').innerHTML = ''; updateSpeedNote(); return; }
  const old = Object.fromEntries($$('[data-speed]').filter(i => i.dataset.edited).map(i => [i.dataset.speed, i.value]));
  $('#speedTable').innerHTML = `<div class="speed-row hint"><span>Kalınlık</span><span>Kesim hızı (m/dk)</span><span></span></div>` + ths.map(t => {
    const key = String(+t.toFixed(3));
    const edited = old[key] !== undefined;
    return `<div class="speed-row"><b>${fmt(t, 2)} mm</b><input type="number" min="0.01" max="200" step="0.1" data-speed="${key}" ${edited ? `value="${esc(old[key])}" data-edited="1" class="edited"` : ''} placeholder="${kw ? '…' : 'genel hız'}" aria-label="${fmt(t, 2)} mm kesim hızı"><span data-src="${key}">${edited ? 'düzenlendi' : ''}</span></div>`;
  }).join('');
  fillTableSpeeds();
}
async function fillTableSpeeds() {
  const kw = $('#laserKw').value, mat = $('#material').value, ths = thicknesses();
  updateSpeedNote();
  if (!kw || !ths.length) { $$('[data-speed]').forEach(i => { if (!i.dataset.edited) { i.value = ''; i.placeholder = 'genel hız'; $(`[data-src="${i.dataset.speed}"]`).textContent = ''; } }); return; }
  try {
    const params = new URLSearchParams({ material: mat, kw }); ths.forEach(t => params.append('thickness', t));
    const r = await fetch('/api/laser-speeds?' + params); if (!r.ok) return;
    const { speeds } = await r.json();
    $$('[data-speed]').forEach(i => {
      if (i.dataset.edited) return;
      const v = speeds[i.dataset.speed];
      const src = $(`[data-src="${i.dataset.speed}"]`);
      if (v) { i.value = +v.toFixed(2); src.textContent = 'tablo'; }
      else { i.value = ''; i.placeholder = 'tablo dışı'; src.textContent = 'genel hız'; }
    });
  } catch (_) { }
}
function updateSpeedNote() {
  const kw = $('#laserKw').value, p = state.presets;
  if (!kw || !p) { $('#speedNote').innerHTML = 'Lazer gücünü seçersen her kalınlık için tipik hız gelir; istediğini kendi makine değerinle değiştir.'; return; }
  const gas = p.table[$('#material').value]?.gas || '';
  $('#speedNote').innerHTML = `${esc(fmt(+kw, 1))} kW, ${esc(gas)} için ${esc(p.notes[kw] || '')}. ${esc(p.disclaimer)} Kaynaklar: ${p.sources.map((s, i) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${i + 1}</a>`).join(', ')}.`;
}
function requestBody() {
  const kw = $('#laserKw').value;
  const speeds = {};
  $$('[data-speed]').forEach(i => { if (i.value !== '' && (i.dataset.edited || !kw)) speeds[i.dataset.speed] = Number(i.value) * 1000; });
  return {
    material: $('#material').value, sheetWidth: Number($('#sheetWidth').value), sheetHeight: Number($('#sheetHeight').value),
    confirmMillimeters: $('#confirmMillimeters').checked,
    defaultThickness: $('#defaultThickness').value === '' ? null : Number($('#defaultThickness').value),
    method: state.method, rotation: $('#rotation').value, quality: $('#quality').value,
    gap: Number($('#gap').value), margin: Number($('#margin').value), partInPart: $('#partInPart').checked,
    allowRotation: $('#rotation').value !== 'none', targetEfficiency: 80,
    laserKw: kw ? Number(kw) : null, cuttingSpeeds: speeds,
    cuttingSpeed: Number($('#cuttingSpeed').value) * 1000, pierceSeconds: Number($('#pierceSeconds').value), overheadPercent: Number($('#overheadPercent').value),
    items: state.groups.map(g => ({ id: g.primary.id, quantity: g.quantity })),
  };
}
function invalidate(reason) {
  if (!state.data) return;
  cancelTask(); state.requestCount++;
  const had = !!state.plan; state.plan = null; $('#downloadNest').disabled = true;
  $('#calculate').disabled = false; $('#calculate').textContent = 'Yerleşimi hesapla';
  renderIdle(had ? `${reason} Güncel plaka, kg ve süre için yerleşimi yeniden hesapla.` : '');
}
function cancelTask() {
  if (!state.taskId) return;
  const id = state.taskId; state.taskId = null;
  fetch(`/api/nesting-jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => { });
}

/* ------------------------------------------------------------ nesting run */
function renderIdle(message = '') {
  if (state.view !== 'nest') return;
  $('#runHint').textContent = state.data ? 'Ayarları seç, sonra hesapla. Sonuç bu ekranda açılır.' : 'Önce bir çizim yükle.';
  $('#viewNest').innerHTML = `<div class="idle"><h3>${message ? 'Ayarlar değişti' : 'Yerleşim için hazır'}</h3>
    <p>${esc(message || 'Sağdan malzeme, sac ve makine ayarlarını kontrol et; adetleri soldaki listeden düzenle. Hesapla dediğinde parçalar gerçek konturlarıyla, seçtiğin açılarda birbirine geçirilerek yerleştirilir.')}</p></div>`;
}
function showProgress(s, taskId) {
  const pct = clamp(s.progress || 0, 0, 100);
  const title = { queued: 'Sırada', preparing: 'Parçalar hazırlanıyor', placing: 'Parçalar yerleştiriliyor', finalizing: 'Rapor hazırlanıyor' }[s.stage] || 'Hazırlanıyor';
  let card = $('#viewNest .progress-card');
  if (!card) {
    $('#viewNest').innerHTML = `<div class="progress-card" role="status" aria-live="polite"><h3 class="pc-title"></h3>
      <p class="pc-detail"></p>
      <div class="heat" role="progressbar" aria-valuemin="0" aria-valuemax="100"><i></i></div>
      <span class="heat-label"></span>
      <figure class="live-sim" hidden><figcaption><b>Canlı simülasyon</b> · optimizer parçaları her adımda yeniden diziyor; kırmızı kesik çizgiler o an çözülmekte olan çakışmalar. Her satır bir plaka.</figcaption>
        <div class="live-frame"><img alt="Canlı yerleşim denemesi"></div></figure></div>`;
    card = $('#viewNest .progress-card');
  }
  card.querySelector('.pc-title').textContent = title;
  card.querySelector('.pc-detail').textContent = s.detail || 'Açılar ve parça sıraları karşılaştırılıyor.';
  card.querySelector('.heat').setAttribute('aria-valuenow', pct);
  card.querySelector('.heat i').style.width = pct + '%';
  card.querySelector('.heat-label').textContent = `%${pct}. Ayar değiştirirsen bu hesap durur.`;
  if (s.live && taskId) pullLive(taskId, card);
}
let liveBusy = false, liveUrl = null;
async function pullLive(taskId, card) {
  if (liveBusy) return; liveBusy = true;
  try {
    const r = await fetch(`/api/nesting-jobs/${encodeURIComponent(taskId)}/live`, { cache: 'no-store' });
    if (r.status !== 200) return;
    const blob = await r.blob(), url = URL.createObjectURL(blob), img = new Image();
    img.onload = () => {
      const fig = card.querySelector('.live-sim'), target = fig && fig.querySelector('img');
      if (!target) { URL.revokeObjectURL(url); return; }
      fig.hidden = false; target.src = url;
      if (liveUrl) URL.revokeObjectURL(liveUrl); liveUrl = url;
    };
    img.src = url;
  } catch (e) { /* the live view is optional */ } finally { liveBusy = false; }
}
async function calculate() {
  if (!state.data) return;
  cancelTask();
  const idx = ++state.requestCount, body = requestBody(), btn = $('#calculate');
  btn.disabled = true; btn.textContent = 'Hesaplanıyor…'; setView('nest');
  showProgress({ stage: 'preparing', progress: 0 });
  try {
    const r = await fetch(`/api/nesting-jobs/${encodeURIComponent(state.data.jobId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) throw Error(await apiError(r));
    const job = await r.json(); state.taskId = job.taskId;
    while (idx === state.requestCount) {
      const poll = await fetch(`/api/nesting-jobs/${encodeURIComponent(job.taskId)}`);
      if (!poll.ok) throw Error(await apiError(poll));
      const s = await poll.json();
      if (idx !== state.requestCount) return;
      if (s.stage === 'error') throw Error(s.error);
      if (s.stage === 'cancelled') return;
      if (s.stage === 'complete') {
        state.taskId = null; state.lastTaskId = job.taskId; state.plan = s.result; state.groupIndex = 0; state.sheetIndex = 0;
        $('#downloadNest').disabled = false; renderPlan(); status('Yerleşim hazır. Bir parçaya tıklayarak ölçülerini kontrol edebilirsin.');
        break;
      }
      showProgress(s, job.taskId); await new Promise(res => setTimeout(res, 450));
    }
  } catch (e) {
    if (idx === state.requestCount) $('#viewNest').innerHTML = `<div class="alert"><b>Yerleşim hesaplanamadı.</b>${esc(e.message)}</div>`;
  } finally {
    if (idx === state.requestCount) { btn.disabled = false; btn.textContent = 'Yerleşimi yeniden hesapla'; }
  }
}

/* ------------------------------------------------------------ results */
function renderPlan() {
  const e = state.plan; if (!e) return;
  if (!e.totalQuantity) { renderIdle('Tüm adetler sıfır. En az bir parçanın adedini artır.'); return; }
  const gross = e.totalGrossSheetKg, yieldPct = gross ? 100 * e.totalNetKg / gross : null;
  const saved = e.totalRectSheets != null && e.totalSheets != null ? e.totalRectSheets - e.totalSheets : 0;
  let verdict = '';
  if (e.method === 'shape' && e.totalSheets != null) {
    verdict = saved > 0 ? `Gerçek kontur yerleşimi, parçaları dikdörtgen kutu gibi dizen yönteme göre ${saved} plaka daha az kullanıyor (${e.totalRectSheets} yerine ${e.totalSheets}).`
      : `Plaka sayısı dikdörtgen yöntemle aynı (${e.totalSheets}); fark son plakada kalan şeritte.`;
  }
  $('#viewNest').innerHTML = `
    <dl class="figures">
      <div class="fig lead"><dt>Plaka</dt><dd>${e.totalSheets == null ? '—' : fmt(e.totalSheets, 0)}<small>adet</small></dd><div class="sub">${e.groups.length} kalınlık ayrı yerleşti</div></div>
      <div class="fig"><dt>Brüt sac</dt><dd>${gross == null ? '—' : fmt(gross, 1)}<small>kg</small></dd><div class="sub">alınacak tam plakalar</div></div>
      <div class="fig"><dt>Net parça</dt><dd>${fmt(e.totalNetKg, 1)}<small>kg</small></dd><div class="sub">${fmt(e.totalQuantity, 0)} adet dahil</div></div>
      <div class="fig"><dt>Malzeme verimi</dt><dd>${yieldPct == null ? '—' : '%' + fmt(yieldPct, 1)}</dd><div class="sub">net / brüt ağırlık</div></div>
      <div class="fig"><dt>Kesim süresi</dt><dd>${duration(e.estimatedMinutes)}</dd><div class="sub">${fmt(e.cutLengthM, 0)} m kesim, ${fmt(e.pierceCount, 0)} delme</div></div>
    </dl>
    ${verdict ? `<p class="verdict">${esc(verdict)}</p>` : ''}
    <div class="board-bar"><div class="th-chips" role="tablist">${e.groups.map((g, i) => `<button class="chip ${i === state.groupIndex ? 'on' : ''}" data-group="${i}" type="button">${fmt(g.thicknessMm, 2)} mm, ${g.recommendedSheets == null ? 'sığmıyor' : g.recommendedSheets + ' plaka'}</button>`).join('')}</div>
      <span class="board-meta" id="boardMeta"></span></div>
    <div id="groupView"></div>`;
  renderGroup();
}
function renderGroup() {
  const e = state.plan, g = e?.groups[state.groupIndex]; if (!g) return;
  if (g.recommendedSheets == null) {
    $('#groupView').innerHTML = `<div class="alert"><b>Bu kalınlıkta sığmayan parça var.</b>${(g.oversized || []).map(o => esc(`${o.name}: ${fmt(o.width, 0)} × ${fmt(o.height, 0)} mm`)).join('<br>')}<br>Daha büyük sac seç ya da döndürmeye izin ver.</div>`;
    $('#boardMeta').textContent = ''; return;
  }
  state.sheetIndex = clamp(state.sheetIndex, 0, g.sheets.length - 1);
  const sh = g.sheets[state.sheetIndex];
  const sn = g.shapeNesting;
  $('#boardMeta').textContent = `${g.methodLabel}, %${fmt(g.effectiveUtilization, 1)} etkin doluluk, ${fmt(g.cuttingSpeed / 1000, 2)} m/dk (${g.speedSource})`;
  const longSide = Math.max(e.sheetWidth, e.sheetHeight);
  $('#groupView').innerHTML = `<div class="sheet-wrap">
      ${sheetSvg(g, sh, 'sheet-svg', true, state.sheetIndex === g.sheets.length - 1)}
      <div class="sheet-foot"><span>Plaka ${state.sheetIndex + 1}: ${sh.quantity} parça</span>
        <span class="util-pair" title="Net: parçaların gerçek alanı. Kaplanan: parça dış konturlarının kapladığı alan (parçanın kendi delikleri dahil).">Net doluluk <b>%${fmt(sh.netUtilization, 1)}</b> Kaplanan alan <b>%${fmt(sh.footprintUtilization ?? sh.netUtilization, 1)}</b></span>
        <div class="sheet-nav"><button type="button" id="sheetPrev" aria-label="Önceki plaka">‹</button><span>${state.sheetIndex + 1} / ${g.sheets.length}</span><button type="button" id="sheetNext" aria-label="Sonraki plaka">›</button></div></div>
      ${g.sheets.length > 1 ? `<div class="thumbs">${g.sheets.map((s, i) => `<button type="button" class="sheet-thumb ${i === state.sheetIndex ? 'on' : ''}" data-sheet="${i}">${sheetSvg(g, s, 'sheet-svg mini', false, false)}<span>${i + 1}. plaka, %${fmt(s.netUtilization, 0)}</span></button>`).join('')}</div>` : ''}
    </div>
    <p class="method-note">Son plakada ${fmt(g.lastSheetUsedMm, 0)} mm kullanıldı; ${fmt(g.lastSheetRemnantMm, 0)} mm × ${fmt(Math.min(e.sheetWidth, e.sheetHeight), 0)} mm şerit artık sac olarak kalır (sarı kesikli çizgi).
    ${sn ? (sn.engine === 'sparrow'
      ? ` Kazanan: sparrow optimizer, “${esc(sn.strategy)}” (${fmt(sn.seconds, 1)} sn; ${fmt(g.gap, 1)} mm aralık kesin geometriyle doğrulandı).`
      : ` ${sn.tried.length} strateji denendi, en iyisi “${esc(sn.strategy)}” (${fmt(sn.seconds, 1)} sn, ${fmt(sn.cellMm, 1)} mm arama ızgarası; ${fmt(g.gap, 1)} mm aralık doğrulandı).`)
      + (sn.compared && sn.compared.length > 1 ? ` Karşılaştırma: ${sn.compared.map(c => `${c.engine === 'sparrow' ? 'sparrow' : 'DXFsmart raster'} ${c.sheets} plaka, son plaka ${fmt(c.lastUsedMm, 0)} mm`).join(' · ')}.` : '') : ''}
    Dikdörtgen yöntemle bu kalınlık ${g.rectSheets} plaka. Süreye sac yükleme ve boşaltma dahil değil.</p>`;
  $('#sheetPrev').disabled = state.sheetIndex === 0; $('#sheetNext').disabled = state.sheetIndex === g.sheets.length - 1;
}
function sheetSvg(g, sheet, cls, interactive, showRemnant) {
  const W = state.plan.sheetWidth, H = state.plan.sheetHeight, landscape = H >= W;
  const vb = landscape ? `0 0 ${H} ${W}` : `0 0 ${W} ${H}`;
  const tf = landscape ? 'matrix(0 1 1 0 0 0)' : `matrix(1 0 0 -1 0 ${H})`;
  const m = g.margin || 0;
  const paths = sheet.placements.map(pl => {
    const part = state.partById.get(pl.id); if (!part) return '';
    const grp = state.groups.find(x => x.primary.id === pl.id);
    return `<path class="cut${interactive && state.hoverKey && grp && grp.key === state.hoverKey ? ' hl' : ''}" data-part="${esc(pl.id)}" d="${pathOf([part.outline, ...part.inner])}" transform="translate(${pl.tx} ${pl.ty}) rotate(${pl.angle})">${interactive ? `<title>${esc(part.name)}, ${fmt(pl.angle, 0)}°</title>` : ''}</path>`;
  }).join('');
  let remnant = '';
  if (showRemnant && g.lastSheetUsedMm) {
    const u = g.lastSheetUsedMm + (g.gap || 0) / 2;
    remnant = landscape ? `<line class="remnant" x1="0" y1="${u}" x2="${W}" y2="${u}"/>` : `<line class="remnant" x1="${u}" y1="0" x2="${u}" y2="${H}"/>`;
  }
  return `<svg class="${cls}" viewBox="${vb}" preserveAspectRatio="xMidYMid meet" ${interactive ? 'aria-label="Plaka yerleşimi"' : 'aria-hidden="true"'}><g transform="${tf}">
    <rect class="bed" x="0" y="0" width="${W}" height="${H}" vector-effect="non-scaling-stroke"/>
    ${m > 0 && interactive ? `<rect class="margin-line" x="${m}" y="${m}" width="${W - 2 * m}" height="${H - 2 * m}" vector-effect="non-scaling-stroke"/>` : ''}
    ${paths}${remnant}</g></svg>`;
}

/* ------------------------------------------------------------ report */
function renderReport() {
  const d = state.data; if (!d) return;
  const issues = d.issues.map(i => `<div class="issue ${i.severity === 'danger' ? 'danger' : ''}"><i>${i.severity === 'danger' ? '!' : 'i'}</i><span><b>${esc(i.file)}</b>: ${esc(i.message)}</span></div>`).join('');
  $('#viewReport').innerHTML = `<div class="report">
    <section><h3>Geometri bulguları</h3>${issues || '<p class="hint">Temel geometri denetiminde bulgu yok.</p>'}</section>
    <section><h3>Dosyalar</h3>${d.files.map(f => `<div class="file-line"><span>${esc(f.name)}</span><span>${f.parts} parça, ${fmt(f.entities, 0)} nesne, birim: ${f.units === 4 ? 'mm' : f.units === 0 ? 'tanımsız' : f.units}</span></div>`).join('')}</section>
    <p class="hint">${esc(d.disclaimer)}</p></div>`;
}
function setView(v) {
  state.view = v;
  $$('.stage-tabs [data-view]').forEach(t => t.classList.toggle('on', t.dataset.view === v));
  $('#viewNest').hidden = v !== 'nest'; $('#viewReport').hidden = v !== 'report';
}

/* ------------------------------------------------------------ exports */
async function downloadBlob(url, opts, name) {
  try {
    const r = await fetch(url, opts); if (!r.ok) throw Error(await apiError(r));
    const href = URL.createObjectURL(await r.blob()), a = document.createElement('a');
    a.href = href; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(href), 2000);
  } catch (e) { status('İndirilemedi: ' + e.message, 'error'); }
}
function csvDownload() {
  if (!state.data) return;
  const rows = [['Parça', 'Dosya', 'Genişlik (mm)', 'Yükseklik (mm)', 'Delik', 'Net alan (mm²)', 'Tekrar', 'Adet', 'Kalınlık (mm)', 'Tek parça kg', 'Toplam kg', 'Ayna', 'Aktarım notu']];
  state.groups.forEach(g => {
    const p = g.primary, m = state.plan?.items.find(x => x.id === p.id);
    rows.push([p.name, p.file, p.width, p.height, p.holes, p.area, g.all.length, g.quantity, p.thicknessMm ?? 'Belirsiz', m?.unitKg ?? '', m?.totalKg ?? '', p.mirrorNote ? 'Ayna eşi var' : '',
      p.exportMode === 'sampled' ? 'Polyline aktarım' : p.exportMode === 'trimmed' ? 'Ortak kenar kırpıldı' : ''].map(csvNum));
  });
  const csv = '﻿' + rows.map(l => l.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(';')).join('\r\n');
  const href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })), a = document.createElement('a');
  a.href = href; a.download = 'DXFsmart_Parca_Listesi.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(href), 2000);
}

/* ============================================================ inspector */
const insp = { detail: null, list: [], index: 0, tool: 'pan', measures: [], pending: null, hover: null, hl: null, view: null, drag: null, segs: [] };

async function openInspector(partId) {
  const group = state.groups.find(g => g.all.some(p => p.id === partId)); if (!group) return;
  insp.list = visibleGroups(); if (!insp.list.includes(group)) insp.list = state.groups;
  insp.index = insp.list.indexOf(group);
  await loadInspector(group);
}
async function loadInspector(group) {
  try {
    const r = await fetch(`/api/part/${encodeURIComponent(state.data.jobId)}/${encodeURIComponent(group.primary.id)}`);
    if (!r.ok) throw Error(await apiError(r));
    insp.detail = await r.json(); insp.group = group;
  } catch (e) { status('Parça açılamadı: ' + e.message, 'error'); return; }
  insp.measures = []; insp.pending = null; insp.hover = null; insp.hl = null;
  const d = insp.detail;
  insp.segs = [];
  for (const ring of [d.outline, ...d.inner]) for (let i = 0; i + 1 < ring.length; i++) insp.segs.push([ring[i][0], ring[i][1], ring[i + 1][0], ring[i + 1][1]]);
  $('#inspector').hidden = false; document.body.style.overflow = 'hidden';
  $$('.part-row').forEach(r => r.classList.toggle('active', r.dataset.key === group.key));
  $('#inspTitle').textContent = d.name;
  $('#inspSub').textContent = `${d.file}${group.all.length > 1 ? `, ${group.all.length} aynı parça` : ''}, planlanan ${group.quantity} adet`;
  $('#prevPart').disabled = insp.index <= 0; $('#nextPart').disabled = insp.index >= insp.list.length - 1;
  requestAnimationFrame(() => { fitInspector(); renderInspSide(); renderInsp(); $('#inspSvg').focus(); });
}
function closeInspector() { $('#inspector').hidden = true; document.body.style.overflow = ''; $$('.part-row').forEach(r => r.classList.remove('active')); }

function fitInspector() {
  const d = insp.detail, el = $('#inspCanvas');
  const [x1, y1, x2, y2] = d.bounds, w = Math.max(1e-6, x2 - x1), h = Math.max(1e-6, y2 - y1);
  const cw = el.clientWidth || 800, ch = el.clientHeight || 600;
  insp.view = { cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, scale: Math.min(cw / (w * 1.35), ch / (h * 1.35)) };
  renderInsp();
}
function viewBox() {
  const el = $('#inspCanvas'), cw = el.clientWidth || 800, ch = el.clientHeight || 600, v = insp.view;
  const w = cw / v.scale, h = ch / v.scale;
  return { x: v.cx - w / 2, y: -v.cy - h / 2, w, h, cw, ch };
}
function toWorld(ev) {
  const r = $('#inspSvg').getBoundingClientRect(), vb = viewBox();
  return [vb.x + (ev.clientX - r.left) / r.width * vb.w, -(vb.y + (ev.clientY - r.top) / r.height * vb.h)];
}
function snapPoint(w, constrainFrom) {
  const thr = 12 / insp.view.scale;
  let best = null, bd = thr;
  for (const s of insp.detail.snaps) { const dd = Math.hypot(s.x - w[0], s.y - w[1]); if (dd < bd) { bd = dd; best = { x: s.x, y: s.y, kind: s.kind }; } }
  if (!best) {
    let ed = thr;
    for (const [ax, ay, bx, by] of insp.segs) {
      const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy; if (!L) continue;
      const t = clamp(((w[0] - ax) * dx + (w[1] - ay) * dy) / L, 0, 1), px = ax + t * dx, py = ay + t * dy, dd = Math.hypot(px - w[0], py - w[1]);
      if (dd < ed) { ed = dd; best = { x: px, y: py, kind: 'kenar' }; }
    }
  }
  const p = best || { x: w[0], y: w[1], kind: '' };
  if (constrainFrom) {
    if (Math.abs(p.x - constrainFrom.x) >= Math.abs(p.y - constrainFrom.y)) p.y = constrainFrom.y; else p.x = constrainFrom.x;
    p.kind = p.kind ? p.kind + ', dik' : 'dik';
  }
  return p;
}
const P = (x, y) => `${+x.toFixed(4)},${+(-y).toFixed(4)}`;

function renderInsp() {
  const d = insp.detail; if (!d || !insp.view) return;
  const vb = viewBox(), px = 1 / insp.view.scale, fs = 12.5 * px;
  const svg = $('#inspSvg');
  svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
  svg.classList.toggle('measuring', insp.tool === 'measure');
  let out = `<path class="body" d="${pathOf([d.outline, ...d.inner], true)}"/>`;
  d.holes.forEach((h, i) => { out += `<path class="${insp.hl === i ? 'hole-hl' : 'hole-hit'}" data-hole="${i}" d="${pathOf([d.inner[i]], true)}"/>`; });
  if ($('#showDims').checked) out += dimsSvg(d, px, fs);
  // measurements
  const mLabel = (a, b, text) => {
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, tw = text.length * fs * .56 + fs * .9, th = fs * 1.55;
    return `<rect class="m-bg" x="${mx - tw / 2}" y="${-my - th / 2}" width="${tw}" height="${th}" rx="${fs * .3}"/><text class="m-text" x="${mx}" y="${-my + fs * .36}" font-size="${fs}" text-anchor="middle">${esc(text)}</text>`;
  };
  insp.measures.forEach(m => {
    out += `<line class="m-line" x1="${m.a.x}" y1="${-m.a.y}" x2="${m.b.x}" y2="${-m.b.y}"/><circle class="m-pt" cx="${m.a.x}" cy="${-m.a.y}" r="${3 * px}"/><circle class="m-pt" cx="${m.b.x}" cy="${-m.b.y}" r="${3 * px}"/>` + mLabel(m.a, m.b, `${fmt(m.d, 2)} mm`);
  });
  if (insp.pending) {
    out += `<circle class="m-pt" cx="${insp.pending.x}" cy="${-insp.pending.y}" r="${3.5 * px}"/>`;
    if (insp.hover) { const dd = Math.hypot(insp.hover.x - insp.pending.x, insp.hover.y - insp.pending.y); out += `<line class="m-ghost" x1="${insp.pending.x}" y1="${-insp.pending.y}" x2="${insp.hover.x}" y2="${-insp.hover.y}"/>` + mLabel(insp.pending, insp.hover, `${fmt(dd, 2)} mm`); }
  }
  if (insp.tool === 'measure' && insp.hover && insp.hover.kind) out += `<circle class="snap" cx="${insp.hover.x}" cy="${-insp.hover.y}" r="${5 * px}"/>`;
  svg.innerHTML = out;
}
function dimsSvg(d, px, fs) {
  const [x1, y1, x2, y2] = d.bounds, off = 26 * px, ext = 6 * px, a = 5 * px;
  const arrow = (x, y, dx, dy) => `M${x},${-y}l${(-dx * a + dy * a * .45)},${(dy * a + dx * a * .45)}M${x},${-y}l${(-dx * a - dy * a * .45)},${(dy * a - dx * a * .45)}`;
  let s = '';
  // width (below)
  const yb = y1 - off;
  s += `<path class="dim" d="M${P(x1, y1 - ext * .5)}L${P(x1, yb - ext)}M${P(x2, y1 - ext * .5)}L${P(x2, yb - ext)}M${P(x1, yb)}L${P(x2, yb)}${arrow(x1, yb, -1, 0)}${arrow(x2, yb, 1, 0)}"/>`;
  s += `<text class="dim-text" x="${(x1 + x2) / 2}" y="${-yb + fs * 1.25}" font-size="${fs}" text-anchor="middle">${fmt(d.width, 2)}</text>`;
  // height (left)
  const xl = x1 - off;
  s += `<path class="dim" d="M${P(x1 - ext * .5, y1)}L${P(xl - ext, y1)}M${P(x1 - ext * .5, y2)}L${P(xl - ext, y2)}M${P(xl, y1)}L${P(xl, y2)}${arrow(xl, y1, 0, -1)}${arrow(xl, y2, 0, 1)}"/>`;
  s += `<text class="dim-text" transform="translate(${xl - fs * .5} ${-(y1 + y2) / 2}) rotate(-90)" font-size="${fs}" text-anchor="middle">${fmt(d.height, 2)}</text>`;
  // hole labels where they are readable
  d.holes.forEach(h => {
    const size = Math.max(h.bounds[2] - h.bounds[0], h.bounds[3] - h.bounds[1]) / px;
    if (d.holes.length > 40 && size < 18) return;
    const label = h.type === 'circle' ? `Ø${fmt(h.diameter, 2)}` : `${fmt(h.width, 1)}×${fmt(h.length, 1)}`;
    const x = h.bounds[2] + 3 * px, y = h.bounds[3] + 2 * px;
    s += `<text class="dim-text" x="${x}" y="${-y}" font-size="${fs * .92}">${label}</text>`;
  });
  return s;
}
function relXY(x, y) { const d = insp.detail; return [x - d.bounds[0], y - d.bounds[1]]; }
function renderInspSide() {
  const d = insp.detail, g = insp.group, mm = d.units === 4 ? 'mm' : 'birim';
  const dens = { steel: 7850, stainless: 8000, aluminum: 2700 }[$('#material').value];
  const kg = d.thicknessMm ? d.area * d.thicknessMm * dens / 1e9 : null;
  const notes = [];
  if (d.exportMode === 'sampled') notes.push('<p class="note warn">Bu parçanın orijinal CAD nesneleri kapalı kontur oluşturmadı. DXF çıktısında polyline olarak aktarılır, yaylar segmentlidir.</p>');
  if (d.exportMode === 'trimmed') notes.push('<p class="note">Başka bir parçayla ortak çizgisi vardı; o çizgi bu parçaya ait kısmından kırpılarak aktarılır.</p>');
  if (d.mirrorNote) notes.push(`<p class="note warn">${esc(d.mirrorNote)}.</p>`);
  if (d.fromPackage) notes.push('<p class="note warn">Çerçeveli teknik resimden seçilen ana görünüm adayı. Kesim açınımı olduğunu doğrula.</p>');
  $('#itabDims').innerHTML = `
    <dl class="kv">
      <dt>Dış ölçü</dt><dd class="big">${fmt(d.width, 2)} × ${fmt(d.height, 2)} ${mm}</dd>
      <dt>En dar kutu (döndürülmüş)</dt><dd>${fmt(d.minRect.length, 2)} × ${fmt(d.minRect.width, 2)}</dd>
      <dt>Dış çevre</dt><dd>${fmt(d.outerPerimeter, 2)} ${mm}</dd>
      <dt>Toplam kesim boyu</dt><dd>${fmt(d.cutLength, 1)} ${mm}</dd>
      <dt>Net alan</dt><dd>${d.units === 4 ? fmt(d.area / 1e6, 5) + ' m²' : fmt(d.area, 1) + ' birim²'}</dd>
      <dt>Delik</dt><dd>${d.holes.length}</dd>
      <dt>Kalınlık</dt><dd>${d.thicknessMm == null ? 'okunamadı' : fmt(d.thicknessMm, 2) + ' mm'}</dd>
      <dt>Tek parça ağırlığı</dt><dd>${kg == null ? '—' : fmt(kg, 3) + ' kg'}</dd>
      <dt>Planlanan adet</dt><dd>${g.quantity}</dd>
      <dt>Delme sayısı (adet başına)</dt><dd>${d.holes.length + 1}</dd>
    </dl>${notes.join('')}
    <p class="hint">Çizim üzerindeki ölçüler kontur sınırlarından hesaplanır. İki nokta arasını ölçmek için <span class="kbd">M</span> tuşuna bas ya da “Ölç” aracını seç.</p>`;
  // holes
  $('#holeBadge').textContent = d.holes.length || '';
  const groups = new Map();
  d.holes.forEach(h => { const k = h.type === 'circle' ? `Ø${fmt(h.diameter, 2)}` : `${{ slot: 'Oval', rect: 'Dikdörtgen', free: 'Serbest' }[h.type]} ${fmt(h.width, 1)}×${fmt(h.length, 1)}`; groups.set(k, (groups.get(k) || 0) + 1); });
  $('#itabHoles').innerHTML = d.holes.length ? `
    <div class="hole-sum">${[...groups].map(([k, n]) => `<span class="tag repeat">${n} × ${esc(k)}</span>`).join('')}</div>
    <table class="hole-table"><thead><tr><th>#</th><th>Ölçü</th><th>Merkez X, Y</th><th>Kaynak</th></tr></thead><tbody>
    ${d.holes.map((h, i) => { const [rx, ry] = relXY(h.center[0], h.center[1]); return `<tr data-hole-row="${i}" class="${insp.hl === i ? 'on' : ''}"><td>${i + 1}</td><td>${h.type === 'circle' ? `${h.exact ? '' : '≈'}Ø${fmt(h.diameter, 3)}` : `${{ slot: 'Oval', rect: 'Dikdörtgen', free: 'Serbest' }[h.type]} ${fmt(h.width, 2)}×${fmt(h.length, 2)}`}</td><td>${fmt(rx, 2)}, ${fmt(ry, 2)}</td><td>${esc(h.source)}</td></tr>`; }).join('')}
    </tbody></table><p class="hint">Merkezler parçanın sol alt köşesine göredir. “≈” işaretli çaplar konturdan hesaplanmıştır; CAD dairesi olanlar kesindir.</p>` : '<p class="hint">Bu parçada delik yok.</p>';
  renderMeasureTab();
}
function renderMeasureTab() {
  $('#measureBadge').textContent = insp.measures.length || '';
  $('#itabMeasure').innerHTML = `<p class="hint">Ölç aracını seç, ilk noktaya ve ikinci noktaya tıkla. İmleç köşe, delik merkezi, daire çeyreği ve kenarlara yapışır. <span class="kbd">Shift</span> yatay/dikey kilitler, <span class="kbd">Esc</span> yarım ölçümü iptal eder.</p>
    ${insp.measures.length ? `<ol class="measure-list">${insp.measures.map((m, i) => `<li><b>${fmt(m.d, 3)} mm</b><span>ΔX ${fmt(Math.abs(m.b.x - m.a.x), 3)}, ΔY ${fmt(Math.abs(m.b.y - m.a.y), 3)}, açı ${fmt(m.ang, 2)}°${m.kinds ? ', ' + esc(m.kinds) : ''}</span><button type="button" data-del-measure="${i}" aria-label="Ölçümü sil">Sil</button></li>`).join('')}</ol>
    <button class="btn quiet" type="button" id="clearMeasures">Tümünü temizle</button>` : ''}`;
}
function setTool(t) {
  insp.tool = t; insp.pending = null;
  $$('#toolSeg button').forEach(b => b.classList.toggle('on', b.dataset.tool === t));
  if (t === 'measure') setITab('measure');
  renderInsp();
}
function setITab(t) {
  $$('.insp-side .tab').forEach(b => b.classList.toggle('on', b.dataset.itab === t));
  $('#itabDims').hidden = t !== 'dims'; $('#itabHoles').hidden = t !== 'holes'; $('#itabMeasure').hidden = t !== 'measure';
}
function inspClick(ev) {
  const w = toWorld(ev);
  if (insp.tool === 'measure') {
    const p = snapPoint(w, ev.shiftKey && insp.pending ? insp.pending : null);
    if (!insp.pending) { insp.pending = p; }
    else {
      const a = insp.pending, b = p, d = Math.hypot(b.x - a.x, b.y - a.y);
      if (d > 1e-9) insp.measures.push({ a, b, d, ang: (Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI + 360) % 180, kinds: [a.kind, b.kind].filter(Boolean).join(' → ') });
      insp.pending = null; renderMeasureTab();
    }
    renderInsp(); return;
  }
  const hole = ev.target.closest('[data-hole]');
  insp.hl = hole ? Number(hole.dataset.hole) : null;
  if (hole) setITab('holes');
  renderInspSide(); setITab(hole ? 'holes' : $$('.insp-side .tab').find(t => t.classList.contains('on')).dataset.itab); renderInsp();
}

/* ------------------------------------------------------------ events */
$('#openFile').addEventListener('click', () => $('#fileInput').click());
$('#browse').addEventListener('click', () => $('#fileInput').click());
$('#fileInput').addEventListener('change', ev => analyze([...ev.target.files]));
$('#demo').addEventListener('click', demo); $('#demo2').addEventListener('click', demo);
const drop = $('#drop');
['dragenter', 'dragover'].forEach(t => document.addEventListener(t, ev => { if (ev.dataTransfer?.types?.includes('Files')) { ev.preventDefault(); drop.classList.add('drag'); } }));
document.addEventListener('dragleave', ev => { if (!ev.relatedTarget) drop.classList.remove('drag'); });
document.addEventListener('drop', ev => { if (!ev.dataTransfer?.files?.length) return; ev.preventDefault(); drop.classList.remove('drag'); analyze([...ev.dataTransfer.files]); });
drop.addEventListener('keydown', ev => { if (ev.target === drop && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); $('#fileInput').click(); } });
$('#reset').addEventListener('click', () => {
  cancelTask(); state.requestCount++; state.data = null; state.plan = null; state.groups = [];
  $('#workspace').hidden = true; $('#drop').hidden = false; $('#job').hidden = true; $('#exportMenu').hidden = true; $('#reset').hidden = true;
  $('#parts').innerHTML = ''; $('#partTotal').textContent = ''; $('#calculate').disabled = true; $('#speedTable').innerHTML = '';
  renderIdle(); status('');
});
// export menu
const menuList = $('#exportMenu .menu-list');
$('#exportToggle').addEventListener('click', ev => { ev.stopPropagation(); menuList.hidden = !menuList.hidden; $('#exportToggle').setAttribute('aria-expanded', String(!menuList.hidden)); });
document.addEventListener('click', () => { menuList.hidden = true; $('#exportToggle').setAttribute('aria-expanded', 'false'); });
$('#downloadAll').addEventListener('click', () => state.data && downloadBlob(`/api/download-plan/${encodeURIComponent(state.data.jobId)}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(requestBody()) }, 'DXFsmart_Parcalar.zip'));
$('#downloadNest').addEventListener('click', () => state.lastTaskId && downloadBlob(`/api/nesting-jobs/${encodeURIComponent(state.lastTaskId)}/dxf`, {}, 'DXFsmart_Yerlesim_Plakalari.zip'));
$('#downloadCsv').addEventListener('click', csvDownload);
// parts pane
$('#filters').addEventListener('click', ev => { const b = ev.target.closest('[data-filter]'); if (!b) return; state.filter = b.dataset.filter; $$('#filters button').forEach(x => x.classList.toggle('on', x === b)); renderParts(); });
$('#partSearch').addEventListener('input', renderParts);
$('#parts').addEventListener('click', ev => {
  const plus = ev.target.closest('[data-qty-plus]'), minus = ev.target.closest('[data-qty-minus]');
  if (plus) return changeQty(plus.dataset.qtyPlus, 'plus');
  if (minus) return changeQty(minus.dataset.qtyMinus, 'minus');
  if (ev.target.closest('[data-stop]')) return;
  const row = ev.target.closest('.part-row'); if (!row) return;
  const g = state.groups.find(x => x.key === row.dataset.key); if (g) openInspector(g.primary.id);
});
$('#parts').addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.classList.contains('part-row')) { const g = state.groups.find(x => x.key === ev.target.dataset.key); if (g) openInspector(g.primary.id); } });
$('#parts').addEventListener('change', ev => { if (ev.target.matches('[data-qty]')) changeQty(ev.target.dataset.qty, 'set', ev.target.value); });
$('#parts').addEventListener('mouseover', ev => {
  const row = ev.target.closest('.part-row'); const key = row ? row.dataset.key : null;
  if (key === state.hoverKey) return; state.hoverKey = key;
  const g = key && state.groups.find(x => x.key === key);
  $$('#groupView .sheet-svg .cut').forEach(p => p.classList.toggle('hl', !!g && p.dataset.part === g.primary.id));
});
$('#parts').addEventListener('mouseleave', () => { state.hoverKey = null; $$('#groupView .sheet-svg .cut.hl').forEach(p => p.classList.remove('hl')); });
// settings
$('#methodSeg').addEventListener('click', ev => {
  const b = ev.target.closest('[data-method]'); if (!b) return; state.method = b.dataset.method;
  $$('#methodSeg button').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-checked', String(x === b)); });
  $('#partInPart').disabled = state.method !== 'shape'; $('#quality').disabled = state.method !== 'shape';
  invalidate('Yerleşim yöntemi değişti.');
});
$('#sheetPreset').addEventListener('change', ev => { const v = ev.target.value; if (v !== 'custom') { const [w, h] = v.split('x'); $('#sheetWidth').value = w; $('#sheetHeight').value = h; } invalidate('Sac ölçüsü değişti.'); });
['sheetWidth', 'sheetHeight'].forEach(id => $('#' + id).addEventListener('change', () => {
  const k = $('#sheetWidth').value + 'x' + $('#sheetHeight').value; $('#sheetPreset').value = [...$('#sheetPreset').options].some(o => o.value === k) ? k : 'custom'; invalidate('Sac ölçüsü değişti.');
}));
['gap', 'margin', 'rotation', 'quality', 'partInPart', 'confirmMillimeters', 'pierceSeconds', 'overheadPercent', 'cuttingSpeed'].forEach(id => $('#' + id).addEventListener('change', () => invalidate('Ayarlar değişti.')));
$('#material').addEventListener('change', () => { fillTableSpeeds(); invalidate('Malzeme değişti.'); });
$('#defaultThickness').addEventListener('change', () => { buildSpeedRows(); invalidate('Varsayılan kalınlık değişti.'); });
$('#laserKw').addEventListener('change', () => { try { localStorage.setItem('dxfsmart.kw', $('#laserKw').value); } catch (_) { } fillTableSpeeds(); invalidate('Lazer gücü değişti.'); });
$('#speedTable').addEventListener('input', ev => { const i = ev.target.closest('[data-speed]'); if (!i) return; i.dataset.edited = i.value === '' ? '' : '1'; i.classList.toggle('edited', !!i.dataset.edited); $(`[data-src="${i.dataset.speed}"]`).textContent = i.dataset.edited ? 'düzenlendi' : ''; if (!i.dataset.edited) fillTableSpeeds(); });
$('#speedTable').addEventListener('change', () => invalidate('Kesim hızları değişti.'));
$('#calculate').addEventListener('click', calculate);
// stage
$$('.stage-tabs [data-view]').forEach(t => t.addEventListener('click', () => { setView(t.dataset.view); if (t.dataset.view === 'nest' && !state.plan && !state.taskId) renderIdle(); }));
$('#viewNest').addEventListener('click', ev => {
  const chip = ev.target.closest('[data-group]'); if (chip) { state.groupIndex = Number(chip.dataset.group); state.sheetIndex = 0; $$('.chip').forEach(c => c.classList.toggle('on', c === chip)); renderGroup(); return; }
  const th = ev.target.closest('[data-sheet]'); if (th) { state.sheetIndex = Number(th.dataset.sheet); renderGroup(); return; }
  if (ev.target.closest('#sheetPrev')) { state.sheetIndex--; renderGroup(); return; }
  if (ev.target.closest('#sheetNext')) { state.sheetIndex++; renderGroup(); return; }
  const cut = ev.target.closest('.sheet-svg .cut'); if (cut) openInspector(cut.dataset.part);
});
// inspector
$('#closeInspector').addEventListener('click', closeInspector);
$('#inspector').addEventListener('click', ev => { if (ev.target.id === 'inspector') closeInspector(); });
$('#fitView').addEventListener('click', fitInspector);
$('#showDims').addEventListener('change', renderInsp);
$('#toolSeg').addEventListener('click', ev => { const b = ev.target.closest('[data-tool]'); if (b) setTool(b.dataset.tool); });
$$('.insp-side .tab').forEach(t => t.addEventListener('click', () => { setITab(t.dataset.itab); if (t.dataset.itab === 'measure' && insp.tool !== 'measure') setTool('measure'); }));
$('#prevPart').addEventListener('click', () => { if (insp.index > 0) { insp.index--; loadInspector(insp.list[insp.index]); } });
$('#nextPart').addEventListener('click', () => { if (insp.index < insp.list.length - 1) { insp.index++; loadInspector(insp.list[insp.index]); } });
$('#itabHoles').addEventListener('click', ev => {
  const row = ev.target.closest('[data-hole-row]'); if (!row) return;
  insp.hl = Number(row.dataset.holeRow); const h = insp.detail.holes[insp.hl];
  const size = Math.max(h.bounds[2] - h.bounds[0], h.bounds[3] - h.bounds[1]);
  const el = $('#inspCanvas'); insp.view.cx = h.center[0]; insp.view.cy = h.center[1];
  insp.view.scale = Math.max(insp.view.scale, Math.min(el.clientWidth, el.clientHeight) / (size * 8));
  $$('[data-hole-row]').forEach(r => r.classList.toggle('on', r === row)); renderInsp();
});
$('#itabMeasure').addEventListener('click', ev => {
  const del = ev.target.closest('[data-del-measure]'); if (del) { insp.measures.splice(Number(del.dataset.delMeasure), 1); renderMeasureTab(); renderInsp(); }
  if (ev.target.id === 'clearMeasures') { insp.measures = []; renderMeasureTab(); renderInsp(); }
});
const isvg = $('#inspSvg');
isvg.addEventListener('wheel', ev => {
  ev.preventDefault(); if (!insp.view) return;
  const before = toWorld(ev), k = Math.exp(-ev.deltaY * 0.0015);
  insp.view.scale = clamp(insp.view.scale * k, 0.01, 4000);
  const after = toWorld(ev); insp.view.cx += before[0] - after[0]; insp.view.cy += before[1] - after[1]; renderInsp();
}, { passive: false });
isvg.addEventListener('pointerdown', ev => { if (ev.button > 1) return; insp.drag = { x: ev.clientX, y: ev.clientY, cx: insp.view.cx, cy: insp.view.cy, moved: false }; isvg.setPointerCapture(ev.pointerId); });
isvg.addEventListener('pointermove', ev => {
  if (!insp.detail) return;
  const w = toWorld(ev);
  if (insp.drag) {
    const dx = ev.clientX - insp.drag.x, dy = ev.clientY - insp.drag.y;
    if (insp.drag.moved || Math.hypot(dx, dy) > 4) {
      insp.drag.moved = true; isvg.classList.add('dragging');
      insp.view.cx = insp.drag.cx - dx / insp.view.scale; insp.view.cy = insp.drag.cy + dy / insp.view.scale; renderInsp(); return;
    }
  }
  const [rx, ry] = relXY(w[0], w[1]);
  if (insp.tool === 'measure') { insp.hover = snapPoint(w, ev.shiftKey && insp.pending ? insp.pending : null); renderInsp(); }
  $('#readout').textContent = `X ${fmt(rx, 2, 2)}  Y ${fmt(ry, 2, 2)} mm (sol alt köşeye göre)${insp.tool === "measure" && insp.hover?.kind ? ", yapışma: " + insp.hover.kind : ""}`;
});
isvg.addEventListener('pointerup', ev => { const d = insp.drag; insp.drag = null; isvg.classList.remove('dragging'); if (d && !d.moved) inspClick(ev); });
isvg.addEventListener('pointerleave', () => { insp.hover = null; renderInsp(); });
document.addEventListener('keydown', ev => {
  if ($('#inspector').hidden) return;
  if (ev.target.matches('input,select,textarea')) return;
  if (ev.key === 'Escape') { if (insp.pending) { insp.pending = null; renderInsp(); } else closeInspector(); }
  else if (ev.key === 'm' || ev.key === 'M') setTool(insp.tool === 'measure' ? 'pan' : 'measure');
  else if (ev.key === 'f' || ev.key === 'F') fitInspector();
  else if (ev.key === 'Backspace' && insp.measures.length) { insp.measures.pop(); renderMeasureTab(); renderInsp(); }
  else if (ev.key === 'ArrowRight') $('#nextPart').click();
  else if (ev.key === 'ArrowLeft') $('#prevPart').click();
});
window.addEventListener('resize', () => { if (!$('#inspector').hidden) renderInsp(); });

async function checkUpdate() {
  try {
    const r = await fetch('/api/update'); if (!r.ok) return;
    const u = await r.json();
    if (!u.available) return;
    let skipped = null; try { skipped = localStorage.getItem('dxfsmart.skip'); } catch (_) { }
    if (skipped === u.latest) return;
    $('#updateText').innerHTML = `<b>DXFsmart ${esc(u.latest)}</b> yayında (sende ${esc(u.current)}). ${u.canInstall ? 'Tek tıkla güncelleyip yeniden açabilirim.' : 'GitHub Releases sayfasından indirebilirsin.'}`;
    $('#updateBtn').textContent = u.canInstall ? 'Güncelle' : 'İndirme sayfası';
    $('#updateBtn').onclick = async () => {
      if (!u.canInstall) { window.open(u.url, '_blank'); return; }
      $('#updateBtn').disabled = true; $('#updateText').innerHTML = '<b>Güncelleniyor…</b> Yeni sürüm indiriliyor, bitince program kendini yeniden açacak.';
      try {
        const res = await fetch('/api/update/install', { method: 'POST' }); if (!res.ok) throw Error(await apiError(res));
        $('#updateText').innerHTML = '<b>Yeniden başlatılıyor…</b> Pencere birkaç saniye içinde yeni sürümle açılacak.';
      } catch (e) { $('#updateText').textContent = 'Güncellenemedi: ' + e.message; $('#updateBtn').disabled = false; }
    };
    $('#updateLater').onclick = () => { try { localStorage.setItem('dxfsmart.skip', u.latest); } catch (_) { } $('#updateBar').hidden = true; document.body.classList.remove('has-update'); };
    $('#updateBar').hidden = false; document.body.classList.add('has-update');
  } catch (_) { }
}

renderIdle(); loadPresets(); checkUpdate();
