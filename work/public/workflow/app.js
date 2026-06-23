import { WorkflowAPI, tokenStore } from './api.js';

const $ = (id) => document.getElementById(id);
const state = { projects: [], projectId: '', project: null, sources: [], assets: [], storyboard: [], generation: {scene_plans: [], tasks: []} };

function escapeHtml(value){ return String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s])); }
function debug(data){ $('debug').textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2); }
function status(id, text, type=''){ const el=$(id); if(el){ el.textContent=text; el.className=`statusbar ${type}`; } }
function requireProject(){ if(!state.projectId) throw new Error('请先选择项目'); }

function bindTabs(){
  document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.section').forEach(x => x.classList.remove('active'));
    btn.classList.add('active');
    $(btn.dataset.section).classList.add('active');
  }));
}

async function login(){
  const phone = $('login-phone').value.trim();
  const password = $('login-password').value;
  const data = await WorkflowAPI.login(phone, password);
  status('login-status', '登录成功', 'success');
  debug(data);
  await loadProjects();
}

async function loadProjects(){
  if(!tokenStore.get()) { status('login-status', '请先登录', 'error'); return; }
  const data = await WorkflowAPI.projects();
  state.projects = data.projects || [];
  $('project-list').innerHTML = state.projects.map(p => `<button class="project ${p.id===state.projectId?'active':''}" data-project-id="${p.id}"><b>${escapeHtml(p.name)}</b><span>${escapeHtml(p.project_type || '宣传片')} · ${escapeHtml(p.status || '')}</span></button>`).join('') || '<p class="empty">暂无项目</p>';
  document.querySelectorAll('[data-project-id]').forEach(btn => btn.addEventListener('click', () => selectProject(btn.dataset.projectId)));
  debug(data);
}

async function createProject(){
  const data = await WorkflowAPI.createProject({
    name: `可信宣传片项目 ${new Date().toLocaleString()}`,
    type: '宣传片', scenes: 6, data_level: 'L1', publish_channel: '官网 / 视频号', aspect_ratio: '16:9', duration_target: '90s',
    script: '一、开场：以真实业务场景建立可信度。\n二、背景：说明项目来源、服务对象和公共价值。\n三、做法：呈现工作流程和一线行动。\n四、成效：以可核查资料展示成果。\n五、结尾：回到服务承诺和发布复核。'
  });
  state.projectId = data.id;
  debug(data);
  await loadProjects();
  await selectProject(data.id);
}

async function selectProject(projectId){
  state.projectId = projectId;
  document.querySelectorAll('[data-project-id]').forEach(x => x.classList.toggle('active', x.dataset.projectId === projectId));
  const [project, brief, sources, assets, readiness, generation] = await Promise.all([
    WorkflowAPI.project(projectId), WorkflowAPI.brief(projectId), WorkflowAPI.sources(projectId), WorkflowAPI.assets(projectId), WorkflowAPI.readiness(projectId), WorkflowAPI.generationQueue(projectId)
  ]);
  state.project = project.project;
  state.sources = sources.sources || [];
  state.assets = assets.assets || [];
  state.generation = generation || {scene_plans: [], tasks: []};
  state.storyboard = (project.project?.scenes || []).filter(s => s.scene_goal).map(sceneToStoryboard);
  $('page-title').textContent = state.project?.name || '真实工作流';
  renderBrief(brief.project || {});
  renderSources();
  renderAssets();
  renderReadiness(readiness);
  renderStoryboard(state.storyboard);
  renderGenerationQueue();
  renderNextActions(readiness);
  debug({project, brief, sources, assets, readiness, generation});
}

function renderBrief(project){
  $('brief-objective').value = project.objective || '';
  $('brief-audience').value = project.target_audience || '';
  $('brief-tone').value = project.tone || '';
  $('brief-required').value = project.required_messages || '';
  $('brief-forbidden').value = project.forbidden_expressions || '';
  $('story-style').value = project.tone || '纪实、克制、可信、公共服务宣传片';
}

async function saveBrief(){
  requireProject();
  const payload = { objective: $('brief-objective').value, target_audience: $('brief-audience').value, tone: $('brief-tone').value, required_messages: $('brief-required').value, forbidden_expressions: $('brief-forbidden').value };
  const data = await WorkflowAPI.saveBrief(state.projectId, payload);
  status('brief-status', '已保存', 'success'); debug(data); await refreshCurrent();
}

function renderReadiness(data){
  const c = data.counts || {};
  const metrics = [['资料', c.sources || 0, c.sources && c.parsed_sources === c.sources ? 'ok' : 'warn'], ['已解析资料', c.parsed_sources || 0, c.parsed_sources ? 'ok' : 'warn'], ['视觉资产', c.assets || 0, c.assets && c.authorized_assets === c.assets ? 'ok' : 'warn'], ['结构化镜头', c.structured_scenes || 0, c.structured_scenes ? 'ok' : 'bad']];
  $('readiness-metrics').innerHTML = metrics.map(m => `<div class="metric"><span>${m[0]}</span><b class="${m[2]}">${m[1]}</b></div>`).join('');
  const blockers = data.blockers || [];
  $('readiness-blockers').innerHTML = blockers.length ? blockers.map(b => `<div class="blocker">${escapeHtml(b)}</div>`).join('') : '<div class="blocker pass">当前没有阻断项，可以继续生成或交付。</div>';
}

function renderNextActions(data){
  const blockers = data.blockers || [];
  const actions = blockers.length ? blockers : ['完善视觉资产授权', '生成结构化分镜', '创建图片/视频生成任务', '发布前运行自检并导出证据包'];
  $('next-actions').innerHTML = actions.map((x,i) => `<div class="source-card"><div class="source-head"><div class="trusted-mark ${i===0?'risk-mark':''}">${i+1}</div><div><b>${escapeHtml(x)}</b><small>系统根据当前项目状态自动给出</small></div></div></div>`).join('');
}

function renderSources(){
  $('source-list').innerHTML = state.sources.map(s => `<div class="source-card" data-source-card="${s.id}"><div class="source-head"><div class="trusted-mark">S</div><div><b>${escapeHtml(s.title || s.original_name || '未命名资料')}</b><small>${escapeHtml(s.file_type || s.source_type || '')} · ${escapeHtml(s.parse_status || 'pending')}</small></div></div><div><span class="pill ${s.can_quote?'ok':'bad'}">${s.can_quote?'可引用':'不可引用'}</span><span class="pill ${s.can_visualize?'ok':'warn'}">${s.can_visualize?'可视化':'不建议可视化'}</span><span class="pill">${escapeHtml(s.source_authority_level || 'internal')}</span><span class="pill ${s.sensitive_level==='high'?'bad':s.sensitive_level==='medium'?'warn':'ok'}">${escapeHtml(s.sensitive_level || 'normal')}</span></div><div class="mini-grid" style="margin-top:10px"><div class="field"><label>权威等级</label><select data-field="source_authority_level"><option value="official">官方资料</option><option value="internal">内部资料</option><option value="media">媒体资料</option><option value="user_input">用户输入</option></select></div><div class="field"><label>敏感等级</label><select data-field="sensitive_level"><option value="normal">普通</option><option value="medium">中等</option><option value="high">高敏感</option></select></div><div class="field"><label>来源责任人</label><input data-field="source_owner" value="${escapeHtml(s.source_owner || '')}"></div></div><div class="mini-grid"><div class="field"><label>是否可引用</label><select data-field="can_quote"><option value="true">可引用</option><option value="false">不可引用</option></select></div><div class="field"><label>是否可视化</label><select data-field="can_visualize"><option value="true">可视化</option><option value="false">不可视化</option></select></div><div class="field"><label>引用是否必需</label><select data-field="citation_required"><option value="true">必需</option><option value="false">非必需</option></select></div></div><div class="field"><label>可信度备注</label><textarea data-field="notes">${escapeHtml(s.notes || '')}</textarea></div><div class="table-actions"><button class="btn" data-parse-source="${s.id}">解析该资料</button><button class="btn primary" data-save-source="${s.id}">保存可信度</button></div></div>`).join('') || '<div class="card"><div class="card-body"><p class="empty">暂无资料。先上传政策文件、甲方确认稿、图片或视频参考。</p></div></div>';
  state.sources.forEach(s => { const card = document.querySelector(`[data-source-card="${s.id}"]`); if(!card) return; card.querySelector('[data-field="source_authority_level"]').value = s.source_authority_level || 'internal'; card.querySelector('[data-field="sensitive_level"]').value = s.sensitive_level || 'normal'; card.querySelector('[data-field="can_quote"]').value = String(!!s.can_quote); card.querySelector('[data-field="can_visualize"]').value = String(!!s.can_visualize); card.querySelector('[data-field="citation_required"]').value = String(s.citation_required !== false); });
  document.querySelectorAll('[data-save-source]').forEach(btn => btn.addEventListener('click', () => saveSourceTrust(btn.dataset.saveSource)));
  document.querySelectorAll('[data-parse-source]').forEach(btn => btn.addEventListener('click', () => parseOneSource(btn.dataset.parseSource)));
}

async function saveSourceTrust(sourceId){ const card = document.querySelector(`[data-source-card="${sourceId}"]`); const payload = {}; card.querySelectorAll('[data-field]').forEach(el => payload[el.dataset.field] = el.value); const data = await WorkflowAPI.saveSourceTrust(sourceId, payload); status('source-status', '资料可信度已保存', 'success'); debug(data); await refreshSources(); }
async function parseOneSource(sourceId){ const data = await fetch(`/api/workflow/sources/${sourceId}/parse`, {method:'POST', headers:{Authorization:`Bearer ${tokenStore.get()}`}}).then(async r => { const d = await r.json(); if(!r.ok) throw new Error(d.error || d.message || r.statusText); return d; }); status('source-status', '资料已提交解析', 'success'); debug(data); await refreshSources(); }
async function uploadSource(){ requireProject(); const file = $('source-file').files[0]; if(!file) throw new Error('请选择文件'); const data = await WorkflowAPI.uploadSource(state.projectId, file, $('upload-data-level').value); status('source-status', '资料已上传', 'success'); debug(data); await refreshSources(); }
async function parseAllSources(){ requireProject(); const data = await WorkflowAPI.parseAllSources(state.projectId); status('source-status', '资料批量解析完成', 'success'); debug(data); await refreshCurrent(); }

function renderAssets(){ $('asset-list').innerHTML = state.assets.map(a => `<div class="asset-card"><div class="asset-head"><div class="trusted-mark">A</div><div><b>${escapeHtml(a.title || '未命名资产')}</b><small>${escapeHtml(a.visual_description || '暂无描述')}</small></div></div><span class="pill">${escapeHtml(a.asset_type || 'reference')}</span><span class="pill ${a.auth_status==='authorized'?'ok':a.auth_status==='forbidden'?'bad':'warn'}">${escapeHtml(a.auth_status || 'unchecked')}</span></div>`).join('') || '<p class="empty">暂无资产。建议添加 Logo、关键场景、人物授权照、证书或 B-roll。</p>'; }
async function createAsset(){ requireProject(); const payload = {title:$('asset-title').value || '未命名资产', asset_type:$('asset-type').value, auth_status:$('asset-auth').value, visual_description:$('asset-desc').value}; const data = await WorkflowAPI.createAsset(state.projectId, payload); $('asset-title').value = ''; $('asset-desc').value = ''; status('asset-status', '资产已新增', 'success'); debug(data); await refreshAssets(); }

function sceneToStoryboard(s){ return {scene_id:s.id, scene_name:s.name, scene_goal:s.scene_goal, source_citations:parseJson(s.source_citations_json, []), shot_size:s.shot_size, camera_movement:s.camera_movement, generation_mode:s.generation_mode, visual_subject:s.visual_subject, location:s.location, subtitle_text:s.subtitle_text, image_prompt:'', video_prompt:s.prompt || s.voiceover_text || ''}; }
function parseJson(value, fallback){ try{return JSON.parse(value || '')}catch{return fallback} }
function renderStoryboard(items){ $('storyboard-list').innerHTML = items.map((s,i) => `<div class="shot-card"><div class="shot-head"><div class="trusted-mark">${String(i+1).padStart(2,'0')}</div><div><b>${escapeHtml(s.scene_name || '镜头')}</b><small>${escapeHtml(s.scene_goal || '')}</small></div></div><div class="shot-grid"><div class="kv"><span>资料引用</span><b>${escapeHtml((s.source_citations||[]).join('、') || '待补')}</b></div><div class="kv"><span>景别 / 运镜</span><b>${escapeHtml((s.shot_size||'')+' / '+(s.camera_movement||''))}</b></div><div class="kv"><span>生成模式</span><b>${escapeHtml(s.generation_mode || '')}</b></div><div class="kv"><span>视觉主体</span><b>${escapeHtml(s.visual_subject || '')}</b></div><div class="kv"><span>场景</span><b>${escapeHtml(s.location || '')}</b></div><div class="kv"><span>字幕</span><b>${escapeHtml(s.subtitle_text || '')}</b></div></div><div class="prompt">图片 Prompt：\n${escapeHtml(s.image_prompt || '已保存的旧镜头暂无图片 Prompt，请重新生成结构化分镜。')}\n\n视频 Prompt：\n${escapeHtml(s.video_prompt || '')}</div></div>`).join('') || '<p class="empty">暂无结构化分镜。先生成脚本或直接点击“生成结构化分镜”。</p>'; }
async function generateStoryboard(){ requireProject(); const data = await WorkflowAPI.structuredStoryboard(state.projectId, {style:$('story-style').value || '纪实、克制、可信、公共服务宣传片'}); state.storyboard = data.storyboard || []; renderStoryboard(state.storyboard); debug(data); await refreshCurrent(); }

function renderGenerationQueue(){
  const plans = state.generation.scene_plans || [];
  const tasks = state.generation.tasks || [];
  $('generation-plan-list').innerHTML = plans.map(p => `<div class="source-card"><div class="source-head"><div class="trusted-mark">${String(p.scene_order || '').padStart(2,'0')}</div><div><b>${escapeHtml(p.scene_name || '镜头')}</b><small>${escapeHtml(p.status || '')} · ${escapeHtml(p.generation_mode || '')}</small></div></div><span class="pill ${p.has_image_prompt?'ok':'warn'}">${p.has_image_prompt?'图片 Prompt 已就绪':'缺图片 Prompt'}</span><span class="pill ${p.has_video_prompt?'ok':'warn'}">${p.has_video_prompt?'视频 Prompt 已就绪':'缺视频 Prompt'}</span></div>`).join('') || '<p class="empty">暂无镜头。先创建项目或生成结构化分镜。</p>';
  $('generation-task-list').innerHTML = tasks.map(t => { const adapter = (t.adapter_runs || [])[0] || {}; return `<div class="source-card"><div class="source-head"><div class="trusted-mark ${t.status==='queued'?'':'risk-mark'}">${t.task_type === 'video' ? 'V' : 'I'}</div><div><b>${escapeHtml(t.scene_id || '项目任务')} · ${escapeHtml(t.task_type)}</b><small>${escapeHtml(t.provider || '')} / ${escapeHtml(t.model_name || '')} · ${escapeHtml(t.status || '')} · ${escapeHtml(adapter.adapter_name || '')}</small></div></div><div class="prompt">${escapeHtml(t.prompt || '')}</div></div>`; }).join('') || '<p class="empty">暂无任务。先点击上方按钮创建图片或视频任务。</p>';
}
async function refreshGenerationQueue(){ requireProject(); const data = await WorkflowAPI.generationQueue(state.projectId); state.generation = data; renderGenerationQueue(); debug(data); }
async function createGenerationBatch(taskType){ requireProject(); const data = await WorkflowAPI.createGenerationBatch(state.projectId, {task_type: taskType}); status('generation-status', `已创建 ${data.created?.length || 0} 个任务，跳过 ${data.skipped?.length || 0} 个`, 'success'); debug(data); await refreshGenerationQueue(); }

async function refreshSources(){ const data = await WorkflowAPI.sources(state.projectId); state.sources = data.sources || []; renderSources(); const r = await WorkflowAPI.readiness(state.projectId); renderReadiness(r); renderNextActions(r); }
async function refreshAssets(){ const data = await WorkflowAPI.assets(state.projectId); state.assets = data.assets || []; renderAssets(); const r = await WorkflowAPI.readiness(state.projectId); renderReadiness(r); renderNextActions(r); }
async function refreshCurrent(){ if(state.projectId) await selectProject(state.projectId); }

function bindActions(){
  $('login-btn').addEventListener('click', () => login().catch(e => status('login-status', e.message, 'error')));
  $('reload-btn').addEventListener('click', () => (state.projectId ? refreshCurrent() : loadProjects()).catch(e => debug(e.message)));
  $('new-project-btn').addEventListener('click', () => createProject().catch(e => debug(e.message)));
  $('save-brief-btn').addEventListener('click', () => saveBrief().catch(e => status('brief-status', e.message, 'error')));
  $('upload-source-btn').addEventListener('click', () => uploadSource().catch(e => status('source-status', e.message, 'error')));
  $('parse-sources-btn').addEventListener('click', () => parseAllSources().catch(e => status('source-status', e.message, 'error')));
  $('reload-sources-btn').addEventListener('click', () => refreshSources().catch(e => status('source-status', e.message, 'error')));
  $('create-asset-btn').addEventListener('click', () => createAsset().catch(e => status('asset-status', e.message, 'error')));
  $('generate-storyboard-btn').addEventListener('click', () => generateStoryboard().catch(e => debug(e.message)));
  $('reload-generation-btn').addEventListener('click', () => refreshGenerationQueue().catch(e => status('generation-status', e.message, 'error')));
  $('create-image-batch-btn').addEventListener('click', () => createGenerationBatch('image').catch(e => status('generation-status', e.message, 'error')));
  $('create-video-batch-btn').addEventListener('click', () => createGenerationBatch('video').catch(e => status('generation-status', e.message, 'error')));
  $('create-both-batch-btn').addEventListener('click', () => createGenerationBatch('both').catch(e => status('generation-status', e.message, 'error')));
}

bindTabs();
bindActions();
if(tokenStore.get()) loadProjects().catch(e => debug(e.message));
