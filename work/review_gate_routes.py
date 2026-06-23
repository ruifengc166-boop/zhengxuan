import json

from flask import Blueprint, jsonify, request

from auth import login_required
from database import get_db, gen_id, now, log_operation
from platform_workflow_routes import ensure_platform_schema


review_gate = Blueprint("review_gate", __name__, url_prefix="/api/workflow")


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
        print(f"[WARN] review gate log failed: {exc}", flush=True)


def safe_json(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def build_item(project, item_type, severity, title, evidence, owner_user_id=""):
    return {
        "id": gen_id("rev"),
        "project_id": project["id"],
        "org_id": project["org_id"],
        "item_type": item_type,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "status": "open",
        "owner_user_id": owner_user_id or project["user_id"] or "",
        "due_at": "",
    }


def insert_review_item(db, item):
    db.execute(
        """
        INSERT INTO review_items (id,project_id,org_id,item_type,severity,title,evidence,status,owner_user_id,due_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            item["id"],
            item["project_id"],
            item["org_id"],
            item["item_type"],
            item["severity"],
            item["title"],
            item["evidence"],
            item["status"],
            item["owner_user_id"],
            item["due_at"],
            now(),
        ),
    )


def collect_review_items(db, project):
    project_id = project["id"]
    items = []
    sources = db.execute("SELECT * FROM project_sources WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()
    scripts = db.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version_no DESC", (project_id,)).fetchall()
    assets = db.execute("SELECT * FROM visual_assets WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
    candidates = db.execute("SELECT * FROM generation_candidates WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
    locked_candidates = [c for c in candidates if c["locked"]]

    if not project["objective"] or not project["target_audience"]:
        items.append(build_item(project, "brief", "R2", "项目 Brief 不完整", "宣传目标或目标受众为空，建议在交付前补齐。"))
    if not sources:
        items.append(build_item(project, "source", "R1", "缺少可信资料", "项目未上传任何资料，无法建立事实依据。"))
    else:
        unparsed = [s for s in sources if s["parse_status"] not in {"parsed", "done", "success"}]
        high_sensitive = [s for s in sources if s["sensitive_level"] == "high"] if "sensitive_level" in sources[0].keys() else []
        not_quotable = [s for s in sources if not s["can_quote"]] if "can_quote" in sources[0].keys() else []
        if unparsed:
            items.append(build_item(project, "source", "R2", "存在未解析资料", f"{len(unparsed)} 份资料尚未解析，可能影响脚本和分镜引用。"))
        if high_sensitive:
            items.append(build_item(project, "source", "R1", "存在高敏感资料", f"{len(high_sensitive)} 份资料标记为高敏感，交付前需要人工确认可用范围。"))
        if len(not_quotable) == len(sources):
            items.append(build_item(project, "source", "R1", "没有可引用资料", "全部资料均不可引用，无法形成可复核证据链。"))

    if not scripts:
        items.append(build_item(project, "script", "R1", "缺少脚本版本", "项目没有脚本版本，无法进入成片交付审核。"))
    if not scenes:
        items.append(build_item(project, "storyboard", "R1", "缺少镜头结构", "项目没有镜头。"))
    else:
        unstructured = [s for s in scenes if not s["scene_goal"] or not s["generation_mode"]]
        no_video_lock = [s for s in scenes if not s["locked_video_url"]]
        no_image_lock = [s for s in scenes if not s["locked_image_url"]]
        if unstructured:
            items.append(build_item(project, "storyboard", "R1", "存在未结构化镜头", f"{len(unstructured)} 个镜头缺少结构化目标或生成模式。"))
        if no_video_lock:
            items.append(build_item(project, "candidate", "R2", "部分镜头未锁定视频候选", f"{len(no_video_lock)} 个镜头尚未锁定视频候选。"))
        if len(no_image_lock) == len(scenes):
            items.append(build_item(project, "candidate", "R2", "缺少锁定图片候选", "所有镜头都没有锁定图片候选，建议至少锁定关键帧。"))

    if candidates and not locked_candidates:
        items.append(build_item(project, "candidate", "R1", "候选结果未确认", "已有候选结果，但没有任何候选被锁定到镜头。"))
    if assets:
        unchecked_assets = [a for a in assets if a["auth_status"] in {"unchecked", "restricted", "forbidden"}]
        if unchecked_assets:
            items.append(build_item(project, "asset", "R2", "存在未完全授权资产", f"{len(unchecked_assets)} 个视觉资产未确认授权或限制使用。"))
    else:
        items.append(build_item(project, "asset", "R2", "缺少视觉资产登记", "未登记 Logo、人物、场景、证书或 B-roll 等资产。"))

    for scene in scenes:
        refs = safe_json(scene["source_citations_json"] if "source_citations_json" in scene.keys() else "[]", [])
        if not refs:
            items.append(build_item(project, "citation", "R2", f"镜头 {scene['scene_order']} 缺少资料引用", f"{scene['name']} 没有 source citation。"))

    items.append(build_item(project, "ai_label", "R3", "建议启用 AI 生成标识", "交付包建议保留 AI 辅助生成说明和人工审核记录。"))
    return items


def summarize(items):
    counts = {"R1": 0, "R2": 0, "R3": 0}
    for item in items:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return {
        "pass": counts.get("R1", 0) == 0,
        "blocking_count": counts.get("R1", 0),
        "warning_count": counts.get("R2", 0),
        "notice_count": counts.get("R3", 0),
        "counts": counts,
    }


@review_gate.route("/projects/<project_id>/review/run", methods=["POST"])
@login_required
def run_review(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    db.execute("UPDATE review_items SET status='superseded',updated_at=? WHERE project_id=? AND item_type LIKE 'auto_%' AND status='open'", (now(), project_id))
    items = collect_review_items(db, project)
    for item in items:
        item["item_type"] = f"auto_{item['item_type']}"
        insert_review_item(db, item)
    summary = summarize(items)
    db.execute("UPDATE projects SET status=?,progress=MAX(progress, ?),updated_at=? WHERE id=?", ("自检通过" if summary["pass"] else "待修正", 92 if summary["pass"] else 80, now(), project_id))
    db.commit()
    db.close()
    log_action("run_publish_review", f"项目 {project_id} 发布前自检，阻断 {summary['blocking_count']}，警告 {summary['warning_count']}")
    return jsonify({"success": True, "summary": summary, "items": items})


@review_gate.route("/projects/<project_id>/review/items", methods=["GET"])
@login_required
def list_review_items(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM review_items WHERE project_id=? ORDER BY CASE severity WHEN 'R1' THEN 1 WHEN 'R2' THEN 2 ELSE 3 END, created_at DESC",
        (project_id,),
    ).fetchall()]
    open_items = [r for r in rows if r["status"] == "open"]
    summary = summarize(open_items)
    db.close()
    return jsonify({"project_id": project_id, "summary": summary, "items": rows})


@review_gate.route("/review-items/<item_id>/resolve", methods=["POST"])
@login_required
def resolve_review_item(item_id):
    ensure_platform_schema()
    db = get_db()
    item = db.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        db.close()
        return jsonify({"error": "审核项不存在"}), 404
    project, error = get_project_for_user(db, item["project_id"], request.current_user)
    if error:
        db.close()
        return error
    db.execute("UPDATE review_items SET status='resolved',updated_at=? WHERE id=?", (now(), item_id))
    db.commit()
    updated = dict(db.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone())
    db.close()
    log_action("resolve_review_item", f"解决审核项 {item_id}")
    return jsonify({"success": True, "item": updated})
