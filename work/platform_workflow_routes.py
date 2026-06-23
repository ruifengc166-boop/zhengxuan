import json
import re
from flask import Blueprint, jsonify, request

from auth import login_required, is_admin_role
from database import get_db, gen_id, now, log_operation

platform_workflow = Blueprint("platform_workflow", __name__, url_prefix="/api/workflow")


def safe_json(value, fallback):
    try:
        if value in (None, ""):
            return fallback
        return json.loads(value)
    except Exception:
        return fallback


def dumps(data):
    return json.dumps(data, ensure_ascii=False)


def is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


def is_admin(user):
    return is_admin_role((user or {}).get("role"))


def ensure_platform_schema():
    """Idempotent schema upgrade for the trusted promo-film workflow layer.

    This is intentionally local to this module so the new workflow can be merged
    without forcing a destructive database rewrite. The existing SQLite prototype
    keeps working, while new fields become available as soon as the module is
    imported.
    """
    db = get_db()

    def columns(table_name):
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}

    def add_column(table_name, column_name, definition):
        if column_name not in columns(table_name):
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    for table_name, column_name, definition in [
        ("projects", "objective", "TEXT DEFAULT ''"),
        ("projects", "target_audience", "TEXT DEFAULT ''"),
        ("projects", "tone", "TEXT DEFAULT '克制、可信、清晰'"),
        ("projects", "forbidden_expressions", "TEXT DEFAULT ''"),
        ("projects", "required_messages", "TEXT DEFAULT ''"),
        ("projects", "approval_owner", "TEXT DEFAULT ''"),
        ("projects", "brief_json", "TEXT DEFAULT '{}'"),
        ("project_sources", "source_authority_level", "TEXT DEFAULT 'internal'"),
        ("project_sources", "source_date", "TEXT DEFAULT ''"),
        ("project_sources", "source_owner", "TEXT DEFAULT ''"),
        ("project_sources", "can_quote", "INTEGER DEFAULT 1"),
        ("project_sources", "can_visualize", "INTEGER DEFAULT 1"),
        ("project_sources", "sensitive_level", "TEXT DEFAULT 'normal'"),
        ("project_sources", "citation_required", "INTEGER DEFAULT 1"),
        ("project_sources", "notes", "TEXT DEFAULT ''"),
        ("project_scenes", "scene_goal", "TEXT DEFAULT ''"),
        ("project_scenes", "source_citations_json", "TEXT DEFAULT '[]'"),
        ("project_scenes", "shot_size", "TEXT DEFAULT ''"),
        ("project_scenes", "camera_movement", "TEXT DEFAULT ''"),
        ("project_scenes", "visual_subject", "TEXT DEFAULT ''"),
        ("project_scenes", "location", "TEXT DEFAULT ''"),
        ("project_scenes", "voiceover_text", "TEXT DEFAULT ''"),
        ("project_scenes", "subtitle_text", "TEXT DEFAULT ''"),
        ("project_scenes", "risk_notes", "TEXT DEFAULT ''"),
        ("project_scenes", "start_frame_url", "TEXT DEFAULT ''"),
        ("project_scenes", "end_frame_url", "TEXT DEFAULT ''"),
        ("project_scenes", "locked_candidate_id", "TEXT DEFAULT ''"),
        ("project_scenes", "locked_image_url", "TEXT DEFAULT ''"),
        ("project_scenes", "locked_video_url", "TEXT DEFAULT ''"),
        ("project_scenes", "generation_mode", "TEXT DEFAULT 'image_to_video'"),
        ("project_scenes", "asset_refs_json", "TEXT DEFAULT '[]'"),
        ("templates", "use_case", "TEXT DEFAULT ''"),
        ("templates", "required_sources_json", "TEXT DEFAULT '[]'"),
        ("templates", "script_structure_json", "TEXT DEFAULT '[]'"),
        ("templates", "shot_structure_json", "TEXT DEFAULT '[]'"),
        ("templates", "tone_rules_json", "TEXT DEFAULT '{}'"),
        ("templates", "risk_rules_json", "TEXT DEFAULT '{}'"),
        ("templates", "default_visual_style", "TEXT DEFAULT ''"),
        ("templates", "default_music_style", "TEXT DEFAULT ''"),
        ("templates", "default_voice_style", "TEXT DEFAULT ''"),
        ("templates", "export_checklist_json", "TEXT DEFAULT '[]'"),
    ]:
        add_column(table_name, column_name, definition)

    db.executescript("""
    CREATE TABLE IF NOT EXISTS visual_assets (
        id TEXT PRIMARY KEY,
        org_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        asset_type TEXT DEFAULT 'reference',
        title TEXT DEFAULT '',
        reference_file_id TEXT DEFAULT '',
        reference_url TEXT DEFAULT '',
        visual_description TEXT DEFAULT '',
        auth_status TEXT DEFAULT 'unchecked',
        source_owner TEXT DEFAULT '',
        usage_restriction TEXT DEFAULT '',
        risk_notes TEXT DEFAULT '',
        tags_json TEXT DEFAULT '[]',
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS brand_kits (
        id TEXT PRIMARY KEY,
        org_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        name TEXT DEFAULT '默认品牌包',
        logo_asset_id TEXT DEFAULT '',
        primary_color TEXT DEFAULT '',
        secondary_color TEXT DEFAULT '',
        font_hint TEXT DEFAULT '',
        subtitle_style TEXT DEFAULT '',
        end_card_rule TEXT DEFAULT '',
        ai_label_rule TEXT DEFAULT '发布前保留 AI 生成内容标识',
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS generation_adapter_runs (
        id TEXT PRIMARY KEY,
        task_id TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        model_name TEXT DEFAULT '',
        adapter_name TEXT DEFAULT '',
        request_json TEXT DEFAULT '{}',
        response_json TEXT DEFAULT '{}',
        status TEXT DEFAULT 'planned',
        error_message TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    """)
    db.commit()
    db.close()


ensure_platform_schema()


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
        print(f"[WARN] platform workflow log failed: {exc}", flush=True)


def get_project_for_user(db, project_id, user):
    project = db.execute(
        "SELECT p.*, o.name as org_name, o.org_type FROM projects p LEFT JOIN organizations o ON p.org_id=o.id WHERE p.id=?",
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


def clean_text(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def latest_script(db, project_id):
    return db.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version_no DESC LIMIT 1", (project_id,)).fetchone()


def project_sources(db, project_id):
    return db.execute("SELECT * FROM project_sources WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()


def project_assets(db, project_id):
    return db.execute("SELECT * FROM visual_assets WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()


def serialize_project(project):
    result = dict(project)
    result["brief"] = safe_json(project["brief_json"] if "brief_json" in project.keys() else "{}", {})
    return result


@platform_workflow.route("/projects/<project_id>/brief", methods=["GET"])
@login_required
def get_project_brief(project_id):
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    db.close()
    return jsonify({"project": serialize_project(project)})


@platform_workflow.route("/projects/<project_id>/brief", methods=["PUT"])
@login_required
def update_project_brief(project_id):
    data = request.get_json() or {}
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    allowed = ["objective", "target_audience", "tone", "forbidden_expressions", "required_messages", "approval_owner"]
    updates = {key: clean_text(data.get(key, project[key] if key in project.keys() else "")) for key in allowed}
    brief = safe_json(project["brief_json"] if "brief_json" in project.keys() else "{}", {})
    brief.update({key: data.get(key, updates[key]) for key in allowed})
    db.execute(
        """
        UPDATE projects
        SET objective=?, target_audience=?, tone=?, forbidden_expressions=?, required_messages=?, approval_owner=?, brief_json=?, updated_at=?
        WHERE id=?
        """,
        (updates["objective"], updates["target_audience"], updates["tone"], updates["forbidden_expressions"], updates["required_messages"], updates["approval_owner"], dumps(brief), now(), project_id),
    )
    db.commit()
    updated = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    db.close()
    log_action("update_project_brief", f"项目 {project_id} 更新 Brief")
    return jsonify({"success": True, "project": serialize_project(updated)})


@platform_workflow.route("/projects/<project_id>/assets", methods=["GET"])
@login_required
