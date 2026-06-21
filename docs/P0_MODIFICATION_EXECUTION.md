# 政宣智作 P0/P1/P2 安全与工作流底座修改执行说明

本分支用于把当前高保真原型补成“可安全部署、可继续接入真实 AI 视频工作流”的后端底座，并补齐资料、脚本、分镜、自检、证据包的最小业务闭环。本次继续新增了“真实模型 API 后台配置模块”，方便在后台填写可灵、Seedance、万相、海螺、OpenAI 或自定义接口。

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

### P2：模型 API 后台配置模块

1. 新增 `work/model_config.py`：模型供应商、模型 API 配置、任务解析逻辑。
2. 新增 `work/model_config_routes.py`：后台 API 配置接口。
3. 新增 `work/secret_store.py`：API Key 加密保存与掩码展示。
4. 新增 `work/public/model-config.html`：后台填写模型 API 的独立页面。
5. `app.py` 已注册 `/api/model-config/*`，并让图片 / 视频生成任务优先读取后台启用配置。
6. 后台配置支持三种作用范围：
   - `platform`：平台级，只有超级管理员可配置；
   - `org`：组织级，适合单位自有 API；
   - `user`：个人级，适合测试。
7. 配置优先级：个人级 > 组织级 > 平台级 > 环境变量 > 模拟模式。
8. 保存的 API Key 不会明文返回前端，只返回 `api_key_masked`。

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
API_KEY_ENCRYPTION_SECRET=<另一个足够随机的长密钥>
PORT=3000
CORS_ORIGINS=<你的正式域名>
```

生成密钥示例：

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

## 模型 API 后台配置

部署后访问：

```text
/model-config.html
```

使用流程：

1. 用管理员账号登录。
2. 选择作用范围：平台级 / 本组织 / 当前用户。
3. 选择任务类型：文本、图像、视频。
4. 选择供应商：可灵、Seedance、万相、海螺、OpenAI、自定义。
5. 填写模型名、Base URL、API Key、额外参数。
6. 保存并启用。
7. 调用 `/api/generate/images` 或 `/api/generate/videos` 时，系统会优先读取启用的后台配置。

注意：当前“测试连接”只做安全连通性检查，不会提交真实生成任务，避免误扣费。真正的视频生成提交逻辑应继续在供应商 Adapter 中实现。

## 模型配置接口

```http
GET /api/model-config/providers
GET /api/model-config/configs
POST /api/model-config/configs
PUT /api/model-config/configs/<config_id>
POST /api/model-config/configs/<config_id>/status
POST /api/model-config/configs/<config_id>/test
DELETE /api/model-config/configs/<config_id>
```

## 生成接口的模型读取优先级

`POST /api/generate/images` 和 `POST /api/generate/videos` 会按以下顺序找 API 配置：

1. 当前用户个人级配置；
2. 当前项目所属组织配置；
3. 平台级配置；
4. 环境变量配置；
5. 没有 API Key 时进入模拟任务落库模式。

## 工作流接口

### 资料

```http
GET /api/workflow/projects/<project_id>/sources
POST /api/workflow/sources/<source_id>/parse
POST /api/workflow/projects/<project_id>/sources/parse-all
```

### 脚本

```http
GET /api/workflow/projects/<project_id>/scripts
POST /api/workflow/projects/<project_id>/scripts/generate
POST /api/workflow/scripts/<script_id>/lock
```

### 分镜 Prompt

```http
POST /api/workflow/projects/<project_id>/storyboard/generate
GET /api/workflow/scenes/<scene_id>/prompts
POST /api/workflow/prompts/<prompt_id>/lock
```

### 发布自检

```http
POST /api/workflow/projects/<project_id>/review/run
POST /api/workflow/review-items/<item_id>/resolve
```

### 证据包导出

```http
POST /api/workflow/projects/<project_id>/exports/evidence-package
GET /api/workflow/exports/<export_id>/download
```

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
2. 为每个供应商实现 Adapter：submit、poll、callback、normalize_result、estimate_cost。
3. 腾讯云 COS：上传文件、候选图、候选视频、导出包都迁移到对象存储。
4. Redis / Celery / RQ：生成任务异步队列化。
5. PDF OCR、图片 OCR、音视频转写。
6. 前台正式 UI 与 `/api/workflow/*` 和 `/api/model-config/*` 深度绑定。
7. 正式视频合成、字幕、封面、横竖屏版本导出。
