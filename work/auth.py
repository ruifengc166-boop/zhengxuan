import os
import jwt
import datetime
import hashlib
import functools
from flask import request, jsonify

SECRET_KEY = os.environ.get("JWT_SECRET", "zhengxuan-zhizuo-secret-key-2026")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

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

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "未登录或 token 已过期"}), 401
        token = auth_header[7:]
        payload = verify_token(token)
        if not payload:
            return jsonify({"error": "token 无效或已过期"}), 401
        request.current_user = payload
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if request.current_user.get("role") not in ("管理员", "超级管理员"):
            return jsonify({"error": "需要管理员权限"}), 403
        return f(*args, **kwargs)
    return decorated
