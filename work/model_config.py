import json
import time
import urllib.error
import urllib.request

from database import get_db, gen_id, now
from secret_store import encrypt_secret, decrypt_secret, mask_secret

DEFAULT_PROVIDERS = [
    {
        "provider": "openai",
        "display_name": "OpenAI",
        "task_types": ["text", "image"],
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "dall-e-3"],
    },
    {
        "provider": "kling",
        "display_name": "可灵 Kling",
        "task_types": ["image", "video"],
        "base_url": "",
        "models": ["kling-1.6", "kling-2.0"],
    },
    {
        "provider": "seedance",
        "display_name": "Seedance / 火山引擎",
        "task_types": ["video"],
        "base_url": "",
        "models": ["seedance", "seedance-pro"],
    },
    {
        "provider": "wanxiang",
        "display_name": "通义万相",
        "task_types": ["image", "video"],
        "base_url": "",
        "models": ["wanx2.1", "wanx-video"],
    },
    {
        "provider": "hailuo",
        "display_name": "海螺 Hailuo",
        "task_types": ["video"],
        "base_url": "",
        "models": ["hailuo-video"],
    },
    {
        "provider": "custom",
        "display_name": "自定义 OpenAI-Compatible 接口",
        "task_types": ["text", "image", "video"],
        "base_url": "",
        "models": [],
    },
]


def ensure_model_config_tables():
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS model_providers (
        id TEXT PRIMARY KEY,
        provider TEXT UNIQUE,
        display_name TEXT DEFAULT '',
        task_types_json TEXT DEFAULT '[]',
        default_base_url TEXT DEFAULT '',
        default_models_json TEXT DEFAULT '[]',
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS model_api_configs (
        id TEXT PRIMARY KEY,
        scope TEXT DEFAULT 'platform',
        org_id TEXT DEFAULT '',
        user_id TEXT DEFAULT '',
        task_type TEXT DEFAULT 'video',
        provider TEXT DEFAULT '',
        display_name TEXT DEFAULT '',
        model_name TEXT DEFAULT '',
        base_url TEXT DEFAULT '',
        api_key_encrypted TEXT DEFAULT '',
        extra_headers_json TEXT DEFAULT '{}',
        params_json TEXT DEFAULT '{}',
        priority INTEGER DEFAULT 100,
        status TEXT DEFAULT 'disabled',
        last_test_status TEXT DEFAULT '',
        last_test_message TEXT DEFAULT '',
        last_test_at TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    """)

    for item in DEFAULT_PROVIDERS:
        existing = db.execute("SELECT id FROM model_providers WHERE provider=?", (item["provider"],)).fetchone()
        if not existing:
            db.execute(
                """
                INSERT INTO model_providers (id,provider,display_name,task_types_json,default_base_url,default_models_json,status,updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    gen_id("mp"),
                    item["provider"],
                    item["display_name"],
                    json.dumps(item["task_types"], ensure_ascii=False),
                    item["base_url"],
                    json.dumps(item["models"], ensure_ascii=False),
                    "active",
                    now(),
                )
            )
    db.commit()
    db.close()


def provider_to_dict(row):
    d = dict(row)
    d["task_types"] = json.loads(d.pop("task_types_json") or "[]")
    d["default_models"] = json.loads(d.pop("default_models_json") or "[]")
    return d


def config_to_public_dict(row):
    d = dict(row)
    d["api_key_masked"] = mask_secret(d.get("api_key_encrypted") or "")
    d["has_api_key"] = bool(d.get("api_key_encrypted"))
    d.pop("api_key_encrypted", None)
    d["extra_headers"] = json.loads(d.pop("extra_headers_json") or "{}")
    d["params"] = json.loads(d.pop("params_json") or "{}")
    return d


def upsert_model_config(data, current_user):
    ensure_model_config_tables()
    db = get_db()
    config_id = data.get("id") or gen_id("mc")
    existing = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()

    api_key_encrypted = None
    if "api_key" in data and data.get("api_key"):
        api_key_encrypted = encrypt_secret(data["api_key"])

    scope = data.get("scope", "platform")
    org_id = data.get("org_id", "")
    user_id = data.get("user_id", "")

    if scope == "org" and not org_id:
        org_id = current_user.get("org_id", "")
    if scope == "user" and not user_id:
        user_id = current_user.get("uid", "")

    values = {
        "scope": scope,
        "org_id": org_id,
        "user_id": user_id,
        "task_type": data.get("task_type", "video"),
        "provider": data.get("provider", "custom"),
        "display_name": data.get("display_name", ""),
        "model_name": data.get("model_name", ""),
        "base_url": data.get("base_url", ""),
        "extra_headers_json": json.dumps(data.get("extra_headers", {}), ensure_ascii=False),
        "params_json": json.dumps(data.get("params", {}), ensure_ascii=False),
        "priority": int(data.get("priority", 100)),
        "status": data.get("status", "disabled"),
        "created_by": current_user.get("uid", ""),
    }

    if existing:
        fields = ["scope", "org_id", "user_id", "task_type", "provider", "display_name", "model_name", "base_url", "extra_headers_json", "params_json", "priority", "status"]
        params = [values[k] for k in fields]
        if api_key_encrypted is not None:
            fields.append("api_key_encrypted")
            params.append(api_key_encrypted)
        fields.append("updated_at")
        params.append(now())
        params.append(config_id)
        db.execute(f"UPDATE model_api_configs SET {','.join([f'{f}=?' for f in fields])} WHERE id=?", params)
    else:
        db.execute(
            """
            INSERT INTO model_api_configs (
                id,scope,org_id,user_id,task_type,provider,display_name,model_name,base_url,api_key_encrypted,
                extra_headers_json,params_json,priority,status,created_by,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                config_id,
                values["scope"], values["org_id"], values["user_id"], values["task_type"], values["provider"],
                values["display_name"], values["model_name"], values["base_url"], api_key_encrypted or "",
                values["extra_headers_json"], values["params_json"], values["priority"], values["status"],
                values["created_by"], now(),
            )
        )

    db.commit()
    row = db.execute("SELECT * FROM model_api_configs WHERE id=?", (config_id,)).fetchone()
    db.close()
    return config_to_public_dict(row)


def list_model_configs(current_user, include_disabled=True):
    ensure_model_config_tables()
    db = get_db()
    role = current_user.get("role")
    params = []
    where = []

    if role != "超级管理员":
        where.append("(scope='org' AND org_id=?) OR (scope='user' AND user_id=?)")
        params.extend([current_user.get("org_id", ""), current_user.get("uid", "")])

    if not include_disabled:
        where.append("status='enabled'")

    sql = "SELECT * FROM model_api_configs"
    if where:
        sql += " WHERE " + " AND ".join(f"({w})" for w in where)
    sql += " ORDER BY scope, task_type, priority ASC, updated_at DESC"
    rows = [config_to_public_dict(r) for r in db.execute(sql, params).fetchall()]
    db.close()
    return rows


def resolve_model_config(task_type, org_id="", user_id=""):
    ensure_model_config_tables()
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM model_api_configs
        WHERE status='enabled' AND task_type=?
          AND (
            scope='platform'
            OR (scope='org' AND org_id=?)
            OR (scope='user' AND user_id=?)
          )
        ORDER BY
          CASE scope WHEN 'user' THEN 1 WHEN 'org' THEN 2 ELSE 3 END,
          priority ASC,
          updated_at DESC
        LIMIT 1
        """,
        (task_type, org_id, user_id)
    ).fetchone()
    db.close()

    if not rows:
        return None

    d = dict(rows)
    d["api_key"] = decrypt_secret(d.pop("api_key_encrypted") or "")
    d["extra_headers"] = json.loads(d.pop("extra_headers_json") or "{}")
    d["params"] = json.loads(d.pop("params_json") or "{}")
    return d


def list_providers():
    ensure_model_config_tables()
    db = get_db()
    rows = [provider_to_dict(r) for r in db.execute("SELECT * FROM model_providers ORDER BY provider").fetchall()]
    db.close()
    return rows


def test_config_connectivity(config):
    """Non-destructive connectivity test.

    We only test base URL reachability and local API key decryptability. Vendor-specific
    task submission should be implemented in adapters later to avoid accidental charges.
    """
    api_key = decrypt_secret(config.get("api_key_encrypted") or "") if config.get("api_key_encrypted") else ""
    if not api_key:
        return False, "未填写 API Key"

    base_url = (config.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return True, "API Key 已保存；供应商 Base URL 为空，跳过连通性测试。"

    try:
        req = urllib.request.Request(base_url, method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("User-Agent", "zhengxuan-api-config-test/0.1")
        urllib.request.urlopen(req, timeout=8)
        return True, "Base URL 可访问。注意：这不代表生成接口已完成对接。"
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404, 405}:
            return True, f"Base URL 已响应 HTTP {exc.code}，Key 已可解密；请在供应商适配器里补真实调用。"
        return False, f"Base URL 响应异常 HTTP {exc.code}"
    except Exception as exc:
        return False, f"连通性测试失败：{exc}"
