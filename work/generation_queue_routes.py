import json
import os

from flask import Blueprint, jsonify, request

from auth import login_required, is_admin_role
from database import get_db, gen_id, now, log_operation
from model_config import resolve_model_config
from platform_workflow_routes import ensure_platform_schema
from services.generation_service import build_generation_submission_plan


generation_queue = Blueprint("generation_queue", __name__, url_prefix="/api/workflow")

ENV_AI_MODELS = {
    "image": {
        "provider": os.environ.get("IMAGE_PROVIDER", "openai"),
        "model": os.environ.get("IMAGE_MODEL", "dall-e-3"),
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "scope": "env",
    },
    "video": {
        "provider": os.environ.get("VIDEO_PROVIDER", "kling"),
        "model": os.environ.get("VIDEO_MODEL", "kling-1.6"),
        "api_key": os.environ.get("KLING_API_KEY", ""),
        "scope": "env",
    },
}


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
        print(f"[WARN] generation queue log failed: {exc}", flush=True)


def get_model_config_for_task(task_type, org_id="", user_id=""):
    configured = resolve_model_config(task_type, org_id=org_id, user_id=user_id)
    if configured:
        return {
            "provider": configured.get("provider", ""),
            "model": configured.get("model_name", ""),
            "api_key": configured.get("api_key", ""),
            "api_config_id": configured.get("id", ""),
            "api_source": configured.get("scope", "platform"),
            "base_url": configured.get("base_url", ""),
            "params": configured.get("params", {}),
        }
    return ENV_AI_MODELS[task_type]


def latest_prompt_for_scene(db, scene_id, prompt_type):
    return db.execute(
        """
        SELECT * FROM shot_prompts
        WHERE scene_id=? AND prompt_type=?
        ORDER BY version_no DESC, created_at DESC
        LIMIT 1
        """,
        (scene_id, prompt_type),
    ).fetchone()


def scene_payload(scene, prompt, task_type, project_id):
    references = {}
    try:
        references = json.loads(prompt["references_json"] or "{}") if prompt else {}
    except Exception:
        references = {}
    return {
        "project_id": project_id,
        "scene_id": scene["id"],
        "scene_name": scene["name"],
        "prompt": prompt["prompt"] if prompt else scene["prompt"],
        "negative_prompt": prompt["negative_prompt"] if prompt else "",
        "duration": scene["duration"],
        "aspect_ratio": "16:9",
        "source_citations": references.get("source_citations", []),
        "asset_refs": references.get("asset_refs", []),
        "generation_mode": scene["generation_mode"] or references.get("generation_mode", "image_to_video"),
        "task_type": task_type,
    }


def create_task(db, project, scene, prompt, task_type, user, estimated_cost=0):
    model_config = get_model_config_for_task(task_type, org_id=project["org_id"], user_id=user.get("uid", ""))
    payload = scene_payload(scene, prompt, task_type, project["id"])
    payload["api_config_id"] = model_config.get("api_config_id", "")
    payload["model_base_url"] = model_config.get("base_url", "")
    adapter_plan = build_generation_submission_plan(task_type, model_config, payload)
    payload["adapter_plan"] = adapter_plan
    task_id = gen_id("task")
    api_source = model_config.get("api_source") or model_config.get("scope", "platform")
    status = "queued" if model_config.get("api_key") else "simulated"
    progress = 0 if status == "queued" else 100
    db.execute(
        """
        INSERT INTO generation_tasks (
            id,org_id,project_id,scene_id,task_type,status,progress,provider,model_name,api_source,prompt,params_json,estimated_cost,created_by,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            task_id,
            project["org_id"],
            project["id"],
            scene["id"],
            task_type,
            status,
            progress,
            model_config["provider"],
            model_config["model"],
            api_source,
            payload["prompt"],
            json.dumps(payload, ensure_ascii=False),
            float(estimated_cost or 0),
            user["uid"],
            now(),
        ),
    )
    db.execute(
        """
        INSERT INTO generation_adapter_runs (id,task_id,provider,model_name,adapter_name,request_json,status,updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            gen_id("gar"),
            task_id,
            model_config["provider"],
            model_config["model"],
            adapter_plan.get("adapter_name", ""),
            json.dumps(adapter_plan.get("request", {}), ensure_ascii=False),
            "planned" if adapter_plan.get("ready") else "simulated",
            now(),
        ),
    )
    return {
        "task_id": task_id,
        "scene_id": scene["id"],
        "scene_name": scene["name"],
        "task_type": task_type,
        "status": status,
        "provider": model_config["provider"],
        "model": model_config["model"],
        "adapter_plan": adapter_plan,
    }


def serialize_task(row, adapter_runs):
    item = dict(row)
    item["adapter_runs"] = adapter_runs.get(row["id"], [])
    try:
        item["params"] = json.loads(item.get("params_json") or "{}")
    except Exception:
        item["params"] = {}
    return item


@generation_queue.route("/projects/<project_id>/generation-queue", methods=["GET"])
@login_required
def get_generation_queue(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()
    scene_plans = []
    for scene in scenes:
        image_prompt = latest_prompt_for_scene(db, scene["id"], "image")
        video_prompt = latest_prompt_for_scene(db, scene["id"], "video")
        scene_plans.append({
            "scene_id": scene["id"],
            "scene_name": scene["name"],
            "scene_order": scene["scene_order"],
            "status": scene["status"],
            "generation_mode": scene["generation_mode"],
            "has_image_prompt": bool(image_prompt),
            "has_video_prompt": bool(video_prompt),
            "image_prompt": dict(image_prompt) if image_prompt else None,
            "video_prompt": dict(video_prompt) if video_prompt else None,
        })

    tasks = db.execute(
        "SELECT * FROM generation_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
        (project_id,),
    ).fetchall()
    task_ids = [t["id"] for t in tasks]
    adapter_runs = {}
    if task_ids:
        placeholders = ",".join(["?"] * len(task_ids))
        for run in db.execute(f"SELECT * FROM generation_adapter_runs WHERE task_id IN ({placeholders}) ORDER BY created_at DESC", task_ids).fetchall():
            adapter_runs.setdefault(run["task_id"], []).append(dict(run))
    db.close()
    return jsonify({
        "project_id": project_id,
        "scene_plans": scene_plans,
        "tasks": [serialize_task(t, adapter_runs) for t in tasks],
    })


@generation_queue.route("/projects/<project_id>/generation-queue/batch", methods=["POST"])
@login_required
def create_generation_batch(project_id):
    ensure_platform_schema()
    data = request.get_json() or {}
    requested_type = data.get("task_type", "image")
    if requested_type not in {"image", "video", "both"}:
        return jsonify({"error": "task_type 只能是 image、video 或 both"}), 400

    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    scene_ids = data.get("scene_ids") or []
    if scene_ids:
        placeholders = ",".join(["?"] * len(scene_ids))
        scenes = db.execute(
            f"SELECT * FROM project_scenes WHERE project_id=? AND id IN ({placeholders}) ORDER BY scene_order",
            [project_id, *scene_ids],
        ).fetchall()
    else:
        scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()

    task_types = ["image", "video"] if requested_type == "both" else [requested_type]
    created = []
    skipped = []
    for scene in scenes:
        for task_type in task_types:
            prompt = latest_prompt_for_scene(db, scene["id"], task_type)
            if not prompt and not scene["prompt"]:
                skipped.append({"scene_id": scene["id"], "task_type": task_type, "reason": "缺少 Prompt，请先生成结构化分镜"})
                continue
            created.append(create_task(db, project, scene, prompt, task_type, request.current_user, data.get("estimated_cost", 0)))

    db.execute("UPDATE projects SET progress=MAX(progress, 65), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()
    db.close()
    log_action("create_generation_batch", f"项目 {project_id} 创建生成任务 {len(created)} 个，跳过 {len(skipped)} 个")
    return jsonify({"success": True, "created": created, "skipped": skipped}), 202
