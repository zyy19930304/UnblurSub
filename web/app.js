/* FWSubsBatch 前端逻辑 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const S = {
  settings: {}, schema: [], groups: [], defaults: {}, baseline: {},
  profiles: [], files: new Map(),   // path -> item
  params: {}, baselineParams: {},
  lastProfile: '', dirty: false, _dirtyKeys: null,
  job: { jobs: [], summary: {}, running: false },
  logSeq: 0, pollTimer: null, browseTarget: 'folder',
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

  $('#appVer').textContent = 'v' + (st.app.version || '1.0.0') + ' · 参数 ' + S.schema.length + ' 项';
  renderBadges(st);
  renderProfiles();
  renderParams();
  renderFolderChips();
  renderSettingsForm();
  bind();
  updateSelCount();

  if (cfg('folders', []).length) scanAll(true);
  startPolling();
  if (!st.fw.ok) setTimeout(() => { openModal('#modalSettings'); checkFw(); }, 400);
}

/* ---------------- 顶栏状态 ---------------- */
function renderBadges(st) {
  const b = $('#fwBadge');
  if (st.fw && st.fw.ok) {
    b.className = 'badge ok';
    b.textContent = 'Faster Whisper 已就绪';
    b.title = st.settings ? (st.settings.fw_dir || '') : cfg('fw_dir');
  } else {
    b.className = 'badge err';
    b.textContent = 'Faster Whisper 未配置';
    b.onclick = () => { openModal('#modalSettings'); checkFw(); };
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
    <div class="file-row ${it.selected ? 'sel' : ''} ${it.derived ? 'derived' : ''}" data-path="${esc(it.path)}" title="${esc(it.path)}">
      <input type="checkbox" ${it.selected ? 'checked' : ''} data-chk>
      <span class="c-name">${it.derived ? '<span class="tag">已处理</span>' : ''}${esc(it.name)}</span>
      <span class="c-size">${esc(it.size_h)}</span>
      <span class="c-dst">→ ${esc(it.target)}</span>
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
  try { await api('/api/set_selected', { paths, mode }); } catch (e) { /* 静默 */ }
}

async function applySelection(mode) {
  const paths = Array.from(S.files.keys());
  if (!paths.length) return toast('没有文件', 'warn');
  const r = await api('/api/set_selected', { paths, mode });
  // 依据后端返回值刷新勾选状态
  paths.forEach(p => {
    if (S.files.has(p) && r.selected[p] !== undefined) S.files.get(p).selected = r.selected[p];
  });
  renderFiles(); updateSelCount();
  const n = selectedPaths().length;
  toast(({ auto: `自动勾选完成，选中 ${n} 个（已排除 -C / -UC 产物）`, invert: '已反选', none: '已清空勾选', all: `已全选 ${n} 个` })[mode] || '完成', 'ok');
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

function markDirty() {
  S.dirty = JSON.stringify(S.params) !== JSON.stringify(S.baselineParams);
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

/* ---------------- 设置 ---------------- */
function renderSettingsForm() {
  // 用 cfg() 而非直读 S.settings：S.settings 可能因网络抖动被置为 undefined
  $('#inpFwDir').value = cfg('fw_dir');
  $('#inpFfmpeg').value = cfg('ffmpeg_path');
  $('#inpOutDir').value = cfg('output_dir');
  $('#selPolicy').value = cfg('existing_output_policy', 'number');
  $('#selAutoRule').value = cfg('auto_select_rule', 'not_c');
  $('#inpExts').value = cfg('video_exts', []).join(',');
  $('#chkClosePrompt').checked = cfg('close_prompt', true);
  $('#chkKeepSrt').checked = cfg('keep_srt', true);
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
    fw_dir: $('#inpFwDir').value.trim(),
    ffmpeg_path: $('#inpFfmpeg').value.trim(),
    output_dir: $('#inpOutDir').value.trim(),
    existing_output_policy: $('#selPolicy').value,
    auto_select_rule: $('#selAutoRule').value,
    video_exts: $('#inpExts').value.split(',').map(s => s.trim().replace(/^\./, '')).filter(Boolean),
    close_prompt: $('#chkClosePrompt').checked,
    keep_srt: $('#chkKeepSrt').checked,
    recursive: $('#chkRecursive').checked,
  }, extra);
  const r = await api('/api/save_settings', patch);
  if (!applySettings(r.settings)) {
    // 后端响应不完整：保持原状态并明确报错，避免后续 undefined 访问
    throw new Error('服务返回异常（缺少 settings），请确认程序仍在运行后重试');
  }
  S._dirtyKeys = null;
  renderBadges({ fw: r.fw, ffmpeg: r.ffmpeg });
  return r;
}

async function checkFw() {
  const p = $('#inpFwDir').value.trim();
  const m = $('#fwMsg');
  if (!p) { m.className = 'msg err'; m.textContent = '请填写 Faster Whisper 程序目录'; return; }
  const r = await api('/api/validate_fw', { path: p });
  m.className = 'msg ' + (r.ok ? 'ok' : 'err');
  m.textContent = r.ok ? `✔ ${r.msg}` + (r.has_models ? ' · 已找到 models' : ' · 警告：未找到 models 目录')
    + (r.has_generation_config ? ' · generation_config 已就绪' : ' · 未找到 generation_config.json5') : '✘ ' + r.msg;
}

/* ---------------- 目录浏览 ---------------- */
async function openBrowse(target) {
  S.browseTarget = target;
  // 先显示弹窗，再异步加载，避免用户看到"点了没反应"
  openModal('#modalBrowse');
  $('#browseList').innerHTML = '<div style="color:var(--tx3)">加载中…</div>';
  try {
    let start = (target === 'fw') ? (cfg('fw_dir') || 'C:\\') : (cfg('folders', [])[0] || 'C:\\');
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
  const fs = r.files.map(x => `<div data-path="${esc(x.path)}" data-exe="1" class="${/infer\.exe$/i.test(x.path) ? 'exe' : ''}">⚙ ${esc(x.name)}</div>`).join('');
  const tip = (S.browseTarget === 'fw')
    ? '<div style="padding:6px 11px;color:var(--tx3);font-size:11px;border-bottom:1px solid var(--line2)">点击 infer.exe 可跳转到它所在目录</div>'
    : '';
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
  const fwv = await api('/api/validate_fw', { path: cfg('fw_dir', '') });
  if (!fwv.ok) {
    toast('Faster Whisper 未正确配置：' + fwv.msg, 'err');
    openModal('#modalSettings'); checkFw(); return;
  }
  try {
    await api('/api/start', { paths, params: S.params });
    $('#btnStart').disabled = true; $('#btnStop').disabled = false;
    toast(`开始处理 ${paths.length} 个文件`, 'ok');
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

async function saveProfileFlow(name, overwrite) {
  try {
    const r = await api('/api/profile/save', { name, params: S.params, overwrite });
    S.profiles = r.profiles;
    S.lastProfile = name;
    S.baselineParams = JSON.parse(JSON.stringify(S.params));
    renderProfiles(); markDirty();
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
  const patch = { last_profile: S.lastProfile };
  if (S._dirtyKeys) {
    Object.assign(patch, S._dirtyKeys);
    S._dirtyKeys = null;
  }
  await api('/api/save_settings', patch).then(r => { applySettings(r.settings); })
    .catch(() => { });

  // 参数有修改且未保存 -> 询问
  if (S.dirty && cfg('close_prompt', true)) {
    $('#inpQuitProfile').value = suggestName();
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
async function finishQuit(profileName) {
  // 1. 先落盘所有设置与（可选的）新配置
  try {
    await api('/api/shutdown_prompt', {
      settings: {
        fw_dir: cfg('fw_dir'), ffmpeg_path: cfg('ffmpeg_path'),
        output_dir: cfg('output_dir'), keep_srt: cfg('keep_srt', true),
        recursive: cfg('recursive', true), close_prompt: cfg('close_prompt', true),
        existing_output_policy: cfg('existing_output_policy', 'number'),
        auto_select_rule: cfg('auto_select_rule', 'not_c'), video_exts: cfg('video_exts', []),
        folders: cfg('folders', []), selected: cfg('selected', {}),
        last_profile: profileName || S.lastProfile,
      },
      params: S.params,
      save_profile: !!profileName,
      profile_name: profileName || '',
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
    if (!confirm('恢复为内置默认值？当前修改将丢失。')) return;
    S.params = JSON.parse(JSON.stringify(S.defaults));
    renderParams(); markDirty(); toast('已恢复默认参数', 'ok');
  };

  $('#btnSettings').onclick = () => { renderSettingsForm(); openModal('#modalSettings'); checkFw(); };
  $('#btnFwBrowse').onclick = () => openBrowse('fw');
  $('#btnFwAuto').onclick = async () => {
    const r = await api('/api/pick_default_fw');
    if (r.ok) { $('#inpFwDir').value = r.path; checkFw(); toast('已自动找到：' + r.path, 'ok'); }
    else toast(r.msg, 'warn');
  };
  $('#inpFwDir').addEventListener('input', debounce(checkFw, 400));
  $('#btnSaveSettings').onclick = async () => {
    try {
      await saveSettings();
      renderFolderChips(); scanAll(true);
      closeModal('#modalSettings'); toast('设置已保存', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };

  $('#selDrive').onchange = (e) => e.target.value && browseTo(e.target.value);
  $('#btnGoPath').onclick = () => browseTo($('#inpBrowsePath').value.trim());
  $('#btnUpDir').onclick = () => browseTo($('#inpBrowsePath').value.replace(/[\\/][^\\/]*$/, ''));
  $('#btnPickDir').onclick = () => {
    const p = $('#inpBrowsePath').value.trim();
    if (S.browseTarget === 'fw') { $('#inpFwDir').value = p; closeModal('#modalBrowse'); checkFw(); }
    else { closeModal('#modalBrowse'); addFolder(p); }
  };
  $('#btnPickFw').onclick = () => {
    $('#inpFwDir').value = $('#inpBrowsePath').value.trim();
    closeModal('#modalBrowse'); openModal('#modalSettings'); checkFw();
  };
  $('#chkPickExe').onchange = async (e) => {
    if (!e.target.checked) return;
    e.target.checked = false;
    const r = await api('/api/browse', { path: $('#inpBrowsePath').value.trim() });
    const exe = (r.files || []).find(f => /infer\.exe$/i.test(f.name));
    if (exe) { $('#inpFwDir').value = r.path; toast('已选中 infer.exe 所在目录', 'ok'); checkFw(); }
    else toast('当前目录没有 infer.exe', 'warn');
  };

  $('#btnPreview').onclick = async () => {
    const p = selectedPaths()[0];
    if (!p) return toast('请先勾选一个文件', 'warn');
    try {
      const r = await api('/api/preview_cmd', { path: p, params: S.params });
      $('#previewText').textContent = r.cmd + '\n\n→ 输出文件：\n' + r.dst;
      openModal('#modalPreview');
    } catch (e) { toast(e.message, 'err'); }
  };

  $('#btnStart').onclick = start;
  $('#btnStop').onclick = stop;
  $('#btnClearDone').onclick = async () => { await api('/api/job/clear_finished'); };
  $('#btnClearLog').onclick = () => { $('#logBox').innerHTML = ''; };
  $('#btnCloseApp').onclick = doQuit;

  $('#btnQuitSave').onclick = () => { const n = $('#inpQuitProfile').value.trim(); closeModal('#modalQuit'); finishQuit(n || null); };
  $('#btnQuitDiscard').onclick = () => { closeModal('#modalQuit'); finishQuit(null); };
  $('#btnQuitCancel').onclick = () => closeModal('#modalQuit');

  $$('[data-close]').forEach(b => b.onclick = () => b.closest('.modal').classList.add('hidden'));
  $$('.modal').forEach(m => m.addEventListener('mousedown', e => { if (e.target === m && m.id !== 'modalQuit') m.classList.add('hidden'); }));

  window.addEventListener('beforeunload', (e) => {
    if (S.dirty && cfg('close_prompt', true)) { e.preventDefault(); e.returnValue = ''; }
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
