# 政宣智作可信工作流 · 合并前收敛检查清单

本清单用于 PR 合并前的最后收敛，不再新增产品模块，只验证现有工作流能稳定跑通。

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

## 4. 数据库字段重点检查

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

## 5. 当前边界

当前 worker 仍是模拟执行器，不真实调用供应商 API，不真实扣费。真实供应商 worker、最终成片文件存储、PDF 交付文件属于下一阶段，不再放入当前 PR。

## 6. 合并判断

合并前只看三件事：

1. Flask 能启动；
2. `/workflow.html` 主链路能跑到交付包下载；
3. PR 无冲突且 mergeable。

如果以上三项通过，本 PR 可以作为“可信工作流 v0.7.5”合并。
