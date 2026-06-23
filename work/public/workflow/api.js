export const tokenStore = {
  get(){ return localStorage.getItem('zx_token') || localStorage.getItem('workflow_token') || ''; },
  set(token){ localStorage.setItem('zx_token', token); localStorage.setItem('workflow_token', token); },
};

export async function api(path, options = {}){
  const token = tokenStore.get();
  const headers = options.headers ? {...options.headers} : {};
  if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {...options, headers});
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = {raw: text}; }
  if (!res.ok) throw new Error(data.error || data.message || `${res.status} ${res.statusText}`);
  return data;
}

export const WorkflowAPI = {
  async login(phone, password){
    const data = await api('/api/auth/login', {method:'POST', body: JSON.stringify({phone, password})});
    tokenStore.set(data.token);
    return data;
  },
  me(){ return api('/api/auth/me'); },
  projects(){ return api('/api/projects'); },
  createProject(payload){ return api('/api/projects', {method:'POST', body: JSON.stringify(payload)}); },
  project(projectId){ return api(`/api/projects/${projectId}`); },
  brief(projectId){ return api(`/api/workflow/projects/${projectId}/brief`); },
  saveBrief(projectId, payload){ return api(`/api/workflow/projects/${projectId}/brief`, {method:'PUT', body: JSON.stringify(payload)}); },
  readiness(projectId){ return api(`/api/workflow/projects/${projectId}/readiness`); },
  sources(projectId){ return api(`/api/workflow/projects/${projectId}/sources/trust`); },
  saveSourceTrust(sourceId, payload){ return api(`/api/workflow/sources/${sourceId}/trust`, {method:'PUT', body: JSON.stringify(payload)}); },
  parseAllSources(projectId){ return api(`/api/workflow/projects/${projectId}/sources/parse-all`, {method:'POST', body: JSON.stringify({})}); },
  uploadSource(projectId, file, dataLevel='L1'){
    const fd = new FormData();
    fd.append('file', file);
    fd.append('project_id', projectId);
    fd.append('data_level', dataLevel);
    return api('/api/upload', {method:'POST', body: fd, headers:{}});
  },
  assets(projectId){ return api(`/api/workflow/projects/${projectId}/assets`); },
  createAsset(projectId, payload){ return api(`/api/workflow/projects/${projectId}/assets`, {method:'POST', body: JSON.stringify(payload)}); },
  structuredStoryboard(projectId, payload){ return api(`/api/workflow/projects/${projectId}/storyboard/structure`, {method:'POST', body: JSON.stringify(payload)}); },
  generationQueue(projectId){ return api(`/api/workflow/projects/${projectId}/generation-queue`); },
  createGenerationBatch(projectId, payload){ return api(`/api/workflow/projects/${projectId}/generation-queue/batch`, {method:'POST', body: JSON.stringify(payload)}); },
  generateImage(payload){ return api('/api/generate/images', {method:'POST', body: JSON.stringify(payload)}); },
  generateVideo(payload){ return api('/api/generate/videos', {method:'POST', body: JSON.stringify(payload)}); },
};
