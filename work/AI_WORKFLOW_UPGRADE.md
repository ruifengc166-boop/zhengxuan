# 政宣智作 AI 宣传片可信工作流升级说明

本次升级把项目从“AI 生成原型”往“政企宣传片可信生产线”推进，借鉴 BigBanana-AI-Director 的阶段化生产思路，但不照搬短剧导演台，而是围绕政企宣传片的资料可信、口径可控、分镜可追溯和发布留痕进行改造。

## 1. 新增工作流层

新增文件：`platform_workflow_routes.py`

新增 API：

- `GET /api/workflow/projects/<project_id>/brief`：读取项目 Brief。
- `PUT /api/workflow/projects/<project_id>/brief`：更新宣传目标、受众、口径、禁用表达、必达信息等。
- `GET /api/workflow/projects/<project_id>/assets`：读取项目视觉资产库。
- `POST /api/workflow/projects/<project_id>/assets`：创建 Logo、人物、场景、证书、图表、B-roll 等视觉资产。
- `POST /api/workflow/projects/<project_id>/storyboard/structure`：根据脚本、资料引用和视觉资产生成结构化分镜。
- `GET /api/workflow/projects/<project_id>/readiness`：检查项目是否具备进入生成和导出的条件。

## 2. 自动扩展的数据模型

`platform_workflow_routes.py` 在导入时会执行幂等 schema upgrade，不会清空旧数据。

### projects 增加

- `objective`：宣传目标
- `target_audience`：目标受众
- `tone`：表达语气
- `forbidden_expressions`：禁用表达
- `required_messages`：必须出现的信息
- `approval_owner`：审核负责人
- `brief_json`：扩展 Brief

### project_sources 增加

- `source_authority_level`：资料权威等级
- `source_date`：资料日期
- `source_owner`：资料责任方
- `can_quote`：是否可引用
- `can_visualize`：是否可视化
- `sensitive_level`：敏感级别
- `citation_required`：是否强制引用
- `notes`：备注

### project_scenes 增加

- `scene_goal`：镜头目的
- `source_citations_json`：资料引用
- `shot_size`：景别
- `camera_movement`：运镜
- `visual_subject`：视觉主体
- `location`：场景
- `voiceover_text`：旁白
- `subtitle_text`：字幕
- `risk_notes`：风险提示
- `generation_mode`：生成模式
- `asset_refs_json`：资产引用

### 新增表

- `visual_assets`：视觉资产库
- `brand_kits`：机构品牌包
- `generation_adapter_runs`：供应商适配器请求留痕

## 3. 生成适配器骨架

新增目录：

```text
work/adapters/
work/services/
```

新增能力：

- `OpenAIImageAdapter`
- `KlingVideoAdapter`
- `SeedanceVideoAdapter`
- `GenerationAdapter` 基类
- `build_generation_submission_plan()` 生成供应商请求计划

当前不会直接发起真实付费调用，只会把不同供应商的请求参数标准化并写入 `generation_adapter_runs`，避免原型阶段误扣费。

## 4. 已注册到主应用

`app.py` 已注册：

```python
from platform_workflow_routes import platform_workflow
from services.generation_service import build_generation_submission_plan, list_generation_capabilities

app.register_blueprint(platform_workflow)
```

新增：

- `GET /api/generate/capabilities`
- 生成任务创建时写入 `adapter_plan`
- 生成状态接口返回 `adapter_runs`

## 5. 下一步建议

1. 把前端高保真 HTML 中的分镜、素材、生成队列按钮逐步接入这些新接口。
2. 将上传资料时的来源等级、是否可引用、是否可视化等字段暴露到 UI。
3. 接入后台 worker，按 `generation_adapter_runs` 的请求计划真正提交供应商任务。
4. 把 `review/run` 升级为政宣风控引擎，重点检查数据来源、素材授权、AI 标识、地图国旗国徽、医疗教育等高风险表达。
