import json

from flask import Blueprint, jsonify, request

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
    return jsonify({"project_id": project_id, "exports": rows})


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
    package_url = f"evidence://exports/{export_id}.json"
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
    record = dict(db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone())
    db.close()
    log_action("create_evidence_export", f"项目 {project_id} 创建证据包 {export_id} status={status}")
    return jsonify({"success": True, "export": record, "evidence": evidence})


@export_package.route("/exports/<export_id>/evidence", methods=["GET"])
@login_required
def get_export_evidence(export_id):
    ensure_platform_schema()
    db = get_db()
    record = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if not record:
        db.close()
        return jsonify({"error": "交付包不存在"}), 404
    project, error = get_project_for_user(db, record["project_id"], request.current_user)
    if error:
        db.close()
        return error
    evidence = safe_json(record["evidence_package_json"], {})
    db.close()
    return jsonify({"export": dict(record), "evidence": evidence})
