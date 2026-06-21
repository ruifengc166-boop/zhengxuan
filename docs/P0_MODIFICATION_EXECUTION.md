# 政宣智作 P0 安全与工作流底座修改执行说明

本分支用于把当前高保真原型补成“可安全部署、可继续接入真实 AI 视频工作流”的后端底座。

## 分支

`p0-security-workflow-foundation`

## 本次修改范围

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

- 超级管理员 / 管理员；
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

## 后续建议

下一阶段应继续补：

1. 真实供应商任务提交与回调。
2. 项目资料解析：PDF / Word / 网页 / 图片 OCR。
3. 脚本生成与引用来源绑定。
4. 分镜 Prompt 版本保存。
5. 候选图 / 候选视频文件落 COS。
6. 自检报告和发布证据包 ZIP 真实生成。
