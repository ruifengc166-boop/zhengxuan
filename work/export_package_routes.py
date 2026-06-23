import io
import json
import zipfile

from flask import Blueprint, Response, jsonify, request

from auth import login_required
from database import get_db, gen_id, now, log_operation
from platform_workflow_routes import ensure_platform_schema


export_package = Blueprint("export_package", __name__, url_prefix="/api/workflow")


def is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


def get_project_for_user(db, project_id, user):
    project = db.execute(
        "SELECT p.*, o.name AS org_name, o.org_type FROM projects p LEFT JOIN organizations o ON p.org_id=o.id WHERE p.id=?",
        (project_id,),
    ).fetchone()
    if not project:
        return None, (jsonify({"error": "项目不存在"}), 404)
    if is_super_admin(user):
        return project, None
    if project["org_id"] and project["org_id"] == user.get("org_id"):
        return project, None
    if project["user_id"] == user.get("uid"):
        return project, None
    return None, (jsonify({"error": "无权访问该项目"}), 403)


def safe_json(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def log_action(action, detail=""):
    user = getattr(request, "current_user", {}) or {}
    try:
        log_operation(
            user_id=user.get("uid", ""),
            user_name=user.get("name", ""),
            action=action,
            detail=detail,
            ip=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )
    except Exception as exc:
        print(f"[WARN] export package log failed: {exc}", flush=True)


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def collect_evidence(db, project, export_id):
    project_id = project["id"]
    sources = rows_to_dicts(db.execute(
        """
        SELECT s.*, f.original_name, f.file_type, f.file_size, f.mime_type
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        WHERE s.project_id=?
        ORDER BY s.created_at ASC
        """,
        (project_id,),
    ).fetchall())
    scripts = rows_to_dicts(db.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version_no DESC", (project_id,)).fetchall())
    scenes = rows_to_dicts(db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order ASC", (project_id,)).fetchall())
    assets = rows_to_dicts(db.execute("SELECT * FROM visual_assets WHERE project_id=? ORDER BY created_at ASC", (project_id,)).fetchall())
    tasks = rows_to_dicts(db.execute("SELECT * FROM generation_tasks WHERE project_id=? ORDER BY created_at ASC", (project_id,)).fetchall())
    candidates = rows_to_dicts(db.execute("SELECT * FROM generation_candidates WHERE project_id=? ORDER BY created_at ASC", (project_id,)).fetchall())
    review_items = rows_to_dicts(db.execute("SELECT * FROM review_items WHERE project_id=? ORDER BY created_at ASC", (project_id,)).fetchall())

    locked_candidates = [c for c in candidates if c.get("locked")]
    open_r1 = [r for r in review_items if r.get("status") == "open" and r.get("severity") == "R1"]
    open_r2 = [r for r in review_items if r.get("status") == "open" and r.get("severity") == "R2"]

    scene_cards = []
    for scene in scenes:
        scene_candidates = [c for c in candidates if c.get("scene_id") == scene.get("id")]
        locked_for_scene = [c for c in scene_candidates if c.get("locked")]
        scene_cards.append({
            "scene_id": scene.get("id"),
            "scene_order": scene.get("scene_order"),
            "name": scene.get("name"),
            "scene_goal": scene.get("scene_goal", ""),
            "source_citations": safe_json(scene.get("source_citations_json", "[]"), []),
            "generation_mode": scene.get("generation_mode", ""),
            "locked_image_url": scene.get("locked_image_url", ""),
            "locked_video_url": scene.get("locked_video_url", ""),
            "locked_candidates": locked_for_scene,
        })

    package = {
        "export_id": export_id,
        "created_at": now(),
        "project": dict(project),
        "brief": {
            "objective": project["objective"] if "objective" in project.keys() else "",
            "target_audience": project["target_audience"] if "target_audience" in project.keys() else "",
            "tone": project["tone"] if "tone" in project.keys() else "",
            "required_messages": project["required_messages"] if "required_messages" in project.keys() else "",
            "forbidden_expressions": project["forbidden_expressions"] if "forbidden_expressions" in project.keys() else "",
        },
        "source_register": sources,
        "script_versions": scripts,
        "visual_assets": assets,
        "scene_cards": scene_cards,
        "generation_tasks": tasks,
        "generation_candidates": candidates,
        "locked_candidates": locked_candidates,
        "review_items": review_items,
        "ai_label": {
            "enabled": True,
            "text": "本项目为 AI 辅助生成内容，已保留资料来源、生成任务、候选确认和人工审核记录。",
        },
        "summary": {
            "source_count": len(sources),
            "scene_count": len(scenes),
            "asset_count": len(assets),
            "task_count": len(tasks),
            "candidate_count": len(candidates),
            "locked_candidate_count": len(locked_candidates),
            "open_r1_count": len(open_r1),
            "open_r2_count": len(open_r2),
            "export_ready": len(open_r1) == 0,
        },
    }
    return package


def export_urls(export_id):
    return {
        "evidence_json": f"/api/workflow/exports/{export_id}/download.json",
        "manifest_md": f"/api/workflow/exports/{export_id}/manifest.md",
        "zip": f"/api/workflow/exports/{export_id}/download.zip",
    }


def enrich_export_record(record):
    item = dict(record)
    item["download_urls"] = export_urls(item["id"])
    return item


def evidence_json_text(evidence):
    return json.dumps(evidence, ensure_ascii=False, indent=2)


def manifest_markdown(record, evidence):
    project = evidence.get("project", {})
    summary = evidence.get("summary", {})
    brief = evidence.get("brief", {})
    ai_label = evidence.get("ai_label", {})
    lines = [
        f"# {record.get('version_label') or '交付包'}",
        "",
        "## 基本信息",
        f"- 项目名称：{project.get('name', '')}",
        f"- 项目 ID：{project.get('id', '')}",
        f"- 交付包 ID：{record.get('id', '')}",
        f"- 状态：{record.get('status', '')}",
        f"- 创建时间：{record.get('created_at', '')}",
        "",
        "## Brief 摘要",
        f"- 宣传目标：{brief.get('objective', '')}",
        f"- 目标受众：{brief.get('target_audience', '')}",
        f"- 语气风格：{brief.get('tone', '')}",
        "",
        "## 证据链摘要",
        f"- 资料数量：{summary.get('source_count', 0)}",
        f"- 镜头数量：{summary.get('scene_count', 0)}",
        f"- 视觉资产数量：{summary.get('asset_count', 0)}",
        f"- 生成任务数量：{summary.get('task_count', 0)}",
        f"- 候选数量：{summary.get('candidate_count', 0)}",
        f"- 锁定候选数量：{summary.get('locked_candidate_count', 0)}",
        f"- R1 阻断项：{summary.get('open_r1_count', 0)}",
        f"- R2 警告项：{summary.get('open_r2_count', 0)}",
        f"- 是否可交付：{'是' if summary.get('export_ready') else '否'}",
        "",
        "## AI 标识",
        ai_label.get("text", ""),
        "",
        "## 文件清单",
        "- evidence.json：完整证据 JSON",
        "- manifest.md：本说明文件",
        "- sources.json：资料清单",
        "- scenes.json：镜头和引用清单",
        "- review_items.json：审核项清单",
        "- generation_tasks.json：生成任务清单",
        "- candidates.json：候选结果清单",
    ]
    return "\n".join(lines)


def csv_escape(value):
    text = str(value or "")
    return '"' + text.replace('"', '""').replace("\n", " ") + '"'


def source_csv(evidence):
    rows = ["id,title,source_type,data_level,authority,sensitive,can_quote,can_visualize,parse_status"]
    for s in evidence.get("source_register", []):
        rows.append(",".join([
            csv_escape(s.get("id")), csv_escape(s.get("title") or s.get("original_name")), csv_escape(s.get("source_type")),
            csv_escape(s.get("data_level")), csv_escape(s.get("source_authority_level")), csv_escape(s.get("sensitive_level")),
            csv_escape(s.get("can_quote")), csv_escape(s.get("can_visualize")), csv_escape(s.get("parse_status")),
        ]))
    return "\n".join(rows)


def scene_csv(evidence):
    rows = ["scene_order,name,scene_goal,generation_mode,source_citations,locked_image_url,locked_video_url"]
    for s in evidence.get("scene_cards", []):
        rows.append(",".join([
            csv_escape(s.get("scene_order")), csv_escape(s.get("name")), csv_escape(s.get("scene_goal")),
            csv_escape(s.get("generation_mode")), csv_escape(";".join(s.get("source_citations") or [])),
            csv_escape(s.get("locked_image_url")), csv_escape(s.get("locked_video_url")),
        ]))
    return "\n".join(rows)


def fetch_export_record(export_id):
    db = get_db()
    record = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if not record:
        db.close()
        return None, None, (jsonify({"error": "交付包不存在"}), 404)
    project, error = get_project_for_user(db, record["project_id"], request.current_user)
    if error:
        db.close()
        return None, None, error
    evidence = safe_json(record["evidence_package_json"], {})
    db.close()
    return dict(record), evidence, None


@export_package.route("/projects/<project_id>/exports", methods=["GET"])
@login_required
def list_exports(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    rows = rows_to_dicts(db.execute("SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())
    db.close()
    return jsonify({"project_id": project_id, "exports": [enrich_export_record(r) for r in rows]})


@export_package.route("/projects/<project_id>/exports", methods=["POST"])
@login_required
def create_export(project_id):
    ensure_platform_schema()
    data = request.get_json() or {}
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    export_id = gen_id("exp")
    evidence = collect_evidence(db, project, export_id)
    version_label = data.get("version_label") or f"交付包 {now()}"
    package_url = f"/api/workflow/exports/{export_id}/download.zip"
    status = "locked" if evidence["summary"]["export_ready"] else "needs_review"
    db.execute(
        """
        INSERT INTO exports (id,project_id,org_id,version_label,status,video_url,package_url,ai_label_enabled,evidence_package_json,confirmed_by,confirmed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            export_id,
            project_id,
            project["org_id"],
            version_label,
            status,
            data.get("video_url", ""),
            package_url,
            1,
            json.dumps(evidence, ensure_ascii=False),
            request.current_user.get("uid", ""),
            now(),
        ),
    )
    db.execute("UPDATE projects SET status=?,progress=MAX(progress, ?),updated_at=? WHERE id=?", ("已锁版" if status == "locked" else "待复核", 98 if status == "locked" else 90, now(), project_id))
    db.commit()
    record = enrich_export_record(db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone())
    db.close()
    log_action("create_evidence_export", f"项目 {project_id} 创建证据包 {export_id} status={status}")
    return jsonify({"success": True, "export": record, "evidence": evidence})


@export_package.route("/exports/<export_id>/evidence", methods=["GET"])
@login_required
def get_export_evidence(export_id):
    ensure_platform_schema()
    record, evidence, error = fetch_export_record(export_id)
    if error:
        return error
    return jsonify({"export": enrich_export_record(record), "evidence": evidence})


@export_package.route("/exports/<export_id>/download.json", methods=["GET"])
@login_required
def download_evidence_json(export_id):
    ensure_platform_schema()
    record, evidence, error = fetch_export_record(export_id)
    if error:
        return error
    body = evidence_json_text(evidence)
    filename = f"{record['id']}-evidence.json"
    return Response(body, mimetype="application/json; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={filename}"})


@export_package.route("/exports/<export_id>/manifest.md", methods=["GET"])
@login_required
def download_manifest(export_id):
    ensure_platform_schema()
    record, evidence, error = fetch_export_record(export_id)
    if error:
        return error
    body = manifest_markdown(record, evidence)
    filename = f"{record['id']}-manifest.md"
    return Response(body, mimetype="text/markdown; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={filename}"})


@export_package.route("/exports/<export_id>/download.zip", methods=["GET"])
@login_required
def download_export_zip(export_id):
    ensure_platform_schema()
    record, evidence, error = fetch_export_record(export_id)
    if error:
        return error
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("evidence.json", evidence_json_text(evidence))
        zf.writestr("manifest.md", manifest_markdown(record, evidence))
        zf.writestr("sources.json", json.dumps(evidence.get("source_register", []), ensure_ascii=False, indent=2))
        zf.writestr("scenes.json", json.dumps(evidence.get("scene_cards", []), ensure_ascii=False, indent=2))
        zf.writestr("review_items.json", json.dumps(evidence.get("review_items", []), ensure_ascii=False, indent=2))
        zf.writestr("generation_tasks.json", json.dumps(evidence.get("generation_tasks", []), ensure_ascii=False, indent=2))
        zf.writestr("candidates.json", json.dumps(evidence.get("generation_candidates", []), ensure_ascii=False, indent=2))
        zf.writestr("sources.csv", source_csv(evidence))
        zf.writestr("scenes.csv", scene_csv(evidence))
    buffer.seek(0)
    filename = f"{record['id']}-evidence-package.zip"
    log_action("download_evidence_zip", f"下载交付包 {export_id}")
    return Response(buffer.getvalue(), mimetype="application/zip", headers={"Content-Disposition": f"attachment; filename={filename}"})
