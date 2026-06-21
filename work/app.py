import os
import sys
import json
import time
import random
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from database import get_db, gen_id, now, today, hash_password, verify_password
from auth import create_token, verify_token, login_required, admin_required
from admin_routes import admin

app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)

BASE_DIR = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "public"
UPLOAD_DIR = BASE_DIR / "uploads"
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB

app.register_blueprint(admin)

# ─── Request Logger ─────────────────────────────────────────
@app.before_request
def log_request():
    ts = time.strftime("%H:%M:%S", time.localtime())
    if not request.path.startswith("/static/"):
        print(f"[{ts}] {request.method} {request.path}", flush=True)

# ─── CORS Headers for all responses ─────────────────────────
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response

# ─── Auth Routes ────────────────────────────────────────────
@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def login():
    if request.method == "OPTIONS":
        return jsonify({}), 200
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    password = data.get("password", "")
    
    db = get_db()
    user = db.execute("SELECT u.*, o.name as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id WHERE u.phone=?", (phone,)).fetchone()
    db.close()
    
    if not user:
        return jsonify({"success": False, "error": "账号不存在"}), 401
    
    if user["status"] != "active":
        return jsonify({"success": False, "error": "账号已被禁用"}), 403
    
    if not verify_password(password, user["password_hash"]):
        return jsonify({"success": False, "error": "密码错误"}), 401
    
    token = create_token(user["id"], user["name"], user["role"], user["org_id"] or "", user["org_name"])
    
    # Update last login
    db = get_db()
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
            "role": user["role"],
            "phone": user["phone"],
        }
    })

@app.route("/api/auth/send-code", methods=["POST"])
def send_code():
    return jsonify({"success": True, "message": "验证码已发送"})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    return jsonify({"success": True})

@app.route("/api/auth/me")
@login_required
def me():
    u = request.current_user
    return jsonify({
        "user": {
            "id": u["uid"],
            "name": u["name"],
            "org": u.get("org_name", ""),
            "role": u["role"]
        }
    })

# ─── Projects ──────────────────────────────────────────────
@app.route("/api/projects", methods=["GET"])
@login_required
def list_projects():
    db = get_db()
    rows = db.execute("""
        SELECT p.*, o.name as org_name 
        FROM projects p 
        LEFT JOIN organizations o ON p.org_id=o.id 
        ORDER BY p.updated_at DESC
    """).fetchall()
    db.close()
    return jsonify({
        "projects": [dict(r) for r in rows]
    })

@app.route("/api/projects/<project_id>")
@login_required
def get_project(project_id):
    db = get_db()
    project = db.execute("SELECT p.*, o.name as org_name FROM projects p LEFT JOIN organizations o ON p.org_id=o.id WHERE p.id=?", (project_id,)).fetchone()
    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()
    db.close()
    if not project:
        return jsonify({"error": "项目不存在"}), 404
    result = dict(project)
    result["scenes"] = [dict(s) for s in scenes]
    return jsonify({"project": result})

@app.route("/api/projects", methods=["POST"])
@login_required
def create_project():
    data = request.get_json()
    db = get_db()
    pid = gen_id("p")
    db.execute(
        "INSERT INTO projects (id,name,org_id,user_id,project_type,deadline) VALUES (?,?,?,?,?,?)",
        (pid, data.get("name", "新项目"), request.current_user.get("org_id", ""), request.current_user["uid"], data.get("type", "宣传片"), data.get("deadline", ""))
    )
    db.commit()
    db.close()
    return jsonify({"success": True, "id": pid, "message": "项目已创建"})

# ─── Templates ──────────────────────────────────────────────
@app.route("/api/templates")
def list_templates():
    db = get_db()
    rows = db.execute("SELECT * FROM templates WHERE status='published' ORDER BY usage_count DESC").fetchall()
    db.close()
    return jsonify({"templates": [dict(r) for r in rows]})

# ─── File Upload ────────────────────────────────────────────
@app.route("/api/upload", methods=["POST"])
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400
    
    ext = Path(file.filename).suffix.lower()
    allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".avi", ".mp3", ".wav", ".pdf", ".doc", ".docx", ".xlsx", ".pptx", ".txt"}
    if ext not in allowed:
        return jsonify({"error": f"不支持的文件类型: {ext}"}), 400
    
    fid = gen_id("f")
    safe_name = f"{fid}{ext}"
    file_path = UPLOAD_DIR / safe_name
    file.save(str(file_path))
    
    fsize = file_path.stat().st_size
    db = get_db()
    db.execute(
        "INSERT INTO uploaded_files (id,filename,original_name,file_type,file_size,uploader_id,project_id) VALUES (?,?,?,?,?,?,?)",
        (fid, safe_name, file.filename, ext, fsize, request.current_user["uid"], request.form.get("project_id", ""))
    )
    db.commit()
    db.close()
    
    return jsonify({
        "success": True,
        "file": {
            "id": fid,
            "url": f"/uploads/{safe_name}",
            "original_name": file.filename,
            "size": fsize,
            "type": ext
        }
    })

@app.route("/uploads/<filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ─── AI Generation (architected for real API) ──────────────
AI_MODELS = {
    "text": {"provider": "openai", "model": "gpt-4o", "api_key": os.environ.get("OPENAI_API_KEY", "")},
    "image": {"provider": "openai", "model": "dall-e-3", "api_key": os.environ.get("OPENAI_API_KEY", "")},
    "video": {"provider": "kling", "model": "kling-1.6", "api_key": os.environ.get("KLING_API_KEY", "")},
}

@app.route("/api/generate/images", methods=["POST"])
@login_required
def generate_images():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    model_config = AI_MODELS["image"]
    
    # If API key is configured, make real call
    if model_config["api_key"]:
        try:
            # TODO: implement real API call for the configured provider
            # Placeholder for actual integration
            pass
        except Exception as e:
            return jsonify({"error": f"AI生成失败: {str(e)}"}), 500
    
    # Fallback: simulated generation
    task_id = gen_id("task")
    return jsonify({
        "taskId": task_id,
        "status": "simulated",
        "estimate": "约 30 秒",
        "message": "当前为模拟模式。如需真实AI生成，请在设置中配置API密钥。"
    })

@app.route("/api/generate/videos", methods=["POST"])
@login_required
def generate_videos():
    data = request.get_json() or {}
    model_config = AI_MODELS["video"]
    
    if model_config["api_key"]:
        try:
            pass  # TODO: implement real API call
        except Exception as e:
            return jsonify({"error": f"AI生成失败: {str(e)}"}), 500
    
    task_id = gen_id("task")
    return jsonify({
        "taskId": task_id,
        "status": "simulated",
        "estimate": "约 1-3 分钟",
        "message": "当前为模拟模式。如需真实AI生成，请在设置中配置API密钥。"
    })

@app.route("/api/generate/status/<task_id>")
@login_required
def generation_status(task_id):
    r = random.random()
    return jsonify({
        "taskId": task_id,
        "status": "completed" if r > 0.3 else "running",
        "progress": min(100, int(r * 100))
    })

@app.route("/api/generate/retry", methods=["POST"])
@login_required
def retry_generation():
    return jsonify({"success": True, "message": "任务已重新提交"})

# ─── Health ────────────────────────────────────────────────
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "政宣智作 API", "version": "0.2.0"})

# ─── Admin Panel ────────────────────────────────────────────
@app.route("/admin/")
@app.route("/admin")
def serve_admin():
    return send_from_directory(PUBLIC_DIR / "admin", "index.html")

# ─── Serve Frontend ──────────────────────────────────────────
@app.route("/")
def serve_root():
    return send_from_directory(PUBLIC_DIR, "index.html")

@app.route("/<path:subpath>")
def serve_frontend(subpath):
    if subpath.startswith("api/"):
        return jsonify({"error": "API endpoint not found"}), 404
    file_path = PUBLIC_DIR / subpath
    if file_path.exists():
        return send_from_directory(PUBLIC_DIR, subpath)
    return send_from_directory(PUBLIC_DIR, "index.html")

# ─── Start ──────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print("┌─────────────────────────────────────────────┐")
    print(f"│  政宣智作 · 开发服务器 v0.2.0                │")
    print(f"│                                             │")
    print(f"│  前端:  http://localhost:{port}                │")
    print(f"│  API:   http://localhost:{port}/api           │")
    print(f"│  管理后台: http://localhost:{port}/admin/      │")
    print("└─────────────────────────────────────────────┘")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}] 服务器已启动")
    print(f"[DB] 数据库已加载")
    app.run(host="0.0.0.0", port=port, debug=False)
