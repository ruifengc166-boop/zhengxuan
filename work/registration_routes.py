from flask import Blueprint, jsonify, request

from auth import create_token, current_token_payload, is_admin_role
from database import get_db, gen_id, hash_password, verify_password, log_operation, now

registration = Blueprint("registration", __name__, url_prefix="/api/registration")


def log_action(user_id="", user_name="", action="", detail=""):
    try:
        log_operation(user_id=user_id, user_name=user_name, action=action, detail=detail, ip=request.headers.get("X-Forwarded-For", request.remote_addr or ""))
    except Exception as exc:
        print(f"[WARN] registration log failed: {exc}", flush=True)


def public_user_payload(user, org_name=""):
    token = create_token(user["id"], user["name"], user["role"], user["org_id"] or "", org_name)
    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "org_id": user["org_id"],
            "org": org_name,
            "role": user["role"],
            "phone": user["phone"],
            "account_type": user["account_type"],
        }
    }


@registration.route("/personal", methods=["POST"])
def register_personal():
    """Personal creator signup: direct creation of a private personal workspace.

    This is the lightweight creator track. It does not require organization approval.
    The personal workspace is represented as an organization with org_type='personal'
    so the existing project/file isolation logic keeps working.
    """
    data = request.get_json() or {}
    name = (data.get("name") or data.get("nickname") or "").strip()
    phone = (data.get("phone") or "").strip().replace(" ", "")
    password = data.get("password") or ""

    if len(name) < 2:
        return jsonify({"error": "请输入昵称或姓名"}), 400
    if len(phone) < 6:
        return jsonify({"error": "请输入有效手机号"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
    if existing:
        db.close()
        return jsonify({"error": "该手机号已注册，请直接登录"}), 409

    user_id = gen_id("u")
    org_id = gen_id("person")
    org_name = f"{name}的个人创作空间"
    pwd = hash_password(password)

    db.execute(
        """
        INSERT INTO organizations (id,name,short_name,contact,phone,status,org_type,owner_user_id)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (org_id, org_name, "个人空间", name, phone, "active", "personal", user_id)
    )
    db.execute(
        """
        INSERT INTO users (id,name,nickname,org_id,role,phone,password_hash,status,account_type)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (user_id, name, name, org_id, "个人创作者", phone, pwd, "active", "personal")
    )
    db.execute(
        """
        INSERT INTO projects (id,name,org_id,user_id,status,progress,project_type,business_line,visibility,aspect_ratio)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (gen_id("p"), "我的第一个 AI 视频项目", org_id, user_id, "制作中", 0, "个人创作", "personal", "private", "16:9")
    )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    db.close()

    log_action(user_id, name, "register_personal", f"个人创作者注册 {phone}")
    return jsonify(public_user_payload(user, org_name)), 201


@registration.route("/org-application", methods=["POST"])
def submit_org_application():
    """Organization track: submit an application, then admin approves it."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip().replace(" ", "")
    password = data.get("password") or ""
    org_name = (data.get("org_name") or "").strip()

    if len(name) < 2:
        return jsonify({"error": "请输入联系人姓名"}), 400
    if len(phone) < 6:
        return jsonify({"error": "请输入有效手机号"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if len(org_name) < 2:
        return jsonify({"error": "请输入单位名称"}), 400

    db = get_db()
    existing_user = db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone()
    if existing_user:
        db.close()
        return jsonify({"error": "该手机号已存在，请登录或联系管理员"}), 409

    existing_pending = db.execute("SELECT id FROM registration_applications WHERE phone=? AND status='pending'", (phone,)).fetchone()
    if existing_pending:
        db.close()
        return jsonify({"error": "该手机号已有待审核申请"}), 409

    app_id = gen_id("app")
    db.execute(
        """
        INSERT INTO registration_applications (
            id,application_type,name,phone,password_hash,org_name,org_type,role_title,city,use_case,status
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            app_id,
            "org",
            name,
            phone,
            hash_password(password),
            org_name,
            data.get("org_type", "行政事业单位"),
            data.get("role_title", "宣传员"),
            data.get("city", ""),
            data.get("use_case", ""),
            "pending",
        )
    )
    db.commit()
    db.close()

    log_action("", name, "submit_org_application", f"单位申请 {org_name} / {phone}")
    return jsonify({"success": True, "application_id": app_id, "status": "pending", "message": "单位申请已提交，等待后台审核开通。"}), 201


@registration.route("/applications", methods=["GET"])
def list_applications():
    payload = current_token_payload()
    if not payload or not is_admin_role(payload.get("role")):
        return jsonify({"error": "需要管理员权限"}), 403

    status = request.args.get("status", "")
    db = get_db()
    params = []
    sql = "SELECT id,application_type,name,phone,org_name,org_type,role_title,city,use_case,status,assigned_user_id,assigned_org_id,review_note,reviewed_by,reviewed_at,created_at FROM registration_applications"
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    db.close()
    return jsonify({"applications": rows})


@registration.route("/applications/<application_id>/approve", methods=["POST"])
def approve_application(application_id):
    payload = current_token_payload()
    if not payload or not is_admin_role(payload.get("role")):
        return jsonify({"error": "需要管理员权限"}), 403

    db = get_db()
    app = db.execute("SELECT * FROM registration_applications WHERE id=?", (application_id,)).fetchone()
    if not app:
        db.close()
        return jsonify({"error": "申请不存在"}), 404
    if app["status"] != "pending":
        db.close()
        return jsonify({"error": "该申请已处理"}), 400

    existing_user = db.execute("SELECT id FROM users WHERE phone=?", (app["phone"],)).fetchone()
    if existing_user:
        db.close()
        return jsonify({"error": "该手机号已注册，无法审批通过"}), 409

    org_id = gen_id("org")
    user_id = gen_id("u")
    db.execute(
        """
        INSERT INTO organizations (id,name,short_name,contact,phone,status,org_type,owner_user_id)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (org_id, app["org_name"], app["org_name"][:12], app["name"], app["phone"], "active", "organization", user_id)
    )
    db.execute(
        """
        INSERT INTO users (id,name,org_id,role,phone,password_hash,status,account_type)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (user_id, app["name"], org_id, "管理员", app["phone"], app["password_hash"], "active", "org")
    )
    db.execute(
        """
        UPDATE registration_applications
        SET status='approved', assigned_user_id=?, assigned_org_id=?, reviewed_by=?, reviewed_at=?, review_note=?
        WHERE id=?
        """,
        (user_id, org_id, payload.get("uid", ""), now(), (request.get_json() or {}).get("note", "审批通过"), application_id)
    )
    db.commit()
    db.close()

    log_action(payload.get("uid", ""), payload.get("name", ""), "approve_org_application", f"审批通过 {application_id}")
    return jsonify({"success": True, "org_id": org_id, "user_id": user_id})


@registration.route("/applications/<application_id>/reject", methods=["POST"])
def reject_application(application_id):
    payload = current_token_payload()
    if not payload or not is_admin_role(payload.get("role")):
        return jsonify({"error": "需要管理员权限"}), 403

    data = request.get_json() or {}
    db = get_db()
    app = db.execute("SELECT * FROM registration_applications WHERE id=?", (application_id,)).fetchone()
    if not app:
        db.close()
        return jsonify({"error": "申请不存在"}), 404

    db.execute(
        """
        UPDATE registration_applications
        SET status='rejected', reviewed_by=?, reviewed_at=?, review_note=?
        WHERE id=?
        """,
        (payload.get("uid", ""), now(), data.get("note", "未通过"), application_id)
    )
    db.commit()
    db.close()

    log_action(payload.get("uid", ""), payload.get("name", ""), "reject_org_application", f"拒绝申请 {application_id}")
    return jsonify({"success": True})
