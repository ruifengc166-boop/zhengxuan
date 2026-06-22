from flask import Blueprint, jsonify, request

from auth import login_required, is_admin_role
from database import get_db, now, log_operation
from platform_workflow_routes import ensure_platform_schema

source_trust = Blueprint("source_trust", __name__, url_prefix="/api/workflow")


def is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


def is_admin(user):
    return is_admin_role((user or {}).get("role"))


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


def get_source_for_user(db, source_id, user):
    source = db.execute(
        """
        SELECT s.*, f.original_name, f.file_type, f.file_size, f.mime_type
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        WHERE s.id=?
        """,
        (source_id,),
    ).fetchone()
    if not source:
        return None, None, (jsonify({"error": "资料不存在"}), 404)
    project, error = get_project_for_user(db, source["project_id"], user)
    if error:
        return None, None, error
    return source, project, None


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
        print(f"[WARN] source trust log failed: {exc}", flush=True)


def to_int_bool(value, default=1):
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    return 1 if str(value).lower() in {"1", "true", "yes", "y", "on", "可", "是"} else 0


def serialize_source(row):
    item = dict(row)
    for key in ["can_quote", "can_visualize", "citation_required"]:
        item[key] = bool(item.get(key, 0))
    return item


@source_trust.route("/projects/<project_id>/sources/trust", methods=["GET"])
@login_required
def list_source_trust(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    rows = db.execute(
        """
        SELECT
            s.*,
            f.original_name,
            f.file_type,
            f.file_size,
            f.mime_type,
            e.extracted_text,
            e.facts_json,
            e.risk_json,
            e.created_at AS last_extracted_at
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        LEFT JOIN source_extractions e ON e.id=(
            SELECT id FROM source_extractions
            WHERE source_id=s.id
            ORDER BY created_at DESC
            LIMIT 1
        )
        WHERE s.project_id=?
        ORDER BY s.created_at DESC
        """,
        (project_id,),
    ).fetchall()
    db.close()
    return jsonify({"sources": [serialize_source(r) for r in rows], "project_id": project_id})


@source_trust.route("/sources/<source_id>/trust", methods=["GET", "PUT"])
@login_required
def source_trust_detail(source_id):
    ensure_platform_schema()
    db = get_db()
    source, project, error = get_source_for_user(db, source_id, request.current_user)
    if error:
        db.close()
        return error

    if request.method == "GET":
        db.close()
        return jsonify({"source": serialize_source(source)})

    data = request.get_json() or {}
    allowed = {
        "title",
        "source_authority_level",
        "source_date",
        "source_owner",
        "sensitive_level",
        "notes",
        "source_type",
        "data_level",
    }
    fields = []
    params = []
    for key in allowed:
        if key in data:
            fields.append(f"{key}=?")
            params.append(data.get(key, ""))
    for key in ["can_quote", "can_visualize", "citation_required"]:
        if key in data:
            fields.append(f"{key}=?")
            params.append(to_int_bool(data.get(key)))
    if not fields:
        db.close()
        return jsonify({"error": "无更新字段"}), 400

    fields.append("updated_at=?")
    params.append(now())
    params.append(source_id)
    db.execute(f"UPDATE project_sources SET {','.join(fields)} WHERE id=?", params)
    db.commit()
    updated = db.execute(
        """
        SELECT s.*, f.original_name, f.file_type, f.file_size, f.mime_type
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        WHERE s.id=?
        """,
        (source_id,),
    ).fetchone()
    db.close()
    log_action("update_source_trust", f"更新资料可信度 {source_id} project={project['id']}")
    return jsonify({"success": True, "source": serialize_source(updated)})


@source_trust.route("/projects/<project_id>/sources/trust-summary", methods=["GET"])
@login_required
def source_trust_summary(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    rows = db.execute(
        """
        SELECT source_authority_level, sensitive_level, can_quote, can_visualize, citation_required, parse_status, COUNT(*) AS total
        FROM project_sources
        WHERE project_id=?
        GROUP BY source_authority_level, sensitive_level, can_quote, can_visualize, citation_required, parse_status
        """,
        (project_id,),
    ).fetchall()
    db.close()
    return jsonify({"summary": [dict(r) for r in rows], "project_id": project_id})
