# 政宣智作 P0/P1 安全与工作流底座修改执行说明

本分支用于把当前高保真原型补成“可安全部署、可继续接入真实 AI 视频工作流”的后端底座，并补齐资料、脚本、分镜、自检、证据包的最小业务闭环。

## 分支

`p0-security-workflow-foundation`

## 本次修改范围

### P0：上线安全底线

1. 后台 API 默认管理员权限保护。
2. 项目、账单、用户、文件按组织隔离。
3. 上传目录自动创建，上传文件下载增加鉴权。
4. 密码从裸 SHA-256 升级为 PBKDF2-SHA256，并兼容旧账号首次登录后自动升级。
5. 新增 AI 视频工作流基础表：
   - `project_sources`
   - `source_extractions`
   - `script_versions`
   - `shot_prompts`
   - `generation_tasks`
   - `generation_candidates`
   - `usage_records`
   - `review_items`
   - `exports`
6. 图片 / 视频生成接口从随机模拟改为“任务落库模式”，后续可接入供应商回调或轮询。
7. 初始化脚本不再默认公开弱密码；未设置 `ADMIN_PASSWORD` 时自动生成强密码。
8. `.env.example` 增加生产部署必要配置。

### P1：宣传片工作流最小闭环

1. 新增 `work/workflow_routes.py`，提供 `/api/workflow/*` 工作流接口。
2. 支持项目资料列表、单份解析、批量解析。
3. 支持 TXT / DOCX / PPTX / XLSX 的轻量文本抽取。
4. PDF、图片、音视频资料进入待解析状态，预留 OCR / 转写接口。
5. 支持基于资料事实生成脚本初稿，并保留脚本版本。
6. 支持锁定脚本版本。
7. 支持从脚本生成分镜 Prompt，包括 image prompt 和 video prompt。
8. 支持锁定单条 Prompt。
9. 支持发布前自检：来源、脚本引用、分镜、素材授权、生成任务。
10. 支持关闭自检项。
11. 支持生成发布证据包 ZIP，包含资料、脚本、分镜、Prompt、生成任务、自检项、上传素材清单。
12. 新增 `work/public/workflow-console.html`，用于部署后快速测试“资料 → 脚本 → 分镜 → 自检 → 证据包”闭环。

## 腾讯云部署执行步骤

### 1. 拉取代码

```bash
git fetch origin
git checkout p0-security-workflow-foundation
```

### 2. 安装依赖

```bash
cd work
pip install -r requirements.txt
```

或在仓库根目录执行：

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

至少设置：

```bash
APP_ENV=production
JWT_SECRET=<足够随机的长密钥>
PORT=3000
CORS_ORIGINS=<你的正式域名>
```

生成 JWT_SECRET 示例：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 4. 首次初始化

在仓库根目录执行：

```bash
python setup.py
```

如果没有设置 `ADMIN_PASSWORD`，脚本会自动生成强密码，请复制保存。

### 5. 启动

如果运行目录是 `work`：

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

如果运行目录是仓库根目录：

```bash
cd work && gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

## 接口变化

### 项目列表

`GET /api/projects`

普通用户只能看到自己组织 / 自己创建的项目；管理员按权限看到对应范围。

### 项目详情

`GET /api/projects/<project_id>`

返回项目、镜头、资料、脚本版本。

### 上传文件

`POST /api/upload`

支持表单字段：

- `file`
- `project_id`
- `data_level`

上传后自动写入 `uploaded_files`，如果带 `project_id`，同时写入 `project_sources`。

### 文件访问

`GET /uploads/<filename>`

现在必须带 Bearer Token，且只允许：

- 超级管理员；
- 上传者本人；
- 同组织用户；
- 有项目权限的用户。

### 图片生成

`POST /api/generate/images`

现在会创建 `generation_tasks` 记录，并返回 `taskId`。未配置真实 API Key 时，状态为 `simulated`。

### 视频生成

`POST /api/generate/videos`

同图片生成，任务会落库。后续可在此基础上接入可灵、Seedance、万相、海螺等供应商。

### 生成状态

`GET /api/generate/status/<task_id>`

从数据库读取任务状态，不再随机返回状态。

## 新增工作流接口

### 资料

```http
GET /api/workflow/projects/<project_id>/sources
POST /api/workflow/sources/<source_id>/parse
POST /api/workflow/projects/<project_id>/sources/parse-all
```

说明：上传资料后先进入 `project_sources`，解析结果进入 `source_extractions`。目前轻量支持 TXT、DOCX、PPTX、XLSX；PDF / 图片 / 音视频会返回待 OCR 或待转写状态。

### 脚本

```http
GET /api/workflow/projects/<project_id>/scripts
POST /api/workflow/projects/<project_id>/scripts/generate
POST /api/workflow/scripts/<script_id>/lock
```

说明：生成脚本会基于已解析资料抽取的 facts，自动生成带 `[S1]` 等引用标记的脚本初稿。

### 分镜 Prompt

```http
POST /api/workflow/projects/<project_id>/storyboard/generate
GET /api/workflow/scenes/<scene_id>/prompts
POST /api/workflow/prompts/<prompt_id>/lock
```

说明：分镜生成会把脚本拆成若干 beat，为每个镜头生成 image prompt 和 video prompt，并写入 `shot_prompts`。

### 发布自检

```http
POST /api/workflow/projects/<project_id>/review/run
POST /api/workflow/review-items/<item_id>/resolve
```

说明：自检会检查来源资料、脚本引用、Prompt 完整性、素材授权和生成任务状态。它不能替代人工审批，但可以作为发布前留痕。

### 证据包导出

```http
POST /api/workflow/projects/<project_id>/exports/evidence-package
GET /api/workflow/exports/<export_id>/download
```

说明：导出 ZIP 会写入 `work/exports/`，同时在 `exports` 表里留痕。

## 测试页面

部署后可访问：

```text
/workflow-console.html
```

测试流程：

1. 登录。
2. 新建项目或选择项目。
3. 上传资料。
4. 批量解析资料。
5. 生成脚本初稿。
6. 生成分镜 Prompt。
7. 运行发布自检。
8. 生成并下载证据包 ZIP。

## 后续建议

下一阶段继续补：

1. 真实供应商任务提交与回调：可灵、Seedance、万相、海螺等。
2. 腾讯云 COS：上传文件、候选图、候选视频、导出包都迁移到对象存储。
3. Redis / Celery / RQ：生成任务异步队列化。
4. PDF OCR、图片 OCR、音视频转写。
5. 前台正式 UI 与 `/api/workflow/*` 深度绑定。
6. 正式视频合成、字幕、封面、横竖屏版本导出。
7. 单位 API Key 加密存储与模型路由策略。
