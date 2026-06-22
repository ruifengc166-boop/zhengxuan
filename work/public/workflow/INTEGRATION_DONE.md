# 前端真实接入完成

本次前端接入不是继续维护旧的单文件演示原型，而是新增 `/workflow.html` 真实工作流入口，并拆成模块化文件。

## 覆盖范围

- Brief：读取、编辑、保存
- 资料可信度：读取、编辑、保存、上传、解析
- 视觉资产：读取、新增、授权状态管理
- 就绪度：读取阻断项和关键数量
- 结构化分镜：调用真实接口生成并展示镜头结构和 Prompt

## 后端补充

新增 `source_trust_routes.py`，用于资料可信度读写：

- `GET /api/workflow/projects/<project_id>/sources/trust`
- `GET /api/workflow/sources/<source_id>/trust`
- `PUT /api/workflow/sources/<source_id>/trust`
- `GET /api/workflow/projects/<project_id>/sources/trust-summary`

`app.py` 已注册 `source_trust` blueprint。
