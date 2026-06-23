# 真实工作流前端

这个目录把原来高保真原型里的核心业务区，拆成可以直接调用后端接口的真实前端模块。

入口：`/workflow.html`

## 已接入能力

- 登录、读取项目、创建测试项目
- 项目 Brief：读取、编辑、保存
- 资料可信度：读取、编辑、保存、单份解析、批量解析
- 视觉资产：读取、新增
- 就绪度：资料、脚本、资产、结构化镜头和阻断项
- 结构化分镜：调用 `/api/workflow/projects/<project_id>/storyboard/structure`
- 生成队列：按镜头创建图片/视频任务，展示任务状态和 adapter plan
- worker 执行器：把 queued/simulated 任务推进为候选结果
- 候选锁定：把图片/视频候选锁定回具体镜头
- 发布自检：检查 Brief、可信资料、结构化分镜、候选锁定、资产授权和 AI 标识建议
- 证据包/交付包：把 Brief、资料、脚本、镜头、任务、候选、审核项和 AI 标识汇总成可复核记录

## 模块说明

- `api.js`：统一封装 API、Token、上传、生成队列、worker 和候选锁定逻辑
- `app.js`：真实工作流页面状态、渲染和事件绑定
- `review.js`：发布前自检、审核项展示和人工处理
- `export.js`：证据包生成、交付包记录、证据 JSON 查看
- `styles.css`：独立工作流样式，避免继续污染原来的单文件高保真原型

## 当前生成队列边界

生成队列会把结构化分镜中的图片/视频 Prompt 写入 `generation_tasks`，并同步写入 `generation_adapter_runs`。如果环境或组织 API Key 已配置，任务状态为 `queued`；否则任务状态为 `simulated`，用于验证流程，不会真实扣费或调用供应商。

当前 worker 是可手动触发的模拟执行器：

1. 读取 queued/simulated 任务；
2. 回写 `generation_candidates`；
3. 更新 `generation_tasks` 为 completed；
4. 更新 `generation_adapter_runs` 为 succeeded；
5. 写入 `usage_records`；
6. 前端可锁定候选到镜头。

## 发布自检边界

发布自检会生成 `review_items`，并将风险分为：

- `R1`：交付阻断项，必须处理；
- `R2`：警告项，建议处理或人工确认；
- `R3`：提示项，作为交付建议保留。

当前已检查：项目 Brief、资料可引用性、资料敏感等级、脚本版本、结构化镜头、图片/视频候选锁定、视觉资产授权、镜头引用、AI 标识建议。

## 证据包边界

证据包会写入 `exports.evidence_package_json`，并记录：

- 项目 Brief；
- 可信资料清单；
- 脚本版本；
- 视觉资产；
- 镜头卡和资料引用；
- 生成任务；
- 候选结果和锁定候选；
- 审核项；
- AI 辅助生成标识。

如果仍有未处理 R1，交付包状态为 `needs_review`；如果没有 R1，交付包状态为 `locked`。

下一步需要把证据 JSON 转成真实文件，例如 JSON/ZIP/PDF，并接入最终成片文件存储。

## 设计取舍

原来的 `index.html` 保留为产品高保真展示，不再继续把真实业务逻辑堆在单文件里。真实生产流程从 `/workflow.html` 进入，后续可以逐步把 dashboard、项目列表、生成队列、候选锁定、发布自检和证据包导出迁移到这个模块体系。
