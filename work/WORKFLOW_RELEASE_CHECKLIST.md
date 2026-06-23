# 政宣智作可信工作流 · 合并前收敛检查清单

本清单用于 PR 合并前的最后收敛，不再新增产品模块，只验证现有工作流能稳定跑通。

## 0. 收敛原则

当前 PR 已经进入功能冻结阶段：

- 不再新增业务模块；
- 不再扩大真实供应商调用、PDF 导出、对象存储等范围；
- 只修启动失败、接口 500、字段缺失、前后端调用不一致、下载失败等阻断问题；
- 当前版本目标为 `可信工作流 v0.7.5`。

## 1. 启动检查

```bash
cd work
python -m py_compile app.py platform_workflow_routes.py source_trust_routes.py generation_queue_routes.py generation_worker_routes.py review_gate_routes.py export_package_routes.py
python app.py
```

期望：

- 服务正常启动；
- `/api/health` 返回 `0.7.5`；
- `/workflow.html` 可以打开；
- 根路径右下角有“进入真实工作流”。

## 2. 登录与项目

默认种子账号：

- 手机号：`18800000000`
- 密码：`123456`

检查路径：

1. 登录；
2. 查看项目列表；
3. 新建测试项目；
4. 进入新项目。

接口级 smoke test：

```bash
TOKEN=$(curl -s -X POST http://localhost:3002/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"18800000000","password":"123456"}' | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s http://localhost:3002/api/auth/me -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:3002/api/projects -H "Authorization: Bearer $TOKEN"
```

## 3. 可信工作流主链路

按顺序验证：

1. 保存 Brief；
2. 上传资料；
3. 编辑资料可信度；
4. 新增视觉资产；
5. 生成结构化分镜；
6. 创建图片 + 视频任务；
7. 运行 worker；
8. 查看候选结果；
9. 锁定候选到镜头；
10. 运行发布自检；
11. 处理 R1 阻断项；
12. 生成交付包；
13. 下载 ZIP / JSON / Markdown。

## 4. 核心接口对照

前端 `/workflow.html` 依赖以下接口。合并前至少确认接口存在且不会 404：

| 模块 | 方法 | 接口 |
|---|---:|---|
| Brief | GET/PUT | `/api/workflow/projects/<project_id>/brief` |
| 资料可信度 | GET | `/api/workflow/projects/<project_id>/sources/trust` |
| 单份资料可信度 | GET/PUT | `/api/workflow/sources/<source_id>/trust` |
| 批量解析资料 | POST | `/api/workflow/projects/<project_id>/sources/parse-all` |
| 视觉资产 | GET/POST | `/api/workflow/projects/<project_id>/assets` |
| 结构化分镜 | POST | `/api/workflow/projects/<project_id>/storyboard/structure` |
| 就绪度 | GET | `/api/workflow/projects/<project_id>/readiness` |
| 生成队列 | GET/POST | `/api/workflow/projects/<project_id>/generation-queue` / `/generation-queue/batch` |
| worker | POST | `/api/workflow/projects/<project_id>/generation-worker/run` |
| 候选结果 | GET | `/api/workflow/projects/<project_id>/candidates` |
| 候选锁定 | POST | `/api/workflow/candidates/<candidate_id>/lock` |
| 发布自检 | GET/POST | `/api/workflow/projects/<project_id>/review/items` / `/review/run` |
| 审核项处理 | POST | `/api/workflow/review-items/<item_id>/resolve` |
| 交付包 | GET/POST | `/api/workflow/projects/<project_id>/exports` |
| 证据 JSON | GET | `/api/workflow/exports/<export_id>/evidence` |
| 文件下载 | GET | `/api/workflow/exports/<export_id>/download.zip` / `download.json` / `manifest.md` |

## 5. 数据库字段重点检查

以下字段是后续链路依赖项，必须存在：

- `project_scenes.locked_candidate_id`
- `project_scenes.locked_image_url`
- `project_scenes.locked_video_url`
- `project_scenes.generation_mode`
- `project_scenes.source_citations_json`
- `project_scenes.asset_refs_json`
- `exports.evidence_package_json`
- `generation_adapter_runs.request_json`
- `generation_adapter_runs.response_json`

可以在 SQLite 里执行：

```sql
PRAGMA table_info(project_scenes);
PRAGMA table_info(project_sources);
PRAGMA table_info(generation_adapter_runs);
PRAGMA table_info(exports);
```

重点确认：

```text
project_scenes: locked_candidate_id, locked_image_url, locked_video_url, generation_mode, source_citations_json, asset_refs_json
project_sources: source_authority_level, can_quote, can_visualize, sensitive_level, citation_required, notes
exports: evidence_package_json, package_url, ai_label_enabled
generation_adapter_runs: request_json, response_json, status, error_message
```

## 6. 下载文件检查

交付包生成后，确认以下 3 个下载按钮可用：

- 下载 ZIP；
- 下载 JSON；
- 下载说明。

ZIP 内必须包含：

- `evidence.json`
- `manifest.md`
- `sources.json`
- `scenes.json`
- `review_items.json`
- `generation_tasks.json`
- `candidates.json`
- `sources.csv`
- `scenes.csv`

## 7. 当前边界

当前 worker 仍是模拟执行器，不真实调用供应商 API，不真实扣费。真实供应商 worker、最终成片文件存储、PDF 交付文件属于下一阶段，不再放入当前 PR。

## 8. 合并判断

合并前只看三件事：

1. Flask 能启动；
2. `/workflow.html` 主链路能跑到交付包下载；
3. PR 无冲突且 mergeable。

如果以上三项通过，本 PR 可以作为“可信工作流 v0.7.5”合并。

## 9. 验收记录模板

```text
验收时间：
验收人：
分支：ai-workflow-platform-upgrade-3
PR：#4

启动检查：通过 / 未通过
登录与项目：通过 / 未通过
Brief：通过 / 未通过
资料上传与可信度：通过 / 未通过
视觉资产：通过 / 未通过
结构化分镜：通过 / 未通过
生成队列：通过 / 未通过
worker 与候选：通过 / 未通过
候选锁定：通过 / 未通过
发布自检：通过 / 未通过
交付包生成：通过 / 未通过
ZIP/JSON/Markdown 下载：通过 / 未通过

阻断问题：
非阻断问题：
是否建议合并：是 / 否
```
