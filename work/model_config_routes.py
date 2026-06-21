from flask import Blueprint, jsonify, request

from auth import current_token_payload, is_admin_role
from database import get_db, gen_id, now, log_operation
from model_config import (
    ensure_model_config_tables,
    list_providers,
    list_model_configs,
    upsert_model_config,
    config_to_public_dict,
    test_config_connectivity,
)

model_config_api = Blueprint("model_config_api", __name__, url_prefix="/api/model-config")


@model_config_api.before_request
def require_admin():
    if request.method == "OPTIONS":
        return None
    payload = current_token_payload()
    if not payload:
        return jsonify({"error": "未登录或 token 无效 / 已过期"}), 401
    if not is_admin_role(payload.get("role")):
        return jsonify({"error": "需要管理员权限"}), 403
    request.current_user = payload
    ensure_model_config_tables()
    return None


def is_super_admin():
    return request.current_user.get("role") == "超级管理员"


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
        print(f"[WARN] model config log failed: {exc}", flush=True)


@model_config_api.route("/providers", methods=["GET"])
def providers():
    return jsonify({"providers": list_providers()})


@model_config_api.route("/configs", methods=["GET"])
def configs():
    include_disabled = request.args.get("include_disabled", "1") != "0"
    return jsonify({"configs": list_model_configs(request.current_user, include_disabled=include_disabled)})


@model_config_api.route("/configs", methods=["POST"])
def save_config():
    data = request.get_json() or {}

    if data.get("scope") == "platform" and not is_super_admin():
        return jsonify({"error": "只有超级管理员可以配置平台级 API"}), 403

    if data.get("scope") == "org" and not is_super_admin():
        data["org_id"] = request.current_user.get("org_id", "")

    if data.get("scope") == "user":
        data["user_id"] = request.current_user.get("uid", "")

    config = upsert_model_config(data, request.current_user)
    log_admin("save_model_config", f"保存模型 API 配置 {config['id']} {config.get('provider')}/{config.get('model_name')}")
    return jsonify({"success": True, "config": config}), 201


@model_config_api.route("/configs/<config_id>", methods=["PUT"])
def update_config(config_id):
    data = request.get_json() or {}
    data["id"] = config_id

    db = get_db()
    existing = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    db.close()
    if not existing:
        return jsonify({"error": "配置不存在"}), 404

    if existing["scope"] == "platform" and not is_super_admin():
        return jsonify({"error": "只有超级管理员可以修改平台级 API"}), 403
    if existing["scope"] == "org" and not is_super_admin() and existing["org_id"] != request.current_user.get("org_id"):
        return jsonify({"error": "无权修改其他组织 API"}), 403
    if existing["scope"] == "user" and existing["user_id"] != request.current_user.get("uid") and not is_super_admin():
        return jsonify({"error": "无权修改其他用户 API"}), 403

    if not is_super_admin() and data.get("scope") == "platform":
        return jsonify({"error": "组织管理员不能升级为平台级 API"}), 403

    config = upsert_model_config(data, request.current_user)
    log_admin("update_model_config", f"更新模型 API 配置 {config_id}")
    return jsonify({"success": True, "config": config})


@model_config_api.route("/configs/<config_id>/status", methods=["POST"])
def update_status(config_id):
    data = request.get_json() or {}
    status = data.get("status", "disabled")
    if status not in {"enabled", "disabled"}:
        return jsonify({"error": "状态必须是 enabled 或 disabled"}), 400

    db = get_db()
    existing = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    if not existing:
        db.close()
        return jsonify({"error": "配置不存在"}), 404

    if existing["scope"] == "platform" and not is_super_admin():
        db.close()
        return jsonify({"error": "只有超级管理员可以修改平台级 API"}), 403

    db.execute("UPDATE model_api_configs SET status=?, updated_at=? WHERE id=?", (status, now(), config_id))
    db.commit()
    row = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    db.close()
    log_admin("update_model_config_status", f"配置 {config_id} -> {status}")
    return jsonify({"success": True, "config": config_to_public_dict(row)})


@model_config_api.route("/configs/<config_id>/test", methods=["POST"])
def test_config(config_id):
    db = get_db()
    row = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "配置不存在"}), 404

    if row["scope"] == "platform" and not is_super_admin():
        db.close()
        return jsonify({"error": "只有超级管理员可以测试平台级 API"}), 403
    if row["scope"] == "org" and not is_super_admin() and row["org_id"] != request.current_user.get("org_id"):
        db.close()
        return jsonify({"error": "无权测试其他组织 API"}), 403

    ok, message = test_config_connectivity(dict(row))
    db.execute(
        "UPDATE model_api_configs SET last_test_status=?, last_test_message=?, last_test_at=?, updated_at=? WHERE id=?",
        ("success" if ok else "failed", message, now(), now(), config_id)
    )
    db.commit()
    updated = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    db.close()

    log_admin("test_model_config", f"测试模型 API 配置 {config_id}: {message}")
    return jsonify({"success": ok, "message": message, "config": config_to_public_dict(updated)})


@model_config_api.route("/configs/<config_id>", methods=["DELETE"])
def delete_config(config_id):
    db = get_db()
    existing = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    if not existing:
        db.close()
        return jsonify({"error": "配置不存在"}), 404

    if existing["scope"] == "platform" and not is_super_admin():
        db.close()
        return jsonify({"error": "只有超级管理员可以删除平台级 API"}), 403

    db.execute("DELETE FROM model_api_configs WHERE id=?", (config_id,))
    db.commit()
    db.close()
    log_admin("delete_model_config", f"删除模型 API 配置 {config_id}")
    return jsonify({"success": True})
