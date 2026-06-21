import json
import time
import random
from pathlib import Path

from flask import Blueprint, jsonify, request
from database import get_db, gen_id, log_operation, hash_password
from auth import current_token_payload, is_admin_role

admin = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin.before_request
def require_admin_access():
    if request.method == "OPTIONS":
        return None

    payload = current_token_payload()
    if not payload:
        return jsonify({"error": "未登录或 token 无效 / 已过期"}), 401
    if not is_admin_role(payload.get("role")):
        return jsonify({"error": "需要管理员权限"}), 403

    request.current_user = payload
    return None


def is_super_admin():
    return request.current_user.get("role") == "超级管理员"


def current_org_id():
    return request.current_user.get("org_id", "")


def log_admin(action, detail=""):
    try:
        log_operation(
            user_id=request.current_user.get("uid", ""),
            user_name=request.current_user.get("name", ""),
            action=action,
            detail=detail,
            ip=request.headers.get("X-Forwarded-For", request.remote_addr or "")
        )
    except Exception as exc:
        print(f"[WARN] admin log failed: {exc}", flush=True)


@admin.route("/dashboard")
def dashboard():
    db = get_db()
    org_filter = "" if is_super_admin() else " WHERE org_id=?"
    org_params = [] if is_super_admin() else [current_org_id()]

    total_users = db.execute(f"SELECT COUNT(*) FROM users{org_filter}", org_params).fetchone()[0]
    active_users = db.execute(f"SELECT COUNT(*) FROM users{org_filter + (' AND ' if org_filter else ' WHERE ')}status='active'", org_params).fetchone()[0]
    total_orgs = db.execute(
        "SELECT COUNT(*) FROM organizations WHERE status='active'" if is_super_admin() else "SELECT COUNT(*) FROM organizations WHERE id=? AND status='active'",
        [] if is_super_admin() else [current_org_id()]
    ).fetchone()[0]
    total_projects = db.execute(f"SELECT COUNT(*) FROM projects{org_filter}", org_params).fetchone()[0]

    template_published = db.execute("SELECT COUNT(*) FROM templates WHERE status='published'").fetchone()[0]
    billing_filter = "" if is_super_admin() else " AND org_id=?"
    total_revenue = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='充值' AND status='completed'{billing_filter}",
        org_params
    ).fetchone()[0]
    total_usage = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='消费' AND status='completed'{billing_filter}",
        org_params
    ).fetchone()[0]

    recent_users = [dict(r) for r in db.execute(
        f"SELECT * FROM users{org_filter} ORDER BY created_at DESC LIMIT 4",
        org_params
    ).fetchall()]
    recent_billing = [dict(r) for r in db.execute(
        f"SELECT * FROM billing_records WHERE 1=1{billing_filter} ORDER BY created_at DESC LIMIT 4",
        org_params
    ).fetchall()]

    if is_super_admin():
        org_usage = [dict(r) for r in db.execute("""
            SELECT o.name, COUNT(p.id) as projects
            FROM organizations o LEFT JOIN projects p ON o.id=p.org_id
            GROUP BY o.id
        """).fetchall()]
    else:
        org_usage = [dict(r) for r in db.execute("""
            SELECT o.name, COUNT(p.id) as projects
            FROM organizations o LEFT JOIN projects p ON o.id=p.org_id
            WHERE o.id=?
            GROUP BY o.id
        """, (current_org_id(),)).fetchall()]

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


@admin.route("/users", methods=["GET"])
def list_users():
    db = get_db()
    q = request.args.get("q", "").lower()
    status_filter = request.args.get("status", "")

    query = "SELECT u.*, COALESCE(o.name,'') as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id"
    params = []
    conditions = []

    if not is_super_admin():
        conditions.append("u.org_id=?")
        params.append(current_org_id())

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
def add_user():
    data = request.get_json() or {}

    org_id = data.get("org_id", "")
    if not is_super_admin():
        org_id = current_org_id()
        if data.get("role") == "超级管理员":
            return jsonify({"error": "组织管理员不能创建超级管理员"}), 403

    db = get_db()
    uid = gen_id("u")
    db.execute(
        "INSERT INTO users (id,name,org_id,role,phone,password_hash) VALUES (?,?,?,?,?,?)",
        (
            uid,
            data.get("name", "新用户"),
            org_id,
            data.get("role", "内容创作"),
            data.get("phone", ""),
            hash_password(data.get("password", "123456"))
        )
    )
    db.commit()
    user = dict(db.execute(
        "SELECT u.*, COALESCE(o.name,'') as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id WHERE u.id=?",
        (uid,)
    ).fetchone())
    db.close()

    log_admin("admin_add_user", f"创建用户 {uid}")
    return jsonify({"success": True, "user": user}), 201


@admin.route("/users/<user_id>", methods=["PUT"])
def update_user(user_id):
    data = request.get_json() or {}

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        db.close()
        return jsonify({"error": "用户不存在"}), 404

    if not is_super_admin() and target["org_id"] != current_org_id():
        db.close()
        return jsonify({"error": "无权修改其他组织用户"}), 403

    fields = []
    params = []
    allowed_fields = ["name", "role", "phone", "status"] if not is_super_admin() else ["name", "org_id", "role", "phone", "status"]

    for k in allowed_fields:
        if k in data:
            if k == "role" and not is_super_admin() and data[k] == "超级管理员":
                db.close()
                return jsonify({"error": "组织管理员不能授予超级管理员角色"}), 403
            fields.append(f"{k}=?")
            params.append(data[k])

    if "password" in data and data["password"]:
        fields.append("password_hash=?")
        params.append(hash_password(data["password"]))

    if not fields:
        db.close()
        return jsonify({"error": "无更新字段"}), 400

    params.append(user_id)
    db.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", params)
    db.commit()
    user = dict(db.execute(
        "SELECT u.*, COALESCE(o.name,'') as org_name FROM users u LEFT JOIN organizations o ON u.org_id=o.id WHERE u.id=?",
        (user_id,)
    ).fetchone())
    db.close()

    log_admin("admin_update_user", f"更新用户 {user_id}")
    return jsonify({"success": True, "user": user})


@admin.route("/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        db.close()
        return jsonify({"error": "用户不存在"}), 404

    if not is_super_admin() and target["org_id"] != current_org_id():
        db.close()
        return jsonify({"error": "无权删除其他组织用户"}), 403

    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    db.close()

    log_admin("admin_delete_user", f"删除用户 {user_id}")
    return jsonify({"success": True})


@admin.route("/users/batch", methods=["POST"])
def batch_users():
    data = request.get_json() or {}
    action = data.get("action")
    ids = data.get("ids", [])

    if not ids:
        return jsonify({"success": True, "affected": 0})

    placeholders = ",".join("?" * len(ids))
    db = get_db()

    if not is_super_admin():
        rows = db.execute(f"SELECT id FROM users WHERE id IN ({placeholders}) AND org_id=?", [*ids, current_org_id()]).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            db.close()
            return jsonify({"success": True, "affected": 0})
        placeholders = ",".join("?" * len(ids))

    if action == "delete":
        db.execute(f"DELETE FROM users WHERE id IN ({placeholders})", ids)
    elif action == "disable":
        db.execute(f"UPDATE users SET status='disabled' WHERE id IN ({placeholders})", ids)
    elif action == "enable":
        db.execute(f"UPDATE users SET status='active' WHERE id IN ({placeholders})", ids)
    else:
        db.close()
        return jsonify({"error": "不支持的批量操作"}), 400

    db.commit()
    db.close()

    log_admin("admin_batch_users", f"{action}: {len(ids)} users")
    return jsonify({"success": True, "affected": len(ids)})


@admin.route("/orgs", methods=["GET"])
def list_orgs():
    db = get_db()
    if is_super_admin():
        orgs = db.execute("""
            SELECT o.*, COUNT(DISTINCT u.id) as user_count, COUNT(DISTINCT p.id) as project_count
            FROM organizations o
            LEFT JOIN users u ON o.id=u.org_id
            LEFT JOIN projects p ON o.id=p.org_id
            GROUP BY o.id
            ORDER BY o.created_at
        """).fetchall()
    else:
        orgs = db.execute("""
            SELECT o.*, COUNT(DISTINCT u.id) as user_count, COUNT(DISTINCT p.id) as project_count
            FROM organizations o
            LEFT JOIN users u ON o.id=u.org_id
            LEFT JOIN projects p ON o.id=p.org_id
            WHERE o.id=?
            GROUP BY o.id
        """, (current_org_id(),)).fetchall()
    db.close()
    return jsonify({"orgs": [dict(r) for r in orgs]})


@admin.route("/orgs", methods=["POST"])
def add_org():
    if not is_super_admin():
        return jsonify({"error": "只有超级管理员可以创建组织"}), 403

    data = request.get_json() or {}
    db = get_db()
    oid = gen_id("org")
    db.execute(
        "INSERT INTO organizations (id,name,short_name,contact,phone) VALUES (?,?,?,?,?)",
        (oid, data.get("name", ""), data.get("short", data.get("short_name", "")), data.get("contact", ""), data.get("phone", ""))
    )
    db.commit()
    org = dict(db.execute("SELECT * FROM organizations WHERE id=?", (oid,)).fetchone())
    db.close()

    log_admin("admin_add_org", f"创建组织 {oid}")
    return jsonify({"success": True, "org": org}), 201


@admin.route("/orgs/<org_id>", methods=["PUT"])
def update_org(org_id):
    if not is_super_admin() and org_id != current_org_id():
        return jsonify({"error": "无权修改其他组织"}), 403

    data = request.get_json() or {}
    db = get_db()
    fields = []
    params = []
    for k in ["name", "short_name", "status", "contact", "phone"]:
        if k in data:
            if k == "status" and not is_super_admin():
                db.close()
                return jsonify({"error": "组织管理员不能修改组织状态"}), 403
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

    log_admin("admin_update_org", f"更新组织 {org_id}")
    return jsonify({"success": True, "org": org})


@admin.route("/orgs/<org_id>", methods=["DELETE"])
def delete_org(org_id):
    if not is_super_admin():
        return jsonify({"error": "只有超级管理员可以删除组织"}), 403

    db = get_db()
    db.execute("DELETE FROM organizations WHERE id=?", (org_id,))
    db.commit()
    db.close()

    log_admin("admin_delete_org", f"删除组织 {org_id}")
    return jsonify({"success": True})


@admin.route("/templates", methods=["GET"])
def list_admin_templates():
    db = get_db()
    rows = [dict(r) for r in db.execute("SELECT * FROM templates ORDER BY created_at DESC").fetchall()]
    db.close()
    return jsonify({"templates": rows})


@admin.route("/templates", methods=["POST"])
def add_template():
    data = request.get_json() or {}
    db = get_db()
    tid = gen_id("t")
    db.execute(
        "INSERT INTO templates (id,name,scenes,duration,description,category,status) VALUES (?,?,?,?,?,?,?)",
        (
            tid,
            data.get("name", ""),
            int(data.get("scenes", 4)),
            data.get("duration", "30秒"),
            data.get("description", ""),
            data.get("category", "政宣"),
            data.get("status", "draft"),
        )
    )
    db.commit()
    tpl = dict(db.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone())
    db.close()

    log_admin("admin_add_template", f"创建模板 {tid}")
    return jsonify({"success": True, "template": tpl}), 201


@admin.route("/templates/<tpl_id>", methods=["PUT"])
def update_template(tpl_id):
    data = request.get_json() or {}
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

    log_admin("admin_update_template", f"更新模板 {tpl_id}")
    return jsonify({"success": True, "template": tpl})


@admin.route("/templates/<tpl_id>", methods=["DELETE"])
def delete_template(tpl_id):
    db = get_db()
    db.execute("DELETE FROM templates WHERE id=?", (tpl_id,))
    db.commit()
    db.close()

    log_admin("admin_delete_template", f"删除模板 {tpl_id}")
    return jsonify({"success": True})


@admin.route("/billing")
def list_billing():
    db = get_db()
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 20))
    offset = (page - 1) * size

    if is_super_admin():
        total = db.execute("SELECT COUNT(*) FROM billing_records").fetchone()[0]
        records = [dict(r) for r in db.execute(
            "SELECT * FROM billing_records ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (size, offset)
        ).fetchall()]
    else:
        total = db.execute("SELECT COUNT(*) FROM billing_records WHERE org_id=?", (current_org_id(),)).fetchone()[0]
        records = [dict(r) for r in db.execute(
            "SELECT * FROM billing_records WHERE org_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (current_org_id(), size, offset)
        ).fetchall()]

    db.close()
    return jsonify({"records": records, "total": total, "page": page, "size": size})


@admin.route("/billing/stats")
def billing_stats():
    db = get_db()
    org_clause = "" if is_super_admin() else " AND org_id=?"
    params = [] if is_super_admin() else [current_org_id()]

    total_recharge = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='充值' AND status='completed'{org_clause}",
        params
    ).fetchone()[0]
    total_consume = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE type='消费' AND status='completed'{org_clause}",
        params
    ).fetchone()[0]
    pending = db.execute(
        f"SELECT COALESCE(SUM(amount),0) FROM billing_records WHERE status='pending'{org_clause}",
        params
    ).fetchone()[0]

    if is_super_admin():
        org_stats = [dict(r) for r in db.execute("""
            SELECT o.name,
                COALESCE((SELECT SUM(amount) FROM billing_records WHERE org_id=o.id AND type='充值' AND status='completed'),0) as recharge,
                COALESCE((SELECT SUM(amount) FROM billing_records WHERE org_id=o.id AND type='消费' AND status='completed'),0) as consume
            FROM organizations o WHERE o.status='active'
        """).fetchall()]
    else:
        org_stats = [dict(r) for r in db.execute("""
            SELECT o.name,
                COALESCE((SELECT SUM(amount) FROM billing_records WHERE org_id=o.id AND type='充值' AND status='completed'),0) as recharge,
                COALESCE((SELECT SUM(amount) FROM billing_records WHERE org_id=o.id AND type='消费' AND status='completed'),0) as consume
            FROM organizations o WHERE o.status='active' AND o.id=?
        """, (current_org_id(),)).fetchall()]

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


@admin.route("/settings", methods=["GET"])
def get_settings():
    config_path = Path(__file__).parent / "data" / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {
            "platformName": "政宣智作",
            "platformVersion": "0.3.0",
            "maintenance": False,
            "allowRegister": False,
            "defaultModelText": "GPT-4o",
            "defaultModelImage": "DALL·E 3",
            "defaultModelVideo": "Sora",
            "maxProjectsPerUser": 50,
            "maxFileSize": 500,
            "auditEnabled": True,
            "aiIdentifier": True,
            "orgApiEnabled": True,
            "personalApiEnabled": False,
            "notice": "系统已升级至 v0.3.0，新增权限隔离、文件鉴权和生成任务留痕。",
        }
    return jsonify({"config": config})


@admin.route("/settings", methods=["PUT"])
def update_settings():
    if not is_super_admin():
        return jsonify({"error": "只有超级管理员可以修改系统设置"}), 403

    data = request.get_json() or {}
    config_path = Path(__file__).parent / "data" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}

    for k, v in data.items():
        config[k] = v

    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    log_admin("admin_update_settings", "更新系统设置")
    return jsonify({"success": True, "config": config})


@admin.route("/logs")
def get_logs():
    db = get_db()
    if is_super_admin():
        logs = [dict(r) for r in db.execute("SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT 50").fetchall()]
    else:
        logs = [dict(r) for r in db.execute(
            "SELECT * FROM operation_logs WHERE user_id IN (SELECT id FROM users WHERE org_id=?) ORDER BY created_at DESC LIMIT 50",
            (current_org_id(),)
        ).fetchall()]
    db.close()
    return jsonify({"logs": logs})
