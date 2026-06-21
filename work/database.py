import sqlite3
import os
import hashlib
import hmac
import secrets
import uuid
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "zhengxuan.db"
UPLOAD_DIR = BASE_DIR / "uploads"

PBKDF2_ITERATIONS = int(os.environ.get("PASSWORD_HASH_ITERATIONS", "260000"))


def ensure_directories():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    ensure_directories()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _add_column_if_missing(conn, table_name, column_name, definition):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS organizations (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, short_name TEXT DEFAULT '',
        contact TEXT DEFAULT '', phone TEXT DEFAULT '', status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, org_id TEXT REFERENCES organizations(id),
        role TEXT DEFAULT '内容创作', phone TEXT UNIQUE, password_hash TEXT DEFAULT '',
        status TEXT DEFAULT 'active', last_login TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, org_id TEXT REFERENCES organizations(id),
        user_id TEXT REFERENCES users(id), status TEXT DEFAULT '制作中',
        progress INTEGER DEFAULT 0, deadline TEXT DEFAULT '', project_type TEXT DEFAULT '宣传片',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS project_scenes (
        id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), name TEXT DEFAULT '',
        scene_order INTEGER DEFAULT 0, status TEXT DEFAULT '待生成', duration TEXT DEFAULT '8s',
        prompt TEXT DEFAULT '', image_url TEXT DEFAULT '', video_url TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS templates (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, scenes INTEGER DEFAULT 4,
        duration TEXT DEFAULT '30秒', description TEXT DEFAULT '', category TEXT DEFAULT '政宣',
        status TEXT DEFAULT 'draft', usage_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS billing_records (
        id TEXT PRIMARY KEY, org_id TEXT REFERENCES organizations(id), org_name TEXT DEFAULT '',
        type TEXT DEFAULT '充值', amount REAL DEFAULT 0, method TEXT DEFAULT '',
        status TEXT DEFAULT 'completed', note TEXT DEFAULT '',
        date TEXT DEFAULT (CURRENT_DATE),
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS uploaded_files (
        id TEXT PRIMARY KEY, filename TEXT DEFAULT '', original_name TEXT DEFAULT '',
        file_type TEXT DEFAULT '', file_size INTEGER DEFAULT 0, uploader_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '', created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    CREATE TABLE IF NOT EXISTS operation_logs (
        id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', user_name TEXT DEFAULT '',
        action TEXT DEFAULT '', detail TEXT DEFAULT '', ip TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    """)
    migrate_db(conn)
    conn.commit()
    conn.close()


def migrate_db(conn=None):
    """Safe, idempotent migrations for the lightweight SQLite deployment.

    This keeps existing demo deployments running while adding the tables needed
    for a real AI-video workflow: sources, script versions, prompts, tasks,
    candidates, usage records, exports and review items.
    """
    own_conn = conn is None
    conn = conn or get_db()

    for table_name, column_name, definition in [
        ("uploaded_files", "org_id", "TEXT DEFAULT ''"),
        ("uploaded_files", "mime_type", "TEXT DEFAULT ''"),
        ("uploaded_files", "data_level", "TEXT DEFAULT 'L1'"),
        ("uploaded_files", "storage_status", "TEXT DEFAULT 'local'"),
        ("uploaded_files", "auth_status", "TEXT DEFAULT 'unchecked'"),
        ("project_scenes", "locked_image_url", "TEXT DEFAULT ''"),
        ("project_scenes", "locked_video_url", "TEXT DEFAULT ''"),
        ("project_scenes", "prompt_version", "INTEGER DEFAULT 1"),
        ("projects", "data_level", "TEXT DEFAULT 'L1'"),
        ("projects", "rule_pack", "TEXT DEFAULT ''"),
        ("projects", "publish_channel", "TEXT DEFAULT ''"),
        ("projects", "aspect_ratio", "TEXT DEFAULT '16:9'"),
        ("projects", "duration_target", "TEXT DEFAULT ''"),
    ]:
        try:
            _add_column_if_missing(conn, table_name, column_name, definition)
        except sqlite3.OperationalError:
            pass

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS project_sources (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id),
        org_id TEXT DEFAULT '',
        file_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        source_type TEXT DEFAULT 'file',
        source_url TEXT DEFAULT '',
        data_level TEXT DEFAULT 'L1',
        parse_status TEXT DEFAULT 'pending',
        citation_count INTEGER DEFAULT 0,
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS source_extractions (
        id TEXT PRIMARY KEY,
        source_id TEXT REFERENCES project_sources(id),
        content_type TEXT DEFAULT 'text',
        extracted_text TEXT DEFAULT '',
        facts_json TEXT DEFAULT '{}',
        risk_json TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS script_versions (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id),
        version_no INTEGER DEFAULT 1,
        title TEXT DEFAULT '',
        content TEXT DEFAULT '',
        source_coverage REAL DEFAULT 0,
        risk_status TEXT DEFAULT 'unchecked',
        locked INTEGER DEFAULT 0,
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS shot_prompts (
        id TEXT PRIMARY KEY,
        scene_id TEXT REFERENCES project_scenes(id),
        project_id TEXT REFERENCES projects(id),
        prompt_type TEXT DEFAULT 'image',
        version_no INTEGER DEFAULT 1,
        prompt TEXT DEFAULT '',
        negative_prompt TEXT DEFAULT '',
        references_json TEXT DEFAULT '{}',
        model_provider TEXT DEFAULT '',
        model_name TEXT DEFAULT '',
        locked INTEGER DEFAULT 0,
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS generation_tasks (
        id TEXT PRIMARY KEY,
        org_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        scene_id TEXT DEFAULT '',
        task_type TEXT DEFAULT 'video',
        status TEXT DEFAULT 'queued',
        progress INTEGER DEFAULT 0,
        provider TEXT DEFAULT '',
        model_name TEXT DEFAULT '',
        api_source TEXT DEFAULT 'platform',
        prompt TEXT DEFAULT '',
        params_json TEXT DEFAULT '{}',
        estimated_cost REAL DEFAULT 0,
        actual_cost REAL DEFAULT 0,
        supplier_task_id TEXT DEFAULT '',
        error_message TEXT DEFAULT '',
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS generation_candidates (
        id TEXT PRIMARY KEY,
        task_id TEXT REFERENCES generation_tasks(id),
        project_id TEXT DEFAULT '',
        scene_id TEXT DEFAULT '',
        candidate_type TEXT DEFAULT 'video',
        file_url TEXT DEFAULT '',
        thumbnail_url TEXT DEFAULT '',
        status TEXT DEFAULT 'generated',
        score_json TEXT DEFAULT '{}',
        review_status TEXT DEFAULT 'unchecked',
        failure_reason TEXT DEFAULT '',
        locked INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS usage_records (
        id TEXT PRIMARY KEY,
        org_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        task_id TEXT DEFAULT '',
        user_id TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        model_name TEXT DEFAULT '',
        api_source TEXT DEFAULT 'platform',
        usage_unit TEXT DEFAULT '',
        usage_amount REAL DEFAULT 0,
        cost REAL DEFAULT 0,
        status TEXT DEFAULT 'pending',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS review_items (
        id TEXT PRIMARY KEY,
        project_id TEXT DEFAULT '',
        org_id TEXT DEFAULT '',
        item_type TEXT DEFAULT 'content',
        severity TEXT DEFAULT 'R1',
        title TEXT DEFAULT '',
        evidence TEXT DEFAULT '',
        status TEXT DEFAULT 'open',
        owner_user_id TEXT DEFAULT '',
        due_at TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS exports (
        id TEXT PRIMARY KEY,
        project_id TEXT DEFAULT '',
        org_id TEXT DEFAULT '',
        version_label TEXT DEFAULT '',
        status TEXT DEFAULT 'locked',
        video_url TEXT DEFAULT '',
        package_url TEXT DEFAULT '',
        ai_label_enabled INTEGER DEFAULT 1,
        evidence_package_json TEXT DEFAULT '{}',
        confirmed_by TEXT DEFAULT '',
        confirmed_at TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    """)

    if own_conn:
        conn.commit()
        conn.close()


def log_operation(user_id="", user_name="", action="", detail="", ip=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO operation_logs (id,user_id,user_name,action,detail,ip) VALUES (?,?,?,?,?,?)",
        (gen_id("log"), user_id, user_name, action, detail, ip)
    )
    conn.commit()
    conn.close()


def seed_data():
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] > 0:
        conn.close()
        return

    pwd = hash_password("123456")
    conn.execute("DELETE FROM organizations")
    conn.executescript("""
    INSERT INTO organizations (id,name,short_name,contact,phone,status) VALUES
        ('org-001','市第一人民医院','市一院','陈志远','13800000001','active'),
        ('org-002','市教育局','市教育局','李明芳','13900000002','active'),
        ('org-003','市生态环境局','生态局','王建平','13700000003','active'),
        ('org-004','区卫健委','区卫健委','张雅然','13600000004','active'),
        ('org-005','市应急管理局','应急局','赵铁军','13500000005','disabled');
    """)

    users_data = [
        ('u-001','陈志远','org-001','内容创作','13800000001',pwd),
        ('u-002','李明芳','org-002','内容创作','13900000002',pwd),
        ('u-003','王建平','org-003','审核人','13700000003',pwd),
        ('u-004','张雅然','org-004','管理员','13600000004',pwd),
        ('u-005','赵铁军','org-005','内容创作','13500000005',pwd),
        ('admin','系统管理员','org-004','超级管理员','18800000000',pwd),
    ]
    for u in users_data:
        conn.execute("INSERT OR REPLACE INTO users (id,name,org_id,role,phone,password_hash) VALUES (?,?,?,?,?,?)", u)

    conn.execute("DELETE FROM templates")
    conn.executescript("""
    INSERT INTO templates (id,name,scenes,duration,description,category,status,usage_count) VALUES
        ('t-001','政宣系列·标准版',6,'60秒','适用政策解读、年度工作汇报','政宣','published',128),
        ('t-002','科普教育·简明版',4,'45秒','适用健康教育、科普宣传','科普','published',95),
        ('t-003','专题纪实·深度版',10,'120秒','适用人物专题、项目纪实','专题','published',67);
    """)

    conn.execute("DELETE FROM projects")
    conn.executescript("""
    INSERT INTO projects (id,name,org_id,user_id,status,progress,deadline,project_type) VALUES
        ('p-001','2026年医院宣传片','org-001','u-001','制作中',65,'2026-07-15','宣传片'),
        ('p-002','医师节专题短片','org-004','u-004','自检中',92,'2026-06-28','专题片'),
        ('p-003','公共卫生科普系列','org-002','u-002','制作中',45,'2026-07-30','系列短视频');
    """)

    conn.execute("DELETE FROM billing_records")
    conn.executescript("""
    INSERT INTO billing_records (org_id,org_name,type,amount,method,status,note) VALUES
        ('org-001','市第一人民医院','充值',5000,'对公转账','completed','Q3预存'),
        ('org-002','市教育局','充值',3000,'线上支付','completed',''),
        ('org-004','区卫健委','消费',380,'视频生成','completed','健康教育片'),
        ('org-001','市第一人民医院','消费',520,'视频生成','completed','医院宣传片'),
        ('org-003','市生态环境局','充值',2000,'对公转账','pending','待财务确认');
    """)

    conn.commit()
    conn.close()
    print(f"[DB] 种子数据已写入 ({len(users_data)} 个用户, 3 个模板, 3 个项目)")


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def _legacy_sha256(password):
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, hash_val):
    if not hash_val:
        return False

    if hash_val.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = hash_val.split("$", 3)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
            return hmac.compare_digest(digest, expected)
        except Exception:
            return False

    return hmac.compare_digest(_legacy_sha256(password), hash_val)


def needs_password_rehash(hash_val):
    return bool(hash_val) and not hash_val.startswith("pbkdf2_sha256$")


def gen_id(prefix):
    return f"{prefix}-{int(time.time() * 1000)}{uuid.uuid4().hex[:4]}"


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def today():
    return time.strftime("%Y-%m-%d", time.localtime())


ensure_directories()
if not DB_PATH.exists():
    init_db()
    print(f"[DB] Database initialized: {DB_PATH}")
else:
    conn = get_db()
    migrate_db(conn)
    conn.commit()
    conn.close()
    print(f"[DB] Database exists: {DB_PATH}; migrations checked")
