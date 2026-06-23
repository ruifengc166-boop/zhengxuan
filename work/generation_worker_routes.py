import json

from flask import Blueprint, jsonify, request

from auth import login_required
from database import get_db, gen_id, now, log_operation
from platform_workflow_routes import ensure_platform_schema


generation_worker = Blueprint("generation_worker", __name__, url_prefix="/api/workflow")


def is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


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
        print(f"[WARN] generation worker log failed: {exc}", flush=True)


def safe_json(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def build_simulated_file_url(task, candidate_id):
    ext = "mp4" if task["task_type"] == "video" else "png"
    return f"simulated://{task['task_type']}/{candidate_id}.{ext}"


def score_for_task(task, adapter_run):
    params = safe_json(task["params_json"], {})
    prompt = task["prompt"] or ""
    citations = params.get("source_citations") or []
    asset_refs = params.get("asset_refs") or []
    score = {
        "mode": "simulated_worker",
        "prompt_length": len(prompt),
        "has_source_citations": bool(citations),
        "source_citations": citations,
        "asset_refs": asset_refs,
        "adapter_status": adapter_run["status"] if adapter_run else "none",
        "suggestion": "这是模拟候选，用于验证任务流转、候选入库和审核闭环；接入真实 worker 后替换为供应商返回结果。",
    }
    if len(prompt) < 80:
        score["risk"] = "prompt_too_short"
    elif not citations:
        score["risk"] = "missing_source_citation"
    else:
        score["risk"] = "low"
    return score


def process_task(db, task):
    adapter_run = db.execute(
        "SELECT * FROM generation_adapter_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
        (task["id"],),
    ).fetchone()
    existing = db.execute(
        "SELECT * FROM generation_candidates WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
        (task["id"],),
    ).fetchone()
    if existing:
        return {"task_id": task["id"], "status": "skipped", "reason": "已存在候选结果", "candidate": dict(existing)}

    candidate_id = gen_id("cand")
    score = score_for_task(task, adapter_run)
    file_url = build_simulated_file_url(task, candidate_id)
    thumb_url = f"simulated://thumb/{candidate_id}.jpg"
    db.execute(
        """
        INSERT INTO generation_candidates (
            id,task_id,project_id,scene_id,candidate_type,file_url,thumbnail_url,status,score_json,review_status,failure_reason,locked
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_id,
            task["id"],
            task["project_id"],
            task["scene_id"],
            task["task_type"],
            file_url,
            thumb_url,
            "generated",
            json.dumps(score, ensure_ascii=False),
            "unchecked",
            "",
            0,
        ),
    )
    db.execute(
        "UPDATE generation_tasks SET status='completed',progress=100,actual_cost=?,updated_at=? WHERE id=?",
        (task["estimated_cost"] or 0, now(), task["id"]),
    )
    if adapter_run:
        response = {
            "mode": "simulated_worker",
            "candidate_id": candidate_id,
            "file_url": file_url,
            "message": "已由本地模拟 worker 生成候选结果。",
        }
        db.execute(
            "UPDATE generation_adapter_runs SET status='succeeded',response_json=?,updated_at=? WHERE id=?",
            (json.dumps(response, ensure_ascii=False), now(), adapter_run["id"]),
        )
    db.execute(
        """
        INSERT INTO usage_records (id,org_id,project_id,task_id,user_id,provider,model_name,api_source,usage_unit,usage_amount,cost,status,note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            gen_id("usage"),
            task["org_id"],
            task["project_id"],
            task["id"],
            task["created_by"],
            task["provider"],
            task["model_name"],
            task["api_source"],
            task["task_type"],
            1,
            task["estimated_cost"] or 0,
            "simulated",
            "模拟 worker 回写，用于验证生产闭环",
        ),
    )
    return {"task_id": task["id"], "status": "completed", "candidate_id": candidate_id, "file_url": file_url, "score": score}


@generation_worker.route("/projects/<project_id>/generation-worker/run", methods=["POST"])
@login_required
def run_generation_worker(project_id):
    ensure_platform_schema()
    data = request.get_json() or {}
    limit = int(data.get("limit") or 20)
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    tasks = db.execute(
        """
        SELECT * FROM generation_tasks
        WHERE project_id=? AND status IN ('queued','simulated')
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    results = [process_task(db, task) for task in tasks]
    if results:
        db.execute("UPDATE projects SET progress=MAX(progress, 75), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()
    db.close()
    log_action("run_generation_worker", f"项目 {project_id} worker 处理 {len(results)} 个任务")
    return jsonify({"success": True, "project_id": project_id, "processed": len(results), "results": results})


@generation_worker.route("/projects/<project_id>/candidates", methods=["GET"])
@login_required
def list_project_candidates(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    rows = [dict(r) for r in db.execute(
        """
        SELECT c.*, t.provider, t.model_name, t.prompt, s.name AS scene_name, s.scene_order
        FROM generation_candidates c
        LEFT JOIN generation_tasks t ON t.id=c.task_id
        LEFT JOIN project_scenes s ON s.id=c.scene_id
        WHERE c.project_id=?
        ORDER BY c.created_at DESC
        """,
        (project_id,),
    ).fetchall()]
    for row in rows:
        row["score"] = safe_json(row.get("score_json"), {})
    db.close()
    return jsonify({"project_id": project_id, "candidates": rows})


@generation_worker.route("/candidates/<candidate_id>/lock", methods=["POST"])
@login_required
def lock_candidate(candidate_id):
    ensure_platform_schema()
    db = get_db()
    candidate = db.execute("SELECT * FROM generation_candidates WHERE id=?", (candidate_id,)).fetchone()
    if not candidate:
        db.close()
        return jsonify({"error": "候选不存在"}), 404
    project, error = get_project_for_user(db, candidate["project_id"], request.current_user)
    if error:
        db.close()
        return error

    db.execute("UPDATE generation_candidates SET locked=0 WHERE scene_id=? AND candidate_type=?", (candidate["scene_id"], candidate["candidate_type"]))
    db.execute("UPDATE generation_candidates SET locked=1,review_status='locked' WHERE id=?", (candidate_id,))
    if candidate["candidate_type"] == "image":
        db.execute("UPDATE project_scenes SET locked_image_url=?,locked_candidate_id=?,status='图片已锁定' WHERE id=?", (candidate["file_url"], candidate_id, candidate["scene_id"]))
    else:
        db.execute("UPDATE project_scenes SET locked_video_url=?,locked_candidate_id=?,status='视频已锁定' WHERE id=?", (candidate["file_url"], candidate_id, candidate["scene_id"]))
    db.commit()
    updated = dict(db.execute("SELECT * FROM generation_candidates WHERE id=?", (candidate_id,)).fetchone())
    db.close()
    log_action("lock_candidate", f"锁定候选 {candidate_id}")
    return jsonify({"success": True, "candidate": updated})
