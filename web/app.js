/* FWSubsBatch 前端逻辑 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const S = {
  settings: {}, schema: [], groups: [], defaults: {}, baseline: {},
  profiles: [], files: new Map(),   // path -> item
  params: {}, baselineParams: {},
  jasnaSchema: [], jasnaGroups: [], jasnaDefaults: {}, jasnaBaselineParams: {},
  jasnaProfiles: [], jasnaParams: {}, jasnaBaseline: {},
  lastProfile: '', lastJasnaProfile: '',
  dirty: false, jasnaDirty: false, _dirtyKeys: null, _jasnaDirtyKeys: null,
  job: { jobs: [], summary: {}, running: false },
  modes: [], mode: 2,
  logSeq: 0, pollTimer: null, browseTarget: 'folder',
  saveKind: 'sub',           // 另存配置弹窗当前针对哪套参数
};

/* ---------------- 基础工具 ---------------- */
async function api(path, body) {
  const opt = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: '返回解析失败' }));
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

/*安全读取 S.settings 的字段。
  后端在网络抖动/服务重启瞬间可能返回残缺 JSON，
  若直接赋值会把 S.settings 变成 undefined，之后任何 .fw_dir 访问都会抛
  "Cannot read properties of undefined"，表现为点「保存设置」报错。*/
function cfg(key, fallback = '') {
  return (S.settings && S.settings[key] !== undefined && S.settings[key] !== null)
    ? S.settings[key] : fallback;
}
/* 接收后端 settings 时做校验，绝不让 undefined污染全局状态 */
function applySettings(s) {
  if (!s || typeof s !== 'object') {
    console.warn('后端未返回 settings，保留原值', s);
    return false;
  }
  S.settings = s;
  return true;
}

let toastTimer = null;
function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 3600);
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function openModal(id) { $(id).classList.remove('hidden'); }
function closeModal(id) { $(id).classList.add('hidden'); }

/* ---------------- 启动 ---------------- */
async function boot() {
  const st = await api('/api/state');
  applySettings(st.settings);
  S.schema = st.schema;
  S.groups = st.groups;
  S.defaults = st.defaults;
  S.baselineParams = st.baseline || st.defaults;
  S.profiles = st.profiles;
  S.lastProfile = st.baseline_name || '';
  S.params = JSON.parse(JSON.stringify(S.baselineParams));

  S.jasnaSchema = st.jasna_schema || [];
  S.jasnaGroups = st.jasna_groups || [];
  S.jasnaDefaults = st.jasna_defaults || {};
  S.jasnaBaselineParams = st.jasna_baseline || S.jasnaDefaults;
  S.jasnaProfiles = st.jasna_profiles || [];
  S.lastJasnaProfile = st.jasna_baseline_name || '';
  S.jasnaParams = JSON.parse(JSON.stringify(S.jasnaBaselineParams));
  S.modes = st.modes || [];
  S.mode = Number(cfg('mode', 2)) || 2;

  $('#appVer').textContent = 'v' + (st.app.version || '2.0.0')
    + ' · 字幕 ' + S.schema.length + ' 项 · Jasna ' + S.jasnaSchema.length + ' 项';
  renderBadges(st);
  renderModes();
  renderProfiles();
  renderParams();
  renderJasnaProfiles();
  renderJasnaParams();
  renderFolderChips();
  renderSettingsForm();
  bind();
  updateSelCount();
  applyMode();

  if (cfg('folders', []).length) scanAll(true);
  startPolling();
  if (!st.fw.ok || !st.jasna.ok) setTimeout(() => { openModal('#modalSettings'); checkDirs(); }, 400);
}

/* ---------------- 处理模式 ---------------- */
const MODE_META = {
  1: { label: '只清除马赛克', short: '去马赛克', suffix: '-U', needJasna: true, needSub: false },
  2: { label: '只生成中文字幕', short: '加字幕', suffix: '-C', needJasna: false, needSub: true },
  3: { label: '清除马赛克 + 生成中文字幕', short: '去马赛克+字幕', suffix: '-UC', needJasna: true, needSub: true },
};
function modeMeta(m) { return MODE_META[Number(m)] || MODE_META[2]; }

function renderModes() {
  const box = $('#modeBtns');
  if (!S.modes.length) { box.innerHTML = ''; return; }
  box.innerHTML = S.modes.map(m => `
    <button class="mode-btn${Number(m.value) === S.mode ? ' active' : ''}"
            data-mode="${m.value}" title="${esc(m.desc || '')}">
      <b>${esc(m.short || m.label)}</b><span class="sfx">${esc(m.suffix || '')}</span>
    </button>`).join('');
  $$('#modeBtns .mode-btn').forEach(b => b.onclick = () => setMode(Number(b.dataset.mode)));
}

function setMode(m) {
  if (Number(m) === S.mode) return;
  S.mode = Number(m);
  applySettings(Object.assign({}, S.settings, { mode: S.mode }));
  renderModes();
  applyMode();
  toast('已切换到「' + modeMeta(S.mode).label + '」', 'ok');
  scanAll(true);          // 换模式等于换跳过规则，重算勾选与输出名
}

/* 按当前模式联动界面：禁用无关面板、更新提示文案 */
function applyMode() {
  const meta = modeMeta(S.mode);
  // 参数面板：不需要的 Tab 灰掉
  const tabSub = document.querySelector('.tab[data-tab="sub"]');
  const tabJasna = document.querySelector('.tab[data-tab="jasna"]');
  if (tabSub) tabSub.classList.toggle('disabled', !meta.needSub);
  if (tabJasna) tabJasna.classList.toggle('disabled', !meta.needJasna);
  // 当前 Tab 若被禁用，自动切到需要的那个
  const active = document.querySelector('.tab-pane.active');
  if (active && active.classList.contains('disabled')) {
    switchTab(meta.needJasna && !meta.needSub ? 'jasna' : 'sub');
  } else if (active) {
    const isJasna = active.id === 'paneJasna';
    if (isJasna && !meta.needJasna) switchTab('sub');
    if (!isJasna && !meta.needSub) switchTab('jasna');
  }
  // 模式提示
  const hint = $('#modeHint');
  if (hint) {
    const skipTxt = modeSkipHint(S.mode);
    hint.innerHTML = `<b>输出文件名加 ${esc(meta.suffix)}</b> · ${esc(skipTxt)}`;
  }
  // 「保留独立 SRT」只在模式 2 有意义
  const sw = $('#keepSrtWrap');
  if (sw) sw.style.display = meta.needSub ? '' : 'none';
  // 自动勾选规则提示
  const arh = $('#autoRuleHint');
  if (arh) arh.textContent = modeSkipHint(S.mode);
  // 预览按钮
  const pv = $('#btnPreview');
  if (pv) pv.title = `预览当前模式下将执行的完整命令链（${meta.label}）`;
  updateSelCount();
}

function modeSkipHint(m) {
  if (Number(m) === 1) return '自动跳过已带 -U / -UC 的文件';
  if (Number(m) === 2) return '自动跳过已带 -C / -UC 的文件';
  return '自动跳过已带 -U / -C / -UC 的文件（需两项都未做过）';
}

function switchTab(which) {
  $$('#paramTabs .tab').forEach(t => t.classList.toggle('active', t.dataset.tab === which));
  $('#paneSub').classList.toggle('active', which === 'sub');
  $('#paneJasna').classList.toggle('active', which === 'jasna');
}

/* ---------------- 顶栏状态 ---------------- */
function renderBadges(st) {
  const b = $('#fwBadge');
  if (st.fw && st.fw.ok) {
    b.className = 'badge ok';
    b.textContent = 'Faster Whisper 已就绪';
    b.title = cfg('fw_dir');
  } else {
    b.className = 'badge err';
    b.textContent = 'Faster Whisper 未配置';
    b.onclick = () => { openModal('#modalSettings'); checkDirs(); };
  }
  const j = $('#jasnaBadge');
  if (st.jasna && st.jasna.ok) {
    j.className = 'badge ok';
    j.textContent = 'Jasna 已就绪';
    j.title = cfg('jasna_dir') + (st.jasna.warn ? ('\n⚠ ' + st.jasna.warn) : '');
  } else {
    j.className = 'badge err';
    j.textContent = 'Jasna 未配置';
    j.onclick = () => { openModal('#modalSettings'); checkDirs(); };
  }
  const f = $('#ffBadge');
  if (st.ffmpeg) { f.className = 'badge ok'; f.textContent = 'ffmpeg 已就绪'; f.title = st.ffmpeg; }
  else { f.className = 'badge warn'; f.textContent = 'ffmpeg 未找到（无法封装）'; f.onclick = () => openModal('#modalSettings'); }
}

/* ---------------- 文件夹 ---------------- */
function renderFolderChips() {
  const box = $('#folderChips');
  const fs = cfg('folders', []);
  if (!fs.length) { box.innerHTML = ''; return; }
  box.innerHTML = fs.map(f => {
    const n = Array.from(S.files.values()).filter(x => x.path.startsWith(f)).length;
    return `<div class="chip" title="${esc(f)}"><b>📁</b><span>${esc(f.split(/[\\/]/).pop())}</span>
      <span style="color:var(--tx3)">${n ? '(' + n + ')' : ''}</span>
      <button data-rm="${esc(f)}" title="移除">✕</button></div>`;
  }).join('');
  $$('#folderChips button[data-rm]').forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    await api('/api/remove_folder', { folder: b.dataset.rm });
    S.files.forEach((v, k) => { if (k.startsWith(b.dataset.rm)) S.files.delete(k); });
    applySettings(Object.assign({}, S.settings,
      { folders: cfg('folders', []).filter(x => x !== b.dataset.rm) }));
    renderFolderChips(); renderFiles(); scanAll();
  });
}

async function addFolder(path) {
  path = (path || '').trim().replace(/^"|"$/g, '');
  if (!path) return toast('请输入文件夹路径', 'warn');
  try {
    const r = await api('/api/add_folder', { folder: path });
    applySettings(Object.assign({}, S.settings, { folders: r.folders }));
    $('#inpFolder').value = '';
    renderFolderChips();
    await scanAll();
    toast('已添加文件夹', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function scanAll(silent) {
  const fs = cfg('folders', []);
  if (!fs.length) {
    S.files.clear();          // 没有文件夹了，列表必须清空
    renderFiles(); renderFolderChips(); updateSelCount();
    return;
  }
  let total = 0;
  let cleaned = 0;
  // 逐个文件夹「整目录替换」：先把该文件夹下的旧条目全清掉再放新结果。
  // 否则用户删了文件后点「重新扫描」，旧文件仍会留在列表里。
  for (const f of fs) {
    const base = f.replace(/[\\/]+$/, '');
    for (const key of Array.from(S.files.keys())) {
      const k = key.replace(/\//g, '\\');
      if (k.startsWith(base.replace(/\//g, '\\') + '\\')) S.files.delete(key);
    }
    try {
      const r = await api('/api/scan', {
        folder: f,
        folders: fs,                 // 让后端顺带清理已删文件的勾选记录
        recursive: cfg('recursive', true),
        mode: S.mode,                // 换模式时输出名与跳过判定都不同
      });
      r.items.forEach(it => { S.files.set(it.path, it); total++; });
      if (r.stale_cleared) cleaned += r.stale_cleared;
    } catch (e) {
      if (!silent) toast(`${f} 扫描失败：${e.message}`, 'err');
      else console.warn('scan failed', f, e);
    }
  }
  renderFiles(); renderFolderChips(); updateSelCount();
  if (!silent) {
    const extra = cleaned ? `，清理 ${cleaned} 条已删文件记录` : '';
    toast(`扫描完成，共 ${total} 个视频文件${extra}`, 'ok');
  }
}

/* ---------------- 文件列表 ---------------- */
function renderFiles() {
  const box = $('#fileList');
  const arr = Array.from(S.files.values());
  if (!arr.length) {
    box.innerHTML = `<div class="empty"><div class="empty-icon">🎬</div>
      <p>还没有添加文件夹</p>
      <p class="hint">粘贴视频文件夹路径 → 添加 → 自动递归扫描视频文件</p></div>`;
    $('#fileStat').textContent = '未扫描';
    return;
  }
  const dir = arr[0].path.replace(/[\\/][^\\/]*$/, '');
  box.innerHTML = arr.map(it => `
    <div class="file-row ${it.selected ? 'sel' : ''} ${it.skip ? 'skip' : (it.derived ? 'derived' : '')}"
         data-path="${esc(it.path)}" title="${esc(it.path)}${it.skip ? '\n⚠ ' + esc(it.skip_reason) : ''}">
      <input type="checkbox" ${it.selected ? 'checked' : ''} data-chk>
      <span class="c-name">${it.skip ? '<span class="tag warn">本模式跳过</span>' : (it.derived ? '<span class="tag">已处理</span>' : '')}${esc(it.name)}</span>
      <span class="c-size">${esc(it.size_h)}</span>
      <span class="c-dst">${it.skip ? '<span class="dim">—</span>' : ('→ ' + esc(it.target))}</span>
    </div>`).join('');
  $$('#fileList .file-row').forEach(row => {
    const p = row.dataset.path;
    row.onclick = (e) => {
      if (e.target.tagName === 'INPUT') return;
      const it = S.files.get(p);
      it.selected = !it.selected;
      row.classList.toggle('sel', it.selected);
      row.querySelector('[data-chk]').checked = it.selected;
      updateSelCount(); persistSelection([p], it.selected ? 'add' : 'del');
    };
    row.querySelector('[data-chk]').onchange = (e) => {
      const it = S.files.get(p);
      it.selected = e.target.checked;
      row.classList.toggle('sel', it.selected);
      updateSelCount(); persistSelection([p], it.selected ? 'add' : 'del');
    };
  });
  const dirs = new Set(arr.map(x => x.path.replace(/[\\/][^\\/]*$/, '')));
  $('#fileStat').textContent =
    `${arr.length} 个视频 · ${[...dirs].length > 1 ? '跨 ' + dirs.size + ' 个目录' : ''} · 已勾选 ${arr.filter(x => x.selected).length}`;
}

function selectedPaths() { return Array.from(S.files.values()).filter(x => x.selected).map(x => x.path); }

function updateSelCount() {
  const n = selectedPaths().length;
  const s = $('#selCount');
  if (s) s.textContent = `已勾选 ${n} 个文件`;
  const chk = $('#fileList .file-row input[data-chk]');
  if (chk) { /* noop */ }
  const total = S.files.size;
  const all = $('#chkAll');
  if (all) { all.checked = total > 0 && n === total; all.indeterminate = n > 0 && n < total; }
}

async function persistSelection(paths, mode) {
  if (!paths.length) return;
  try { await api('/api/set_selected', { paths, mode, process_mode: S.mode }); }
  catch (e) { /* 静默 */ }
}

async function applySelection(mode) {
  const paths = Array.from(S.files.keys());
  if (!paths.length) return toast('没有文件', 'warn');
  const r = await api('/api/set_selected', { paths, mode, process_mode: S.mode });
  // 依据后端返回值刷新勾选状态
  paths.forEach(p => {
    if (S.files.has(p) && r.selected[p] !== undefined) S.files.get(p).selected = r.selected[p];
  });
  renderFiles(); updateSelCount();
  const n = selectedPaths().length;
  const msg = {
    auto: `自动勾选完成，选中 ${n} 个（${modeSkipHint(S.mode)}）`,
    invert: '已反选', none: '已清空勾选', all: `已全选 ${n} 个`,
  };
  toast(msg[mode] || '完成', 'ok');
}

/* ---------------- 参数表单 ---------------- */
function renderParams() {
  const box = $('#paramForm');
  box.innerHTML = S.groups.map(g => {
    const fields = S.schema.filter(f => f.group === g);
    if (!fields.length) return '';
    return `<div class="pgroup"><h4>${esc(g)}</h4>` + fields.map(f => {
      const v = S.params[f.key];
      const id = 'p_' + f.key;
      let ctl = '';
      if (f.type === 'bool') {
        ctl = `<label class="switch"><input type="checkbox" id="${id}" data-k="${f.key}" ${v ? 'checked' : ''}><i></i></label>`;
      } else if (f.type === 'enum' || f.type === 'tri') {
        const opts = f.options.map(o => {
          const labels = { auto: 'auto（沿用配置文件）', enable: '启用合并', disable: '禁用合并', true: '开启', false: '关闭' };
          return `<option value="${o}" ${String(v) === o ? 'selected' : ''}>${esc(labels[o] || o)}</option>`;
        }).join('');
        ctl = `<select id="${id}" data-k="${f.key}">${opts}</select>`;
      } else if (f.type === 'int' || f.type === 'float') {
        ctl = `<input type="text" id="${id}" data-k="${f.key}" value="${esc(v)}" placeholder="${esc(f.placeholder || '')}" spellcheck="false">`;
      } else {
        ctl = `<input type="text" id="${id}" data-k="${f.key}" value="${esc(v)}" placeholder="${esc(f.placeholder || '')}" spellcheck="false">`;
      }
      const hint = f.help ? `<div class="hint">${esc(f.help)}</div>` : '';
      return `<div class="prow"><label for="${id}" title="${esc(f.label)}">${esc(f.label)}</label>
        <div class="ctl">${ctl}</div>${hint}</div>`;
    }).join('') + `</div>`;
  }).join('');
  $$('#paramForm [data-k]').forEach(el => {
    const ev = (el.type === 'checkbox') ? 'change' : 'input';
    el.addEventListener(ev, () => {
      S.params[el.dataset.k] = (el.type === 'checkbox') ? el.checked : el.value;
      markDirty();
    });
  });
}

function markDirty() {  S.dirty = JSON.stringify(S.params) !== JSON.stringify(S.baselineParams);
  $('#dirtyDot').classList.toggle('hidden', !S.dirty);
}

function renderProfiles() {
  const sel = $('#selProfile');
  const cur = S.lastProfile;
  sel.innerHTML = S.profiles.map(p =>
    `<option value="${esc(p.name)}" ${p.name === cur ? 'selected' : ''}>${esc(p.name)}${p.builtin ? '' : ' ★'}</option>`
  ).join('');
  const item = S.profiles.find(p => p.name === cur);
  $('#profileHelp').innerHTML = item
    ? `${item.builtin ? '<b>内置配置</b>' : '<b>我的配置</b>'}${item.saved_at ? ' · ' + esc(item.saved_at) : ''}${item.help ? ' — ' + esc(item.help) : ''}`
    : '当前为默认参数（未保存为配置）';
  const isBuiltin = !item || item.builtin;
  $('#btnRenameProfile').disabled = isBuiltin;
  $('#btnDelProfile').disabled = isBuiltin;
}

/* ================ Jasna 参数 ================ */
/* 条件显隐：schema 的 depends_on 形如 "字段名:取值"，为真才显示该行。
   例：secondary_restoration:rtx-super-res -> 只有选了RTX 超分才显示其子项。 */
function jasnaFieldVisible(f) {
  const dep = f.depends_on;
  if (!dep) return true;
  const i = dep.indexOf(':');
  if (i < 0) return true;
  const key = dep.slice(0, i), want = dep.slice(i + 1);
  return String(S.jasnaParams[key]) === want;
}

function jasnaRangeText(f) {
  if (!f.range) return '';
  const [a, b, st] = f.range;
  const fmt = (x) => (Number.isInteger(x) ? x : String(x));
  return `范围 ${fmt(a)} ~ ${fmt(b)}${st && st !== 1 ? '，步长 ' + fmt(st) : ''}`;
}

function renderJasnaParams() {
  const box = $('#jasnaForm');
  if (!box) return;
  if (!S.jasnaSchema.length) {
    box.innerHTML = '<div class="empty"><p>未获取到 Jasna 参数定义</p></div>';
    return;
  }
  box.innerHTML = S.jasnaGroups.map(g => {
    const fields = S.jasnaSchema.filter(f => f.group === g);
    if (!fields.length) return '';
    return `<div class="pgroup"><h4>${esc(g)}</h4>` + fields.map(f => {
      const v = S.jasnaParams[f.key];
      const id = 'j_' + f.key;
      let ctl = '';
      if (f.type === 'bool') {
        ctl = `<label class="switch"><input type="checkbox" id="${id}" data-k="${f.key}" ${v ? 'checked' : ''}><i></i></label>`;
      } else if (f.type === 'enum') {
        // 有 option_labels 就用中文说明，否则退回原值
        const labels = f.option_labels || {};
        const opts = f.options.map(o =>
          `<option value="${esc(o)}" ${String(v) === o ? 'selected' : ''}>${esc(labels[o] || o)}</option>`
        ).join('');
        ctl = `<select id="${id}" data-k="${f.key}">${opts}</select>`;
      } else if (f.type === 'int' || f.type === 'float') {
        // 数值型：给滑块 + 可输入数字，并明示范围
        const r = f.range || [0, 100, 1];
        const step = r[2] || 1;
        const cur = (v === '' || v === undefined || v === null) ? '' : v;
        ctl = `<div class="num-row">
            <input type="range" id="${id}_r" data-k="${f.key}" data-num="1"
                   min="${r[0]}" max="${r[1]}" step="${step}" value="${esc(cur)}">
            <input type="text" id="${id}" data-k="${f.key}" data-num="1"
                   value="${esc(cur)}" placeholder="${esc(f.placeholder || (f.type === 'int' ? '留空=默认' : '留空=默认'))}"
                   spellcheck="false">
          </div>`;
      } else {
        ctl = `<input type="text" id="${id}" data-k="${f.key}" value="${esc(v)}"
                     placeholder="${esc(f.placeholder || '')}" spellcheck="false">`;
      }
      // 说明：Jasna 官方 tooltip 原文 + 本工具补充的范围与依赖提示
      const bits = [];
      if (f.help) bits.push(esc(f.help));
      if (jasnaRangeText(f)) bits.push('<b>' + jasnaRangeText(f) + '</b>');
      if (f.need === 'topaz') bits.push('<span class="need">需自行安装 Topaz Video AI</span>');
      if (f.need === 'nvidia') bits.push('<span class="need">需 NVIDIA 显卡</span>');
      if (f.need === 'rtx50') bits.push('<span class="need">仅限 RTX 50 系列</span>');
      if (f.need === 'vram10g') bits.push('<span class="need">需 ≥10GB 显存</span>');
      const hint = bits.length ? `<div class="hint">${bits.join('<br>')}</div>` : '';
      // 滑块与数字框联动需要 range 属性，单独标出来
      const isNum = (f.type === 'int' || f.type === 'float') && f.range;
      return `<div class="prow${isNum ? ' has-range' : ''}" data-field="${f.key}" data-dep="${esc(f.depends_on || '')}">
        <label for="${id}" title="${esc(f.label)}"><span class="lbl-txt">${esc(f.label)}</span></label>
        <div class="ctl">${ctl}</div>${hint}</div>`;
    }).join('') + `</div>`;
  }).join('');

  // 事件绑定
  $$('#jasnaForm [data-k]').forEach(el => {
    const ev = (el.type === 'checkbox') ? 'change' : 'input';
    el.addEventListener(ev, () => {
      const k = el.dataset.k, f = S.jasnaSchema.find(x => x.key === k);
      let val;
      if (el.type === 'checkbox') val = el.checked;
      else if (el.dataset.num) {
        val = el.value;
        // 同步滑块 <-> 数字框
        const pair = el.id.endsWith('_r')
          ? document.getElementById(el.id.slice(0, -2))
          : document.getElementById(el.id + '_r');
        if (pair && val !== '') pair.value = val;
        // 越界时钳到范围，避免 Jasna 直接报错
        if (val !== '' && f && f.range) {
          let n = Number(val);
          if (!Number.isNaN(n)) {
            n = Math.min(f.range[1], Math.max(f.range[0], n));
            if (String(n) !== String(val)) { el.value = n; val = n; }
          }
        }
      } else val = el.value;
      S.jasnaParams[k] = val;
      markJasnaDirty();
      // 依赖字段可能显隐（如选了 RTX 超分才显示其子项）
      if (f && f.key === 'secondary_restoration') applyJasnaDeps();
      if (f && f.key === 'restoration_model') applyJasnaDeps();
      if (f && f.key === 'post_export_action') applyJasnaDeps();
    });
  });
  applyJasnaDeps();
  checkJasnaIssues();
}

function applyJasnaDeps() {
  $$('#jasnaForm .prow[data-dep]').forEach(row => {
    const f = S.jasnaSchema.find(x => x.key === row.dataset.field);
    if (!f) return;
    const vis = jasnaFieldVisible(f);
    row.style.display = vis ? '' : 'none';
  });
}

/* 依赖自检：把后端返回的error / warn 展示在参数面板顶部 */
async function checkJasnaIssues() {
  const box = $('#jasnaIssues');
  if (!box) return;
  try {
    const r = await api('/api/jasna_check', { params: S.jasnaParams });
    const issues = r.issues || [];
    if (!issues.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
    box.classList.remove('hidden');
    box.innerHTML = issues.map(i =>
      `<div class="issue ${i.level}">${i.level === 'error' ? '⛔' : '⚠'} ${esc(i.text)}</div>`
    ).join('');
  } catch (e) { /* 静默 */ }
}

function markJasnaDirty() {
  const cur = JSON.stringify(S.jasnaParams, Object.keys(S.jasnaParams).sort());
  const base = JSON.stringify(S.jasnaBaselineParams, Object.keys(S.jasnaBaselineParams).sort());
  S.jasnaDirty = cur !== base;
  const dot = $('#jasnaDirtyDot');
  if (dot) dot.classList.toggle('hidden', !S.jasnaDirty);
  checkJasnaIssues();
}

function renderJasnaProfiles() {
  const sel = $('#selJasnaProfile');
  if (!sel) return;
  const cur = S.lastJasnaProfile;
  sel.innerHTML = S.jasnaProfiles.map(p =>
    `<option value="${esc(p.name)}" ${p.name === cur ? 'selected' : ''}>${esc(p.name)}${p.builtin ? '' : ' ★'}</option>`
  ).join('');
  const item = S.jasnaProfiles.find(p => p.name === cur);
  const help = $('#jasnaProfileHelp');
  if (help) {
    help.innerHTML = item
      ? `${item.builtin ? '<b>内置配置</b>' : '<b>我的配置</b>'}${item.saved_at ? ' · ' + esc(item.saved_at) : ''}${item.help ? ' — ' + esc(item.help) : ''}`
      : '当前为 Jasna 默认参数（未保存为配置）';
  }
  const isBuiltin = !item || item.builtin;
  $('#btnRenameJasnaProfile').disabled = isBuiltin;
  $('#btnDelJasnaProfile').disabled = isBuiltin;
}

/* ---------------- 设置 ---------------- */
function renderSettingsForm() {
  // 用 cfg() 而非直读 S.settings：S.settings 可能因网络抖动被置为 undefined
  $('#inpJasnaDir').value = cfg('jasna_dir');
  $('#inpFwDir').value = cfg('fw_dir');
  $('#inpFfmpeg').value = cfg('ffmpeg_path');
  $('#inpOutDir').value = cfg('output_dir');
  $('#selPolicy').value = cfg('existing_output_policy', 'number');
  $('#selAutoRule').value = cfg('auto_select_rule', 'smart');
  $('#inpExts').value = cfg('video_exts', []).join(',');
  $('#chkClosePrompt').checked = cfg('close_prompt', true);
  $('#chkKeepSrt').checked = cfg('keep_srt', true);
  $('#chkKeepIntermediate').checked = cfg('keep_intermediate', false);
  $('#chkRecursive').checked = cfg('recursive', true);
}

/* 增量保存：只提交传入的字段，不碰其它已保存的设置 */
async function persistSettings(patch) {
  try {
    const r = await api('/api/save_settings', patch || {});
    applySettings(r.settings);
    Object.assign(S, { _dirtyKeys: Object.assign({}, S._dirtyKeys, patch) });
    return r;
  } catch (e) {
    console.warn('保存设置失败', e);
    return null;
  }
}

async function saveSettings(extra = {}) {
  const patch = Object.assign({
    jasna_dir: $('#inpJasnaDir').value.trim(),
    fw_dir: $('#inpFwDir').value.trim(),
    ffmpeg_path: $('#inpFfmpeg').value.trim(),
    output_dir: $('#inpOutDir').value.trim(),
    existing_output_policy: $('#selPolicy').value,
    auto_select_rule: $('#selAutoRule').value,
    video_exts: $('#inpExts').value.split(',').map(s => s.trim().replace(/^\./, '')).filter(Boolean),
    close_prompt: $('#chkClosePrompt').checked,
    keep_srt: $('#chkKeepSrt').checked,
    keep_intermediate: $('#chkKeepIntermediate').checked,
    recursive: $('#chkRecursive').checked,
  }, extra);
  const r = await api('/api/save_settings', patch);
  if (!applySettings(r.settings)) {
    // 后端响应不完整：保持原状态并明确报错，避免后续 undefined 访问
    throw new Error('服务返回异常（缺少 settings），请确认程序仍在运行后重试');
  }
  S._dirtyKeys = null;
  renderBadges({ fw: r.fw, jasna: r.jasna, ffmpeg: r.ffmpeg });
  return r;
}

async function checkDirs() {
  // Jasna
  const jp = $('#inpJasnaDir').value.trim();
  const jm = $('#jasnaMsg');
  if (!jp) { jm.className = 'msg'; jm.textContent = '模式 1 / 3 需要 Jasna，请填写其程序目录'; }
  else {
    try {
      const r = await api('/api/validate_jasna', { path: jp });
      jm.className = 'msg ' + (r.ok ? (r.warn ? 'warn' : 'ok') : 'err');
      let t = r.ok ? `✔ ${r.msg} · 模型权重 ${r.model_count} 个` + (r.has_ffmpeg ? ' · 内置 ffmpeg 已就绪' : '')
        : '✘ ' + r.msg;
      if (r.ok && r.warn) t += `<br>⚠ ${esc(r.warn)}`;
      jm.innerHTML = t;
    } catch (e) { jm.className = 'msg err'; jm.textContent = '✘ 校验失败：' + e.message; }
  }
  // Faster Whisper
  const p = $('#inpFwDir').value.trim();
  const m = $('#fwMsg');
  if (!p) { m.className = 'msg'; m.textContent = '模式 2 / 3 需要 Faster Whisper，请填写其程序目录'; }
  else {
    const r = await api('/api/validate_fw', { path: p });
    m.className = 'msg ' + (r.ok ? 'ok' : 'err');
    m.textContent = r.ok ? `✔ ${r.msg}` + (r.has_models ? ' · 已找到 models' : ' · 警告：未找到 models 目录')
      + (r.has_generation_config ? ' · generation_config 已就绪' : ' · 未找到 generation_config.json5') : '✘ ' + r.msg;
  }
  const needJasna = modeMeta(S.mode).needJasna;
  const needSub = modeMeta(S.mode).needSub;
  if (needJasna && !jm.classList.contains('ok')) $('#inpJasnaDir').classList.add('req');
  else $('#inpJasnaDir').classList.remove('req');
  if (needSub && !m.classList.contains('ok')) $('#inpFwDir').classList.add('req');
  else $('#inpFwDir').classList.remove('req');
}

/* ---------------- 目录浏览 ---------------- */
async function openBrowse(target) {
  S.browseTarget = target;
  // 先显示弹窗，再异步加载，避免用户看到"点了没反应"
  openModal('#modalBrowse');
  $('#browseList').innerHTML = '<div style="color:var(--tx3)">加载中…</div>';
  try {
    let start;
    if (target === 'fw') start = cfg('fw_dir') || 'C:\\';
    else if (target === 'jasna') start = cfg('jasna_dir') || 'C:\\';
    else start = cfg('folders', [])[0] || 'C:\\';
    // 起始路径可能已失效，回退到盘符列表
    try {
      await browseTo(start);
    } catch (e) {
      start = 'C:\\';
      await browseTo(start);
    }
    const d = await api('/api/drives');
    $('#selDrive').innerHTML = d.drives.map(x => `<option>${esc(x.path)}</option>`).join('');
    $('#selDrive').value = /^[A-Za-z]:\\?$/.test(start) ? start : '';
  } catch (e) {
    $('#browseList').innerHTML = `<div style="color:var(--err)">读取目录失败：${esc(e.message)}</div>`;
  }
}

async function browseTo(path) {
  const r = await api('/api/browse', { path });
  $('#inpBrowsePath').value = r.path;
  const up = r.parent && r.parent !== r.path
    ? `<div data-path="${esc(r.parent)}" data-dir="1">⬆ 上级目录</div>` : '';
  const ds = r.dirs.map(x => `<div data-path="${esc(x.path)}" data-dir="1">📁 ${esc(x.name)}</div>`).join('');
  const fs = r.files.map(x => {
    const isTargetExe = (S.browseTarget === 'fw' && /infer\.exe$/i.test(x.name))
      || (S.browseTarget === 'jasna' && /jasna\.exe$/i.test(x.name));
    return `<div data-path="${esc(x.path)}" data-exe="1" class="${isTargetExe ? 'exe' : ''}">⚙ ${esc(x.name)}</div>`;
  }).join('');
  const tip = (S.browseTarget === 'fw')
    ? '<div style="padding:6px 11px;color:var(--tx3);font-size:11px;border-bottom:1px solid var(--line2)">点击 infer.exe 可跳转到它所在目录</div>'
    : (S.browseTarget === 'jasna'
      ? '<div style="padding:6px 11px;color:var(--tx3);font-size:11px;border-bottom:1px solid var(--line2)">点击 jasna.exe 可跳转到它所在目录</div>'
      : '');
  $('#browseList').innerHTML = tip + (up + ds + fs) ||
    `<div style="padding:11px;color:var(--tx3)">此目录下没有子文件夹${r.msg ? ' · ' + esc(r.msg) : ''}</div>`;
  $$('#browseList div[data-path]').forEach(el => el.onclick = () => {
    if (el.dataset.dir) return browseTo(el.dataset.path);
    // 点了 exe -> 跳到它所在目录
    const parent = el.dataset.path.replace(/[\\/][^\\/]*$/, '');
    $('#inpBrowsePath').value = parent;
    browseTo(parent);
  });
}

/* ---------------- 执行 ---------------- */
async function start() {
  const paths = selectedPaths();
  if (!paths.length) return toast('请先勾选要处理的视频文件', 'warn');
  const meta = modeMeta(S.mode);
  // 按模式校验依赖：只跑字幕不必配Jasna，反之亦然
  if (meta.needJasna) {
    const jv = await api('/api/validate_jasna', { path: cfg('jasna_dir', '') });
    if (!jv.ok) {
      toast('Jasna 未正确配置：' + jv.msg, 'err');
      openModal('#modalSettings'); checkDirs(); return;
    }
  }
  if (meta.needSub) {
    const fwv = await api('/api/validate_fw', { path: cfg('fw_dir', '') });
    if (!fwv.ok) {
      toast('Faster Whisper 未正确配置：' + fwv.msg, 'err');
      openModal('#modalSettings'); checkDirs(); return;
    }
  }
  // 参数合法性预检（有 error 级问题就不启动，避免跑到一半失败）
  if (meta.needJasna) {
    try {
      const ck = await api('/api/jasna_check', { params: S.jasnaParams });
      const bad = (ck.issues || []).filter(i => i.level === 'error');
      if (bad.length) { toast(bad[0].text, 'err'); switchTab('jasna'); return; }
    } catch (e) { /* 检查失败不阻塞 */ }
  }
  try {
    await api('/api/start', {
      paths, params: S.params, jasna_params: S.jasnaParams, mode: S.mode,
    });
    $('#btnStart').disabled = true; $('#btnStop').disabled = false;
    toast(`开始处理 ${paths.length} 个文件（${meta.label}）`, 'ok');
    $('#jobList').innerHTML = '';
  } catch (e) { toast(e.message, 'err'); }
}

async function stop() {
  await api('/api/stop');
  $('#btnStop').disabled = true;
  toast('已发送停止指令', 'warn');
}

function renderJobs(state) {
  S.job = state;
  const tb = $('#jobList');
  const arr = state.jobs || [];
  // 用 div 行而非 table：表格的 tbody 上overflow:auto 不生效，
  // 会导致文件多时列表撑破容器且无法滚动。
  tb.innerHTML = arr.length ? arr.map(j => `
    <div class="job-row ${j.status}">
      <span class="j-st"><span class="st ${j.status}">${statusText(j.status)}</span></span>
      <span class="j-name" title="${esc(j.src)}">${esc(j.name)}</span>
      <span class="j-prog"><i class="bar"><i style="width:${j.progress}%"></i></i></span>
      <span class="j-stage">${esc(j.stage || '')}${j.elapsed ? ' · ' + j.elapsed + 's' : ''}</span>
    </div>`).join('')
    : '<div class="j-empty">暂无任务</div>';

  const done = arr.filter(j => j.status === 'done' || j.status === 'failed' || j.status === 'skipped').length;
  const total = arr.length;
  $('#overallBar').style.width = total ? (done / total * 100) + '%' : '0%';
  const s = state.summary || {};
  $('#sumBar').innerHTML = total
    ? `共 <b>${total}</b> ｜ 成功 <b>${s.done || 0}</b> ｜ 失败 <b class="f">${s.failed || 0}</b> ｜ 跳过 <b class="s">${s.skipped || 0}</b>`
    : '';
  const busy = state.running;
  $('#btnStart').disabled = busy || !selectedPaths().length;
  $('#btnStop').disabled = !busy;
}
function statusText(s) {
  return { pending: '等待', running: '进行中', done: '成功', failed: '失败', skipped: '跳过', canceled: '取消' }[s] || s;
}

/* ---------------- 日志轮询 ---------------- */
function startPolling() {
  if (S.pollTimer) return;
  S.pollTimer = setInterval(async () => {
    try {
      const [lg, js] = await Promise.all([
        api('/api/job/logs?since=' + S.logSeq),
        api('/api/job/state'),
      ]);
      if (lg.lines.length) {
        S.logSeq = lg.seq;
        const box = $('#logBox');
        const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
        box.insertAdjacentHTML('beforeend', lg.lines.map(l =>
          `<div class="log-line ${esc(l.level)}"><span class="tm">${esc(l.t)}</span>${esc(l.text)}</div>`).join(''));
        if (atBottom) box.scrollTop = box.scrollHeight;
      }
      renderJobs(js);
    } catch (e) { /* 服务未就绪 */ }
  }, 900);
}

/* ---------------- 配置方案操作 ---------------- */
function loadProfile(name) {
  const item = S.profiles.find(p => p.name === name);
  if (!item) return toast('配置不存在', 'err');
  if (S.dirty && !confirm('当前参数已修改但未保存为配置，加载「' + name + '」将丢弃这些修改。继续？')) return;
  S.params = JSON.parse(JSON.stringify(item.params));
  S.baselineParams = JSON.parse(JSON.stringify(item.params));
  S.lastProfile = name;
  api('/api/save_settings', { last_profile: name }).catch(() => { });
  renderParams(); renderProfiles(); markDirty();
  toast('已加载配置：' + name, 'ok');
}

function loadJasnaProfile(name) {
  const item = S.jasnaProfiles.find(p => p.name === name);
  if (!item) return toast('配置不存在', 'err');
  if (S.jasnaDirty && !confirm('当前 Jasna 参数已修改但未保存为配置，加载「' + name + '」将丢弃这些修改。继续？')) return;
  S.jasnaParams = JSON.parse(JSON.stringify(item.params));
  S.jasnaBaselineParams = JSON.parse(JSON.stringify(item.params));
  S.lastJasnaProfile = name;
  api('/api/save_settings', { last_jasna_profile: name }).catch(() => { });
  renderJasnaParams(); renderJasnaProfiles(); markJasnaDirty();
  toast('已加载 Jasna 配置：' + name, 'ok');
}

async function saveProfileFlow(name, overwrite) {
  const kind = S.saveKind;
  try {
    if (kind === 'jasna') {
      const r = await api('/api/jasna_profile/save', { name, params: S.jasnaParams, overwrite });
      S.jasnaProfiles = r.profiles;
      S.lastJasnaProfile = r.last_jasna_profile || name;
      S.jasnaBaselineParams = JSON.parse(JSON.stringify(S.jasnaParams));
      renderJasnaProfiles(); markJasnaDirty();
    } else {
      const r = await api('/api/profile/save', { name, params: S.params, overwrite });
      S.profiles = r.profiles;
      S.lastProfile = name;
      S.baselineParams = JSON.parse(JSON.stringify(S.params));
      renderProfiles(); markDirty();
    }
    closeModal('#modalSaveProfile');
    toast('配置已保存：' + name, 'ok');
  } catch (e) {
    const msg = $('#saveProfileMsg');
    msg.className = 'msg err'; msg.textContent = e.message;
  }
}

/* ---------------- 关闭流程 ---------------- */
async function doQuit() {
  // 只持久化"内存里已经变更过"的字段。
  // 注意：绝不能在这里调用 saveSettings({})——它会从设置弹窗的 DOM 读值，
  // 而弹窗可能从未打开过（DOM 全空），会把用户已保存的 fw_dir/video_exts 等清空。
  const patch = { last_profile: S.lastProfile, last_jasna_profile: S.lastJasnaProfile };
  if (S._dirtyKeys) {
    Object.assign(patch, S._dirtyKeys);
    S._dirtyKeys = null;
  }
  await api('/api/save_settings', patch).then(r => { applySettings(r.settings); })
    .catch(() => { });

  // 任一参数有修改且未保存 -> 询问（字幕与 Jasna 参数各自独立判断）
  const askSub = S.dirty && cfg('close_prompt', true);
  const askJasna = S.jasnaDirty && cfg('close_prompt', true)
    && modeMeta(S.mode).needJasna;
  if (askSub || askJasna) {
    $('#quitRows').innerHTML =
      (askSub ? `<label class="fld"><span>字幕参数保存为</span>
        <input id="inpQuitProfile" type="text" placeholder="${esc(suggestName())}"></label>` : '') +
      (askJasna ? `<label class="fld"><span>Jasna 参数保存为</span>
        <input id="inpQuitJasna" type="text" placeholder="${esc(suggestJasnaName())}"></label>` : '');
    openModal('#modalQuit');
    return;
  }
  finishQuit();
}
function suggestName() {
  const base = S.lastProfile || '我的配置';
  const p = S.params;
  const tag = p.device === 'cpu' ? 'CPU' : (p.compute_type === 'int8_float16' ? 'GPU低显存' : (p.enable_batching ? 'GPU加速' : 'GPU'));
  return `${base.replace(/\s*·.*$/, '')} ${tag} 副本`;
}
function suggestJasnaName() {
  const base = (S.lastJasnaProfile || 'Jasna 配置').replace(/\s*·.*$/, '');
  const clip = S.jasnaParams.max_clip_size;
  const sec = S.jasnaParams.secondary_restoration;
  const tag = sec && sec !== 'none' ? '超分' : (clip >= 180 ? '高质量' : (clip <= 60 ? '快速' : '标准'));
  return `${base} ${tag} 副本`;
}
async function finishQuit() {
  const subName = ($('#inpQuitProfile') ? $('#inpQuitProfile').value.trim() : '');
  const jasnaName = ($('#inpQuitJasna') ? $('#inpQuitJasna').value.trim() : '');
  // 1. 先落盘所有设置与（可选的）新配置
  try {
    await api('/api/shutdown_prompt', {
      settings: {
        jasna_dir: cfg('jasna_dir'), fw_dir: cfg('fw_dir'), ffmpeg_path: cfg('ffmpeg_path'),
        output_dir: cfg('output_dir'), keep_srt: cfg('keep_srt', true),
        keep_intermediate: cfg('keep_intermediate', false),
        recursive: cfg('recursive', true), close_prompt: cfg('close_prompt', true),
        existing_output_policy: cfg('existing_output_policy', 'number'),
        auto_select_rule: cfg('auto_select_rule', 'smart'), video_exts: cfg('video_exts', []),
        folders: cfg('folders', []), selected: cfg('selected', {}), mode: cfg('mode', 2),
        last_profile: subName || S.lastProfile,
        last_jasna_profile: jasnaName || S.lastJasnaProfile,
      },
      params: S.params,
      save_profile: !!subName,
      profile_name: subName,
      jasna_params: S.jasnaParams,
      save_jasna_profile: !!jasnaName,
      jasna_profile_name: jasnaName,
    });
  } catch (e) { /* 服务可能已关 */ }

  // 2. 关闭浏览器标签页（pywebview 下 window.close 无效，走下面的通道）
  try { window.close(); } catch (e) { }

  // 3. 通知主进程退出：优先用 pywebview 桥接，否则走 HTTP
  let sent = false;
  if (window.pywebview && window.pywebview.api && window.pywebview.api.quit) {
    try { window.pywebview.api.quit(); sent = true; } catch (e) { }
  }
  if (!sent) {
    try {
      await fetch('/api/shutdown', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    } catch (e) { }
  }
  // 提示后若仍未退出（极端情况），给出手动指引
  toast('设置已保存，程序正在退出…', 'ok');
  setTimeout(() => toast('若窗口未自动关闭，请按 Alt+F4 关闭，或在浏览器中直接关闭本页', 'warn'), 2500);
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $('#inpFolder').addEventListener('keydown', e => { if (e.key === 'Enter') addFolder($('#inpFolder').value); });
  $('#btnAddFolder').onclick = () => addFolder($('#inpFolder').value);
  $('#btnBrowse').onclick = () => openBrowse('folder');
  $('#btnScanAll').onclick = () => scanAll();
  $('#btnAuto').onclick = () => applySelection('auto');
  $('#btnInvert').onclick = () => applySelection('invert');
  $('#btnNone').onclick = () => applySelection('none');
  $('#chkAll').onchange = (e) => applySelection(e.target.checked ? 'all' : 'none');
  $('#chkRecursive').onchange = (e) => {
    applySettings(Object.assign({}, S.settings, { recursive: e.target.checked }));
    persistSettings({ recursive: e.target.checked });
    scanAll();
  };
  $('#chkKeepSrt').onchange = (e) => {
    applySettings(Object.assign({}, S.settings, { keep_srt: e.target.checked }));
    persistSettings({ keep_srt: e.target.checked });
  };

  $('#btnLoadProfile').onclick = () => loadProfile($('#selProfile').value);
  $('#selProfile').onchange = (e) => {
    const item = S.profiles.find(p => p.name === e.target.value);
    $('#profileHelp').innerHTML = item ? (item.builtin ? '<b>内置配置</b>' : '<b>我的配置</b>') +
      (item.saved_at ? ' · ' + esc(item.saved_at) : '') + (item.help ? ' — ' + esc(item.help) : '') : '';
  };
  $('#btnSaveProfile').onclick = () => {
    S.saveKind = 'sub';
    $$('#saveProfileKind .seg').forEach(b => b.classList.toggle('active', b.dataset.kind === 'sub'));
    $('#inpProfileName').value = suggestName();
    $('#chkOverwrite').checked = false;
    $('#saveProfileMsg').textContent = '';
    openModal('#modalSaveProfile');
    setTimeout(() => $('#inpProfileName').select(), 60);
  };
  $('#btnConfirmSaveProfile').onclick = () => saveProfileFlow($('#inpProfileName').value.trim(), $('#chkOverwrite').checked);
  $('#inpProfileName').addEventListener('keydown', e => {
    if (e.key === 'Enter') saveProfileFlow($('#inpProfileName').value.trim(), $('#chkOverwrite').checked);
  });
  $('#btnRenameProfile').onclick = async () => {
    const old = $('#selProfile').value;
    const nn = prompt('新的配置名称', old);
    if (!nn) return;
    try {
      const r = await api('/api/profile/rename', { name: old, new_name: nn });
      S.profiles = r.profiles; S.lastProfile = nn;
      S.baselineParams = JSON.parse(JSON.stringify(S.params));
      renderProfiles(); markDirty(); toast('已重命名', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#btnDelProfile').onclick = async () => {
    const name = $('#selProfile').value;
    if (!confirm(`确定删除配置「${name}」？`)) return;
    try {
      const r = await api('/api/profile/delete', { name });
      S.profiles = r.profiles; renderProfiles(); toast('已删除', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#btnResetParams').onclick = () => {
    const isJasna = $('#paneJasna').classList.contains('active');
    if (!confirm(`恢复${isJasna ? ' Jasna ' : '字幕 '}参数为内置默认值？当前修改将丢失。`)) return;
    if (isJasna) {
      S.jasnaParams = JSON.parse(JSON.stringify(S.jasnaDefaults));
      renderJasnaParams(); markJasnaDirty();
    } else {
      S.params = JSON.parse(JSON.stringify(S.defaults));
      renderParams(); markDirty();
    }
    toast('已恢复默认参数', 'ok');
  };

  $('#btnLoadJasnaProfile').onclick = () => loadJasnaProfile($('#selJasnaProfile').value);
  $('#selJasnaProfile').onchange = (e) => {
    const item = S.jasnaProfiles.find(p => p.name === e.target.value);
    $('#jasnaProfileHelp').innerHTML = item ? (item.builtin ? '<b>内置配置</b>' : '<b>我的配置</b>') +
      (item.saved_at ? ' · ' + esc(item.saved_at) : '') + (item.help ? ' — ' + esc(item.help) : '') : '';
  };
  $('#btnSaveJasnaProfile').onclick = () => {
    S.saveKind = 'jasna';
    $$('#saveProfileKind .seg').forEach(b => b.classList.toggle('active', b.dataset.kind === 'jasna'));
    $('#inpProfileName').value = suggestJasnaName();
    $('#chkOverwrite').checked = false;
    $('#saveProfileMsg').textContent = '';
    openModal('#modalSaveProfile');
    setTimeout(() => $('#inpProfileName').select(), 60);
  };
  $('#btnRenameJasnaProfile').onclick = async () => {
    const old = $('#selJasnaProfile').value;
    const nn = prompt('新的配置名称', old);
    if (!nn) return;
    try {
      const r = await api('/api/jasna_profile/rename', { name: old, new_name: nn });
      S.jasnaProfiles = r.profiles; S.lastJasnaProfile = nn;
      S.jasnaBaselineParams = JSON.parse(JSON.stringify(S.jasnaParams));
      renderJasnaProfiles(); markJasnaDirty(); toast('已重命名', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#btnDelJasnaProfile').onclick = async () => {
    const name = $('#selJasnaProfile').value;
    if (!confirm(`确定删除 Jasna 配置「${name}」？`)) return;
    try {
      const r = await api('/api/jasna_profile/delete', { name });
      S.jasnaProfiles = r.profiles; renderJasnaProfiles(); toast('已删除', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };

  $('#btnSettings').onclick = () => { renderSettingsForm(); openModal('#modalSettings'); checkDirs(); };
  $('#btnJasnaBrowse').onclick = () => openBrowse('jasna');
  $('#btnJasnaAuto').onclick = async () => {
    const r = await api('/api/pick_default_jasna');
    if (r.ok) { $('#inpJasnaDir').value = r.path; checkDirs(); toast('已自动找到：' + r.path, 'ok'); }
    else toast(r.msg, 'warn');
  };
  $('#inpJasnaDir').addEventListener('input', debounce(checkDirs, 400));
  $('#btnFwBrowse').onclick = () => openBrowse('fw');
  $('#btnFwAuto').onclick = async () => {
    const r = await api('/api/pick_default_fw');
    if (r.ok) { $('#inpFwDir').value = r.path; checkDirs(); toast('已自动找到：' + r.path, 'ok'); }
    else toast(r.msg, 'warn');
  };
  $('#inpFwDir').addEventListener('input', debounce(checkDirs, 400));
  $('#selAutoRule').onchange = (e) => {
    applySettings(Object.assign({}, S.settings, { auto_select_rule: e.target.value }));
    persistSettings({ auto_select_rule: e.target.value });
    scanAll();
  };
  $('#chkKeepIntermediate').onchange = (e) => {
    applySettings(Object.assign({}, S.settings, { keep_intermediate: e.target.checked }));
    persistSettings({ keep_intermediate: e.target.checked });
  };
  $('#btnSaveSettings').onclick = async () => {
    try {
      await saveSettings();
      renderFolderChips(); scanAll(true);
      closeModal('#modalSettings'); toast('设置已保存', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };

  // Tab 切换
  $$('#paramTabs .tab').forEach(t => t.onclick = () => {
    if (t.classList.contains('disabled')) {
      const need = t.dataset.tab === 'jasna' ? 'Jasna' : 'Faster Whisper';
      return toast(`当前模式「${modeMeta(S.mode).label}」不需要 ${need} 参数`, 'warn');
    }
    switchTab(t.dataset.tab);
  });

  // 另存配置弹窗：切换字幕 / Jasna
  $$('#saveProfileKind .seg').forEach(b => b.onclick = () => {
    S.saveKind = b.dataset.kind;
    $$('#saveProfileKind .seg').forEach(x => x.classList.toggle('active', x === b));
    $('#inpProfileName').value = (S.saveKind === 'jasna') ? suggestJasnaName() : suggestName();
  });

  $('#selDrive').onchange = (e) => e.target.value && browseTo(e.target.value);
  $('#btnGoPath').onclick = () => browseTo($('#inpBrowsePath').value.trim());
  $('#btnUpDir').onclick = () => browseTo($('#inpBrowsePath').value.replace(/[\\/][^\\/]*$/, ''));
  $('#btnPickDir').onclick = () => {
    const p = $('#inpBrowsePath').value.trim();
    if (S.browseTarget === 'fw') { $('#inpFwDir').value = p; closeModal('#modalBrowse'); checkDirs(); }
    else if (S.browseTarget === 'jasna') { $('#inpJasnaDir').value = p; closeModal('#modalBrowse'); checkDirs(); }
    else { closeModal('#modalBrowse'); addFolder(p); }
  };
  $('#btnPickFw').onclick = () => {
    $('#inpFwDir').value = $('#inpBrowsePath').value.trim();
    closeModal('#modalBrowse'); openModal('#modalSettings'); checkDirs();
  };
  $('#btnPickJasna').onclick = () => {
    $('#inpJasnaDir').value = $('#inpBrowsePath').value.trim();
    closeModal('#modalBrowse'); openModal('#modalSettings'); checkDirs();
  };
  $('#chkPickExe').onchange = async (e) => {
    if (!e.target.checked) return;
    e.target.checked = false;
    const r = await api('/api/browse', { path: $('#inpBrowsePath').value.trim() });
    const re = S.browseTarget === 'jasna' ? /jasna\.exe$/i : /infer\.exe$/i;
    const hit = (r.files || []).find(f => re.test(f.name));
    if (hit) {
      if (S.browseTarget === 'jasna') { $('#inpJasnaDir').value = r.path; toast('已选中 jasna.exe 所在目录', 'ok'); }
      else { $('#inpFwDir').value = r.path; toast('已选中 infer.exe 所在目录', 'ok'); }
      checkDirs();
    } else toast('当前目录没有' + (S.browseTarget === 'jasna' ? ' jasna.exe' : ' infer.exe'), 'warn');
  };

  $('#btnPreview').onclick = async () => {
    const p = selectedPaths()[0];
    if (!p) return toast('请先勾选一个文件', 'warn');
    try {
      const r = await api('/api/preview_cmd', {
        path: p, params: S.params, jasna_params: S.jasnaParams, mode: S.mode,
      });
      const meta = modeMeta(S.mode);
      let head = `处理模式：${meta.label}（输出加 ${meta.suffix}）\n`;
      if (r.skip) head += `⚠ 该文件会被跳过：${r.skip_reason}\n`;
      head += `→ 输出文件：\n${r.dst}\n\n`;
      const issues = (r.issues || []);
      const warn = issues.length
        ? '\n\n⚠ 参数提示：\n' + issues.map(i => (i.level === 'error' ? '⛔ ' : '⚠ ') + i.text).join('\n')
        : '';
      $('#previewText').textContent = head + r.cmd + warn;
      openModal('#modalPreview');
    } catch (e) { toast(e.message, 'err'); }
  };

  $('#btnStart').onclick = start;
  $('#btnStop').onclick = stop;
  $('#btnClearDone').onclick = async () => { await api('/api/job/clear_finished'); };
  $('#btnClearLog').onclick = () => { $('#logBox').innerHTML = ''; };
  $('#btnCloseApp').onclick = doQuit;

  $('#btnQuitSave').onclick = () => { closeModal('#modalQuit'); finishQuit(); };
  $('#btnQuitDiscard').onclick = () => { closeModal('#modalQuit'); finishQuit(); };
  $('#btnQuitCancel').onclick = () => closeModal('#modalQuit');

  $$('[data-close]').forEach(b => b.onclick = () => b.closest('.modal').classList.add('hidden'));
  $$('.modal').forEach(m => m.addEventListener('mousedown', e => { if (e.target === m && m.id !== 'modalQuit') m.classList.add('hidden'); }));

  window.addEventListener('beforeunload', (e) => {
    if ((S.dirty || S.jasnaDirty) && cfg('close_prompt', true)) { e.preventDefault(); e.returnValue = ''; }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $$('.modal').forEach(m => { if (m.id !== 'modalQuit') m.classList.add('hidden'); });
    if (e.ctrlKey && e.key === 'Enter') start();
  });
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

boot().catch(e => { document.body.innerHTML = `<div style="padding:40px;color:#ff5c5c;font-family:Consolas">启动失败：${esc(e.message)}</div>`; });
