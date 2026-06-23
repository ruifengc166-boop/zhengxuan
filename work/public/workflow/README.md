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

## 模块说明

- `api.js`：统一封装 API、Token、上传、生成队列逻辑
- `app.js`：真实工作流页面状态、渲染和事件绑定
- `styles.css`：独立工作流样式，避免继续污染原来的单文件高保真原型

## 当前生成队列边界

生成队列会把结构化分镜中的图片/视频 Prompt 写入 `generation_tasks`，并同步写入 `generation_adapter_runs`。如果环境或组织 API Key 已配置，任务状态为 `queued`；否则任务状态为 `simulated`，用于验证流程，不会真实扣费或调用供应商。

下一步需要接后台 worker：从 `generation_adapter_runs` 取出 planned 请求，真实提交供应商 API，轮询状态，回写候选结果。

## 设计取舍

原来的 `index.html` 保留为产品高保真展示，不再继续把真实业务逻辑堆在单文件里。真实生产流程从 `/workflow.html` 进入，后续可以逐步把 dashboard、项目列表、生成队列和发布自检迁移到这个模块体系。
