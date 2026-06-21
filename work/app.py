import os
import json
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, make_response
from flask_cors import CORS
from werkzeug.utils import secure_filename

from database import (
    get_db,
    gen_id,
    now,
    hash_password,
    verify_password,
    needs_password_rehash,
    log_operation,
    UPLOAD_DIR,
)
from auth import create_token, login_required, is_admin_role
from admin_routes import admin
from workflow_routes import workflow
from model_config_routes import model_config_api
from registration_routes import registration
from model_config import resolve_model_config

app = Flask(__name__, static_folder="public", static_url_path="")

cors_origins = os.environ.get("CORS_ORIGINS", "*")
CORS(app, resources={r"/api/*": {"origins": cors_origins.split(",") if cors_origins != "*" else "*"}})

BASE_DIR = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "public"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024

app.register_blueprint(admin)
app.register_blueprint(workflow)
app.register_blueprint(model_config_api)
app.register_blueprint(registration)

ALLOWED_UPLOAD_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".mp4", ".mov", ".avi",
    ".mp3", ".wav",
    ".pdf", ".doc", ".docx", ".xlsx", ".pptx", ".txt"
}


def user_is_admin(user):
    return is_admin_role((user or {}).get("role"))


def user_is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


def get_project_for_user(db, project_id, user):
    project = db.execute(
        "SELECT p.*, o.name as org_name, o.org_type FROM projects p LEFT JOIN organizations o ON p.org_id=o.id WHERE p.id=?",
        (project_id,)
    ).fetchone()
    if not project:
        return None, (jsonify({"error": "项目不存在"}), 404)
    if user_is_super_admin(user):
        return project, None
    if project["org_id"] and project["org_id"] == user.get("org_id"):
        return project, None
    if project["user_id"] == user.get("uid"):
        return project, None
    return None, (jsonify({"error": "无权访问该项目"}), 403)


def log_current_user(action, detail=""):
    user = getattr(request, "current_user", {}) or {}
    try:
        log_operation(
            user_id=user.get("uid", ""),
            user_name=user.get("name", ""),
            action=action,
            detail=detail,
            ip=request.headers.get("X-Forwarded-For", request.remote_addr or "")
        )
    except Exception as exc:
        print(f"[WARN] operation log failed: {exc}", flush=True)


def index_response():
    """Serve the original single-file prototype with the non-destructive UI 2.0 layer."""
    html = (PUBLIC_DIR / "index.html").read_text(encoding="utf-8")
    css_tag = '<link rel="stylesheet" href="/ui2.css?v=20260621">'
    if css_tag not in html:
        html = html.replace("</head>", f"  {css_tag}\n</head>")
    response = make_response(html)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    return response


@app.before_request
def log_request():
    ts = time.strftime("%H:%M:%S", time.localtime())
    if not request.path.startswith("/static/"):
        print(f"[{ts}] {request.method} {request.path}", flush=True)


@app.after_request
def add_security_headers(response):
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json() or {}
    phone = data.get("phone", "").strip().replace(" ", "")
    password = data.get("password", "")
    db = get_db()
    user = db.execute(
        "SELECT u.*, o.name as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id WHERE u.phone=?",
        (phone,)
    ).fetchone()
    if not user:
        db.close()
        return jsonify({"success": False, "error": "账号不存在"}), 401
    if user["status"] != "active":
        db.close()
        return jsonify({"success": False, "error": "账号已被禁用"}), 403
    if not verify_password(password, user["password_hash"]):
        db.close()
        return jsonify({"success": False, "error": "密码错误"}), 401
    if needs_password_rehash(user["password_hash"]):
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user["id"]))
    token = create_token(user["id"], user["name"], user["role"], user["org_id"] or "", user["org_name"] or "")
    db.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
    db.commit()
    db.close()
    return jsonify({
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "org": user["org_name"],
            "org_id": user["org_id"],
            "role": user["role"],
            "phone": user["phone"],
            "account_type": user["account_type"] if "account_type" in user.keys() else "org",
        }
    })


@app.route("/api/auth/send-code", methods=["POST"])
def send_code():
    return jsonify({"success": True, "message": "验证码接口待接入短信供应商，当前不创建真实登录态"})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    return jsonify({"success": True})


@app.route("/api/auth/me")
@login_required
def me():
    u = request.current_user
    return jsonify({"user": {"id": u["uid"], "name": u["name"], "org_id": u.get("org_id", ""), "org": u.get("org_name", ""), "role": u["role"]}})


@app.route("/api/projects", methods=["GET"])
@login_required
def list_projects():
    u = request.current_user
    db = get_db()
    base_sql = """
        SELECT p.*, o.name as org_name, o.org_type
        FROM projects p
        LEFT JOIN organizations o ON p.org_id=o.id
    """
    params = []
    where = []
    if not user_is_super_admin(u):
        if u.get("org_id"):
            where.append("(p.org_id=? OR p.user_id=?)")
            params.extend([u.get("org_id"), u.get("uid")])
        else:
            where.append("p.user_id=?")
            params.append(u.get("uid"))
    if request.args.get("status"):
        where.append("p.status=?")
        params.append(request.args["status"])
    if request.args.get("business_line"):
        where.append("p.business_line=?")
        params.append(request.args["business_line"])
    if where:
        base_sql += " WHERE " + " AND ".join(where)
    base_sql += " ORDER BY p.updated_at DESC"
    rows = db.execute(base_sql, params).fetchall()
    db.close()
    return jsonify({"projects": [dict(r) for r in rows]})


@app.route("/api/projects/<project_id>")
@login_required
def get_project(project_id):
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()
    sources = db.execute("SELECT * FROM project_sources WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()
    scripts = db.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version_no DESC", (project_id,)).fetchall()
    db.close()
    result = dict(project)
    result["scenes"] = [dict(s) for s in scenes]
    result["sources"] = [dict(s) for s in sources]
    result["scripts"] = [dict(s) for s in scripts]
    return jsonify({"project": result})


@app.route("/api/projects", methods=["POST"])
@login_required
def create_project():
    data = request.get_json() or {}
    u = request.current_user
    org_id = data.get("org_id") if user_is_admin(u) and data.get("org_id") else u.get("org_id", "")
    if not org_id:
        return jsonify({"error": "当前账号未绑定空间，无法创建项目"}), 400
    db = get_db()
    org = db.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone()
    business_line = data.get("business_line") or ("personal" if org and org["org_type"] == "personal" else "org")
    pid = gen_id("p")
    db.execute(
        """
        INSERT INTO projects (
            id,name,org_id,user_id,project_type,deadline,data_level,rule_pack,publish_channel,aspect_ratio,duration_target,business_line,visibility
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            pid,
            data.get("name", "新项目"),
            org_id,
            u["uid"],
            data.get("type", "个人创作" if business_line == "personal" else "宣传片"),
            data.get("deadline", ""),
            data.get("data_level", "L1"),
            data.get("rule_pack", ""),
            data.get("publish_channel", ""),
            data.get("aspect_ratio", "16:9"),
            data.get("duration_target", data.get("duration", "")),
            business_line,
            data.get("visibility", "private"),
        )
    )
    if data.get("script"):
        db.execute(
            "INSERT INTO script_versions (id,project_id,version_no,title,content,created_by) VALUES (?,?,?,?,?,?)",
            (gen_id("sv"), pid, 1, data.get("script_title", data.get("name", "脚本初稿")), data["script"], u["uid"])
        )
    shot_count = int(data.get("scenes") or data.get("shot_count") or 6)
    for i in range(shot_count):
        db.execute(
            "INSERT INTO project_scenes (id,project_id,name,scene_order,status,duration) VALUES (?,?,?,?,?,?)",
            (gen_id("s"), pid, f"镜头 {i + 1:02d}", i + 1, "待生成", data.get("default_scene_duration", "6s"))
        )
    db.commit()
    db.close()
    log_current_user("create_project", f"创建项目 {pid}: {data.get('name', '新项目')}")
    return jsonify({"success": True, "id": pid, "message": "项目已创建", "nextStep": "upload_sources"}), 201


@app.route("/api/templates")
def list_templates():
    db = get_db()
    rows = db.execute("SELECT * FROM templates WHERE status='published' ORDER BY usage_count DESC").fetchall()
    db.close()
    return jsonify({"templates": [dict(r) for r in rows]})


@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400
    original_name = Path(file.filename).name
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        return jsonify({"error": f"不支持的文件类型: {ext}"}), 400
    db = get_db()
    project_id = request.form.get("project_id", "").strip()
    org_id = request.current_user.get("org_id", "")
    if project_id:
        project, error = get_project_for_user(db, project_id, request.current_user)
        if error:
            db.close()
            return error
        org_id = project["org_id"]
    fid = gen_id("f")
    safe_name = f"{fid}{ext}"
    file_path = UPLOAD_DIR / safe_name
    file.save(str(file_path))
    fsize = file_path.stat().st_size
    data_level = request.form.get("data_level", "L1")
    db.execute(
        """
        INSERT INTO uploaded_files (id,filename,original_name,file_type,file_size,uploader_id,project_id,org_id,mime_type,data_level,storage_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (fid, safe_name, original_name, ext, fsize, request.current_user["uid"], project_id, org_id, file.mimetype or "", data_level, "local")
    )
    if project_id:
        db.execute(
            """
            INSERT INTO project_sources (id,project_id,org_id,file_id,title,source_type,data_level,parse_status,created_by)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (gen_id("src"), project_id, org_id, fid, original_name, "file", data_level, "pending", request.current_user["uid"])
        )
    db.commit()
    db.close()
    log_current_user("upload_file", f"上传文件 {original_name} 到项目 {project_id or '-'}")
    return jsonify({"success": True, "file": {"id": fid, "url": f"/uploads/{safe_name}", "original_name": original_name, "size": fsize, "type": ext, "data_level": data_level, "parse_status": "pending"}})


@app.route("/uploads/<filename>")
@login_required
def serve_upload(filename):
    db = get_db()
    record = db.execute("SELECT * FROM uploaded_files WHERE filename=?", (filename,)).fetchone()
    if not record:
        db.close()
        return jsonify({"error": "文件不存在"}), 404
    u = request.current_user
    allowed = user_is_super_admin(u) or record["uploader_id"] == u.get("uid") or (record["org_id"] and record["org_id"] == u.get("org_id"))
    if not allowed and record["project_id"]:
        project, error = get_project_for_user(db, record["project_id"], u)
        allowed = error is None
    db.close()
    if not allowed:
        return jsonify({"error": "无权访问该文件"}), 403
    return send_from_directory(UPLOAD_DIR, filename)


ENV_AI_MODELS = {
    "text": {"provider": os.environ.get("TEXT_PROVIDER", "openai"), "model": os.environ.get("TEXT_MODEL", "gpt-4o"), "api_key": os.environ.get("OPENAI_API_KEY", ""), "scope": "env"},
    "image": {"provider": os.environ.get("IMAGE_PROVIDER", "openai"), "model": os.environ.get("IMAGE_MODEL", "dall-e-3"), "api_key": os.environ.get("OPENAI_API_KEY", ""), "scope": "env"},
    "video": {"provider": os.environ.get("VIDEO_PROVIDER", "kling"), "model": os.environ.get("VIDEO_MODEL", "kling-1.6"), "api_key": os.environ.get("KLING_API_KEY", ""), "scope": "env"},
}


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


def create_generation_task(task_type, payload):
    db = get_db()
    project_id = (payload.get("project_id") or "").strip()
    scene_id = (payload.get("scene_id") or "").strip()
    org_id = request.current_user.get("org_id", "")
    if project_id:
        project, error = get_project_for_user(db, project_id, request.current_user)
        if error:
            db.close()
            return None, error
        org_id = project["org_id"]
    model_config = get_model_config_for_task(task_type, org_id=org_id, user_id=request.current_user.get("uid", ""))
    task_id = gen_id("task")
    prompt = payload.get("prompt", "")
    api_source = payload.get("api_source") or model_config.get("api_source") or model_config.get("scope", "platform")
    status = "queued" if model_config.get("api_key") else "simulated"
    progress = 0 if status == "queued" else 100
    safe_payload = dict(payload)
    safe_payload["api_config_id"] = model_config.get("api_config_id", "")
    safe_payload["model_base_url"] = model_config.get("base_url", "")
    safe_payload.pop("api_key", None)
    db.execute(
        """
        INSERT INTO generation_tasks (id,org_id,project_id,scene_id,task_type,status,progress,provider,model_name,api_source,prompt,params_json,estimated_cost,created_by,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (task_id, org_id, project_id, scene_id, task_type, status, progress, model_config["provider"], model_config["model"], api_source, prompt, json.dumps(safe_payload, ensure_ascii=False), float(payload.get("estimated_cost") or 0), request.current_user["uid"], now())
    )
    db.commit()
    db.close()
    log_current_user("create_generation_task", f"{task_type} task {task_id} project={project_id or '-'}")
    return {"taskId": task_id, "status": status, "progress": progress, "provider": model_config["provider"], "model": model_config["model"], "api_source": api_source, "api_config_id": model_config.get("api_config_id", ""), "estimate": "约 30 秒" if task_type == "image" else "约 1-3 分钟", "message": "已读取后台 API 配置并创建任务；下一步需要接入供应商任务提交适配器。" if status == "queued" else "当前为模拟模式：未找到启用的后台 API Key 或环境变量 API Key。"}, None


@app.route("/api/generate/images", methods=["POST"])
@login_required
def generate_images():
    result, error = create_generation_task("image", request.get_json() or {})
    if error:
        return error
    return jsonify(result), 202


@app.route("/api/generate/videos", methods=["POST"])
@login_required
def generate_videos():
    result, error = create_generation_task("video", request.get_json() or {})
    if error:
        return error
    return jsonify(result), 202


@app.route("/api/generate/status/<task_id>")
@login_required
def generation_status(task_id):
    db = get_db()
    task = db.execute("SELECT * FROM generation_tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        db.close()
        return jsonify({"error": "任务不存在"}), 404
    u = request.current_user
    if not user_is_super_admin(u) and task["created_by"] != u.get("uid") and task["org_id"] != u.get("org_id"):
        db.close()
        return jsonify({"error": "无权访问该任务"}), 403
    candidates = [dict(r) for r in db.execute("SELECT * FROM generation_candidates WHERE task_id=?", (task_id,)).fetchall()]
    result = dict(task)
    result["candidates"] = candidates
    db.close()
    return jsonify({"task": result})


@app.route("/api/generate/retry", methods=["POST"])
@login_required
def retry_generation():
    data = request.get_json() or {}
    task_id = data.get("task_id", "")
    db = get_db()
    old = db.execute("SELECT * FROM generation_tasks WHERE id=?", (task_id,)).fetchone()
    if not old:
        db.close()
        return jsonify({"error": "原任务不存在"}), 404
    if not user_is_super_admin(request.current_user) and old["org_id"] != request.current_user.get("org_id") and old["created_by"] != request.current_user.get("uid"):
        db.close()
        return jsonify({"error": "无权重试该任务"}), 403
    payload = json.loads(old["params_json"] or "{}")
    payload.update(data.get("overrides") or {})
    db.close()
    result, error = create_generation_task(old["task_type"], payload)
    if error:
        return error
    result["message"] = "重试任务已重新提交，原失败任务不会自动扣费。"
    return jsonify({"success": True, **result}), 202


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "政宣智作 API", "version": "0.6.0"})


@app.route("/admin/")
@app.route("/admin")
def serve_admin():
    return send_from_directory(PUBLIC_DIR / "admin", "index.html")


@app.route("/")
def serve_root():
    return index_response()


@app.route("/<path:subpath>")
def serve_frontend(subpath):
    if subpath.startswith("api/"):
        return jsonify({"error": "API endpoint not found"}), 404
    if subpath == "index.html":
        return index_response()
    file_path = PUBLIC_DIR / subpath
    if file_path.exists():
        return send_from_directory(PUBLIC_DIR, subpath)
    return index_response()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("RAILWAY_PORT", "3000")))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print("┌─────────────────────────────────────────────┐")
    print(f"│  政宣智作 · 开发服务器 v0.6.0                │")
    print(f"│  前端:  http://localhost:{port}                │")
    print(f"│  API:   http://localhost:{port}/api           │")
    print(f"│  管理后台: http://localhost:{port}/admin/      │")
    print("└─────────────────────────────────────────────┘")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}] 服务器已启动")
    print("[DB] 数据库已加载")
    app.run(host="0.0.0.0", port=port, debug=debug)
