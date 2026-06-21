import os
import jwt
import datetime
import functools
from flask import request, jsonify

DEFAULT_DEV_SECRET = "zhengxuan-zhizuo-secret-key-2026"
SECRET_KEY = os.environ.get("JWT_SECRET", DEFAULT_DEV_SECRET)
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

if SECRET_KEY == DEFAULT_DEV_SECRET and os.environ.get("APP_ENV", "").lower() in {"prod", "production"}:
    raise RuntimeError("生产环境必须设置 JWT_SECRET，不能使用默认开发密钥。")


def create_token(user_id, name, role, org_id, org_name):
    payload = {
        "uid": user_id,
        "name": name,
        "role": role,
        "org_id": org_id,
        "org_name": org_name,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def current_token_payload():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return verify_token(auth_header[7:])


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        payload = current_token_payload()
        if not payload:
            return jsonify({"error": "未登录或 token 无效 / 已过期"}), 401
        request.current_user = payload
        return f(*args, **kwargs)
    return decorated


def is_admin_role(role):
    return role in ("管理员", "超级管理员")


def admin_required(f):
    @functools.wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not is_admin_role(request.current_user.get("role")):
            return jsonify({"error": "需要管理员权限"}), 403
        return f(*args, **kwargs)
    return decorated
