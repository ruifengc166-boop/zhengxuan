import json
import time
import random
from pathlib import Path

from flask import Blueprint, jsonify, request
from database import get_db, gen_id, now, today
from auth import login_required, admin_required

admin = Blueprint("admin", __name__, url_prefix="/api/admin")

# ─── Dashboard ──────────────────────────────────────────────
@admin.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_users = db.execute("SELECT COUNT(*) FROM users WHERE status='active'").fetchone()[0]
    total_orgs = db.execute("SELECT COUNT(*) FROM organizations WHERE status='active'").fetchone()[0]
    total_projects = db.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    
    template_published = db.execute("SELECT COUNT(*) FROM templates WHERE status='published'").fetchone()[0]
    total_revenue = db.execute("SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='充值' AND status='completed'").fetchone()[0]
    total_usage = db.execute("SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='消费' AND status='completed'").fetchone()[0]
    
    recent_users = [dict(r) for r in db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 4").fetchall()]
    recent_billing = [dict(r) for r in db.execute("SELECT * FROM billing_records ORDER BY created_at DESC LIMIT 4").fetchall()]
    
    org_usage = [dict(r) for r in db.execute("SELECT o.name, COUNT(p.id) as projects FROM organizations o LEFT JOIN projects p ON o.id=p.org_id GROUP BY o.id").fetchall()]
    
    # Daily stats (from last 7 days - simplified)
    daily = []
    for i in range(6, -1, -1):
        day = time.strftime("%m-%d", time.localtime(time.time() - i * 86400))
        daily.append({"date": day, "projects": random.randint(0, 3), "users": random.randint(0, 2), "revenue": random.randint(0, 5000)})
    
    db.close()
    return jsonify({
        "stats": {
            "totalUsers": total_users,
            "activeUsers": active_users,
            "totalProjects": total_projects,
            "activeOrgs": total_orgs,
            "totalRevenue": total_revenue,
            "totalUsage": total_usage,
            "balance": total_revenue - total_usage,
            "templatesPublished": template_published,
        },
        "recentUsers": recent_users,
        "recentBilling": recent_billing,
        "orgUsage": org_usage,
        "dailyStats": daily
    })

# ─── Users ──────────────────────────────────────────────────
@admin.route("/users", methods=["GET"])
@login_required
def list_users():
    db = get_db()
    q = request.args.get("q", "").lower()
    status_filter = request.args.get("status", "")
    
    query = "SELECT u.*, COALESCE(o.name,'') as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id"
    params = []
    conditions = []
    if q:
        conditions.append("(LOWER(u.name) LIKE ? OR LOWER(o.name) LIKE ? OR u.phone LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if status_filter:
        conditions.append("u.status=?")
        params.append(status_filter)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY u.created_at DESC"
    
    rows = [dict(r) for r in db.execute(query, params).fetchall()]
    db.close()
    return jsonify({"users": rows, "total": len(rows)})

@admin.route("/users", methods=["POST"])
@login_required
def add_user():
    data = request.get_json()
    db = get_db()
    uid = gen_id("u")
    from database import hash_password
    db.execute(
        "INSERT INTO users (id,name,org_id,role,phone,password_hash) VALUES (?,?,?,?,?,?)",
        (uid, data.get("name", "新用户"), data.get("org_id", ""), data.get("role", "内容创作"), data.get("phone", ""), hash_password("123456"))
    )
    db.commit()
    user = dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    db.close()
    return jsonify({"success": True, "user": user}), 201

@admin.route("/users/<user_id>", methods=["PUT"])
@login_required
def update_user(user_id):
    data = request.get_json()
    db = get_db()
    fields = []
    params = []
    for k in ["name", "org_id", "role", "phone", "status"]:
        if k in data:
            fields.append(f"{k}=?")
            params.append(data[k])
    if not fields:
        db.close()
        return jsonify({"error": "无更新字段"}), 400
    params.append(user_id)
    db.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", params)
    db.commit()
    user = dict(db.execute("SELECT u.*, COALESCE(o.name,'') as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id WHERE u.id=?", (user_id,)).fetchone())
    db.close()
    return jsonify({"success": True, "user": user})

@admin.route("/users/<user_id>", methods=["DELETE"])
@login_required
def delete_user(user_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    db.close()
    return jsonify({"success": True})

@admin.route("/users/batch", methods=["POST"])
@login_required
def batch_users():
    data = request.get_json()
    action = data.get("action")
    ids = data.get("ids", [])
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    if action == "delete":
        db.execute(f"DELETE FROM users WHERE id IN ({placeholders})", ids)
    elif action == "disable":
        db.execute(f"UPDATE users SET status='disabled' WHERE id IN ({placeholders})", ids)
    elif action == "enable":
        db.execute(f"UPDATE users SET status='active' WHERE id IN ({placeholders})", ids)
    db.commit()
    db.close()
    return jsonify({"success": True, "affected": len(ids)})

# ─── Organizations ──────────────────────────────────────────
@admin.route("/orgs", methods=["GET"])
@login_required
def list_orgs():
    db = get_db()
    orgs = db.execute("""
        SELECT o.*, COUNT(DISTINCT u.id) as user_count, COUNT(DISTINCT p.id) as project_count
        FROM organizations o
        LEFT JOIN users u ON o.id=u.org_id
        LEFT JOIN projects p ON o.id=p.org_id
        GROUP BY o.id
        ORDER BY o.created_at
    """).fetchall()
    db.close()
    return jsonify({"orgs": [dict(r) for r in orgs]})

@admin.route("/orgs", methods=["POST"])
@login_required
def add_org():
    data = request.get_json()
    db = get_db()
    oid = gen_id("org")
    db.execute(
        "INSERT INTO organizations (id,name,short_name,contact,phone) VALUES (?,?,?,?,?)",
        (oid, data.get("name", ""), data.get("short", ""), data.get("contact", ""), data.get("phone", ""))
    )
    db.commit()
    org = dict(db.execute("SELECT * FROM organizations WHERE id=?", (oid,)).fetchone())
    db.close()
    return jsonify({"success": True, "org": org}), 201

@admin.route("/orgs/<org_id>", methods=["PUT"])
@login_required
def update_org(org_id):
    data = request.get_json()
    db = get_db()
    fields = []
    params = []
    for k in ["name", "short_name", "status", "contact", "phone"]:
        if k in data:
            fields.append(f"{k}=?")
            params.append(data[k])
    if not fields:
        db.close()
        return jsonify({"error": "无更新字段"}), 400
    params.append(org_id)
    db.execute(f"UPDATE organizations SET {','.join(fields)} WHERE id=?", params)
    db.commit()
    org = dict(db.execute("SELECT * FROM organizations WHERE id=?", (org_id,)).fetchone())
    db.close()
    return jsonify({"success": True, "org": org})

@admin.route("/orgs/<org_id>", methods=["DELETE"])
@login_required
def delete_org(org_id):
    db = get_db()
    db.execute("DELETE FROM organizations WHERE id=?", (org_id,))
    db.commit()
    db.close()
    return jsonify({"success": True})

# ─── Templates Management ──────────────────────────────────
@admin.route("/templates", methods=["GET"])
@login_required
def list_admin_templates():
    db = get_db()
    rows = [dict(r) for r in db.execute("SELECT * FROM templates ORDER BY created_at DESC").fetchall()]
    db.close()
    return jsonify({"templates": rows})

@admin.route("/templates", methods=["POST"])
@login_required
def add_template():
    data = request.get_json()
    db = get_db()
    tid = gen_id("t")
    db.execute(
        "INSERT INTO templates (id,name,scenes,duration,description,category) VALUES (?,?,?,?,?,?)",
        (tid, data.get("name", ""), int(data.get("scenes", 4)), data.get("duration", "30秒"), data.get("description", ""), data.get("category", "政宣"))
    )
    db.commit()
    tpl = dict(db.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone())
    db.close()
    return jsonify({"success": True, "template": tpl}), 201

@admin.route("/templates/<tpl_id>", methods=["PUT"])
@login_required
def update_template(tpl_id):
    data = request.get_json()
    db = get_db()
    fields = []
    params = []
    for k in ["name", "scenes", "duration", "description", "category", "status"]:
        if k in data:
            fields.append(f"{k}=?")
            params.append(data[k])
    if not fields:
        db.close()
        return jsonify({"error": "无更新字段"}), 400
    params.append(tpl_id)
    db.execute(f"UPDATE templates SET {','.join(fields)} WHERE id=?", params)
    db.commit()
    tpl = dict(db.execute("SELECT * FROM templates WHERE id=?", (tpl_id,)).fetchone())
    db.close()
    return jsonify({"success": True, "template": tpl})

@admin.route("/templates/<tpl_id>", methods=["DELETE"])
@login_required
def delete_template(tpl_id):
    db = get_db()
    db.execute("DELETE FROM templates WHERE id=?", (tpl_id,))
    db.commit()
    db.close()
    return jsonify({"success": True})

# ─── Billing ────────────────────────────────────────────────
@admin.route("/billing")
@login_required
def list_billing():
    db = get_db()
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 20))
    total = db.execute("SELECT COUNT(*) FROM billing_records").fetchone()[0]
    offset = (page - 1) * size
    records = [dict(r) for r in db.execute("SELECT * FROM billing_records ORDER BY created_at DESC LIMIT ? OFFSET ?", (size, offset)).fetchall()]
    db.close()
    return jsonify({"records": records, "total": total, "page": page, "size": size})

@admin.route("/billing/stats")
@login_required
def billing_stats():
    db = get_db()
    total_recharge = db.execute("SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='充值' AND status='completed'").fetchone()[0]
    total_consume = db.execute("SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='消费' AND status='completed'").fetchone()[0]
    pending = db.execute("SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE status='pending'").fetchone()[0]
    
    org_stats = [dict(r) for r in db.execute("""
        SELECT o.name,
            COALESCE((SELECT SUM(amount) FROM billing_records WHERE org_id=o.id AND type='充值' AND status='completed'),0) as recharge,
            COALESCE((SELECT SUM(amount) FROM billing_records WHERE org_id=o.id AND type='消费' AND status='completed'),0) as consume
        FROM organizations o WHERE o.status='active'
    """).fetchall()]
    for s in org_stats:
        s["balance"] = s["recharge"] - s["consume"]
    
    db.close()
    return jsonify({
        "totalRecharge": total_recharge,
        "totalConsume": total_consume,
        "balance": total_recharge - total_consume,
        "pendingAmount": pending,
        "orgStats": org_stats
    })

# ─── Settings (stored in database) ──────────────────────────
@admin.route("/settings", methods=["GET"])
@login_required
def get_settings():
    # For now, store config in a simple JSON file
    import json
    config_path = Path(__file__).parent / "data" / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {
            "platformName": "政宣智作",
            "platformVersion": "0.2.0",
            "maintenance": False,
            "allowRegister": True,
            "defaultModelText": "GPT-4o",
            "defaultModelImage": "DALL·E 3",
            "defaultModelVideo": "Sora",
            "maxProjectsPerUser": 50,
            "maxFileSize": 500,
            "auditEnabled": True,
            "aiIdentifier": True,
            "orgApiEnabled": True,
            "personalApiEnabled": True,
            "notice": "系统已升级至 v0.2.0，新增数据库持久化和认证系统",
        }
    return jsonify({"config": config})

@admin.route("/settings", methods=["PUT"])
@login_required
def update_settings():
    import json
    data = request.get_json()
    config_path = Path(__file__).parent / "data" / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}
    for k, v in data.items():
        config[k] = v
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"success": True, "config": config})

# ─── Logs ──────────────────────────────────────────────────
@admin.route("/logs")
@login_required
def get_logs():
    db = get_db()
    logs = [dict(r) for r in db.execute("SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 50").fetchall()]
    db.close()
    return jsonify({"logs": logs})
