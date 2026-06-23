import { api, tokenStore } from './api.js';

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));

function currentProjectId(){
  const active = document.querySelector('[data-project-id].active');
  if (!active) throw new Error('请先选择项目');
  return active.dataset.projectId;
}

function setStatus(text, type=''){
  const el = $('export-status');
  if (!el) return;
  el.textContent = text;
  el.className = `statusbar ${type}`;
}

function renderSummary(evidence){
  const s = evidence?.summary || {};
  $('export-summary').innerHTML = `
    <div class="grid g3">
      <div class="metric"><span>是否可交付</span><b class="${s.export_ready ? 'ok' : 'bad'}">${s.export_ready ? '可交付' : '需复核'}</b></div>
      <div class="metric"><span>资料 / 镜头</span><b>${s.source_count || 0} / ${s.scene_count || 0}</b></div>
      <div class="metric"><span>锁定候选</span><b class="${s.locked_candidate_count ? 'ok' : 'warn'}">${s.locked_candidate_count || 0}</b></div>
    </div>
    <div class="blockers" style="margin-top:12px">
      <div class="blocker ${s.open_r1_count ? '' : 'pass'}">R1 阻断项：${s.open_r1_count || 0}</div>
      <div class="blocker ${s.open_r2_count ? '' : 'pass'}">R2 警告项：${s.open_r2_count || 0}</div>
    </div>`;
}

function renderExports(exports){
  $('export-list').innerHTML = (exports || []).map(x => `
    <div class="source-card">
      <div class="source-head">
        <div class="trusted-mark ${x.status === 'locked' ? 'ok' : 'risk-mark'}">E</div>
        <div><b>${escapeHtml(x.version_label || x.id)}</b><small>${escapeHtml(x.status)} · ${escapeHtml(x.created_at || '')}</small></div>
      </div>
      <span class="pill ${x.status === 'locked' ? 'ok' : 'warn'}">${escapeHtml(x.status)}</span>
      <span class="pill">AI 标识：${x.ai_label_enabled ? '已启用' : '未启用'}</span>
      <div class="prompt">${escapeHtml(x.package_url || '')}</div>
      <div class="table-actions">
        <button class="btn primary" data-file-export="${x.id}" data-file-kind="zip">下载 ZIP</button>
        <button class="btn" data-file-export="${x.id}" data-file-kind="json">下载 JSON</button>
        <button class="btn" data-file-export="${x.id}" data-file-kind="md">下载说明</button>
        <button class="btn" data-view-export="${x.id}">查看证据 JSON</button>
      </div>
    </div>`).join('') || '<p class="empty">暂无交付包。</p>';
  document.querySelectorAll('[data-view-export]').forEach(btn => btn.addEventListener('click', () => viewExport(btn.dataset.viewExport)));
  document.querySelectorAll('[data-file-export]').forEach(btn => btn.addEventListener('click', () => saveExportFile(btn.dataset.fileExport, btn.dataset.fileKind)));
}

async function createExport(){
  const projectId = currentProjectId();
  const versionLabel = $('export-version')?.value || '';
  const data = await api(`/api/workflow/projects/${projectId}/exports`, {method:'POST', body: JSON.stringify({version_label: versionLabel})});
  setStatus(data.export?.status === 'locked' ? '交付包已锁版。' : '交付包已生成，但仍需复核。', data.export?.status === 'locked' ? 'success' : 'error');
  renderSummary(data.evidence || {});
  const debug = $('debug');
  if (debug) debug.textContent = JSON.stringify(data, null, 2);
  await loadExports();
}

async function loadExports(){
  const projectId = currentProjectId();
  const data = await api(`/api/workflow/projects/${projectId}/exports`);
  setStatus('已刷新交付包记录', 'success');
  renderExports(data.exports || []);
  const debug = $('debug');
  if (debug) debug.textContent = JSON.stringify(data, null, 2);
}

async function viewExport(exportId){
  const data = await api(`/api/workflow/exports/${exportId}/evidence`);
  setStatus('已读取证据 JSON', 'success');
  renderSummary(data.evidence || {});
  const debug = $('debug');
  if (debug) debug.textContent = JSON.stringify(data, null, 2);
}

function filePath(exportId, kind){
  if (kind === 'json') return `/api/workflow/exports/${exportId}/download.json`;
  if (kind === 'md') return `/api/workflow/exports/${exportId}/manifest.md`;
  return `/api/workflow/exports/${exportId}/download.zip`;
}

function fileName(exportId, kind){
  if (kind === 'json') return `${exportId}-evidence.json`;
  if (kind === 'md') return `${exportId}-manifest.md`;
  return `${exportId}-evidence-package.zip`;
}

async function saveExportFile(exportId, kind){
  const response = await fetch(filePath(exportId, kind), {headers: {Authorization: `Bearer ${tokenStore.get()}`}});
  if (!response.ok) throw new Error(`文件生成失败：${response.status}`);
  const fileData = await response.blob();
  const objectUrl = URL.createObjectURL(fileData);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName(exportId, kind);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
  setStatus('交付文件已开始下载', 'success');
}

$('create-export-btn')?.addEventListener('click', () => createExport().catch(e => setStatus(e.message, 'error')));
$('reload-export-btn')?.addEventListener('click', () => loadExports().catch(e => setStatus(e.message, 'error')));
