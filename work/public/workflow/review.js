import { api } from './api.js';

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));

function currentProjectId(){
  const active = document.querySelector('[data-project-id].active');
  if (!active) throw new Error('请先选择项目');
  return active.dataset.projectId;
}

function setStatus(text, type=''){
  const el = $('review-status');
  if (!el) return;
  el.textContent = text;
  el.className = `statusbar ${type}`;
}

function renderSummary(summary){
  const pass = !!summary?.pass;
  $('review-summary').innerHTML = `
    <div class="grid g3">
      <div class="metric"><span>是否允许交付</span><b class="${pass?'ok':'bad'}">${pass?'通过':'阻断'}</b></div>
      <div class="metric"><span>R1 阻断项</span><b class="${summary?.blocking_count ? 'bad' : 'ok'}">${summary?.blocking_count || 0}</b></div>
      <div class="metric"><span>R2 警告项</span><b class="${summary?.warning_count ? 'warn' : 'ok'}">${summary?.warning_count || 0}</b></div>
    </div>`;
}

function severityClass(severity){
  if (severity === 'R1') return 'bad';
  if (severity === 'R2') return 'warn';
  return 'ok';
}

function renderItems(items){
  $('review-item-list').innerHTML = (items || []).map(item => `
    <div class="source-card">
      <div class="source-head">
        <div class="trusted-mark ${severityClass(item.severity)}">${escapeHtml(item.severity)}</div>
        <div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.item_type)} · ${escapeHtml(item.status)}</small></div>
      </div>
      <span class="pill ${severityClass(item.severity)}">${escapeHtml(item.severity)}</span>
      <span class="pill ${item.status === 'open' ? 'warn' : 'ok'}">${escapeHtml(item.status)}</span>
      <div class="prompt">${escapeHtml(item.evidence || '')}</div>
      ${item.status === 'open' ? `<div class="table-actions"><button class="btn primary" data-resolve-review="${item.id}">标记已处理</button></div>` : ''}
    </div>`).join('') || '<p class="empty">暂无审核项。</p>';
  document.querySelectorAll('[data-resolve-review]').forEach(btn => btn.addEventListener('click', () => resolveReview(btn.dataset.resolveReview)));
}

async function runReview(){
  const projectId = currentProjectId();
  const data = await api(`/api/workflow/projects/${projectId}/review/run`, {method:'POST', body: JSON.stringify({})});
  setStatus(data.summary?.pass ? '自检通过，可以进入证据包/导出。' : '自检存在阻断项，请先处理 R1。', data.summary?.pass ? 'success' : 'error');
  renderSummary(data.summary || {});
  renderItems(data.items || []);
  const debug = $('debug');
  if (debug) debug.textContent = JSON.stringify(data, null, 2);
}

async function loadReview(){
  const projectId = currentProjectId();
  const data = await api(`/api/workflow/projects/${projectId}/review/items`);
  setStatus('已刷新自检结果', 'success');
  renderSummary(data.summary || {});
  renderItems(data.items || []);
  const debug = $('debug');
  if (debug) debug.textContent = JSON.stringify(data, null, 2);
}

async function resolveReview(itemId){
  const data = await api(`/api/workflow/review-items/${itemId}/resolve`, {method:'POST', body: JSON.stringify({})});
  setStatus('审核项已标记处理', 'success');
  const debug = $('debug');
  if (debug) debug.textContent = JSON.stringify(data, null, 2);
  await loadReview();
}

$('run-review-btn')?.addEventListener('click', () => runReview().catch(e => setStatus(e.message, 'error')));
$('reload-review-btn')?.addEventListener('click', () => loadReview().catch(e => setStatus(e.message, 'error')));
