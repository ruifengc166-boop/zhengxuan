import json
import re
from flask import Blueprint, jsonify, request

from auth import login_required, is_admin_role
from database import get_db, gen_id, now, log_operation

platform_workflow = Blueprint("platform_workflow", __name__, url_prefix="/api/workflow")


def safe_json(value, fallback):
    try:
        if value in (None, ""):
            return fallback
        return json.loads(value)
    except Exception:
        return fallback


def dumps(data):
    return json.dumps(data, ensure_ascii=False)


def is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


def is_admin(user):
    return is_admin_role((user or {}).get("role"))


def ensure_platform_schema():
    """Idempotent schema upgrade for the trusted promo-film workflow layer.

    This is intentionally local to this module so the new workflow can be merged
    without forcing a destructive database rewrite. The existing SQLite prototype
    keeps working, while new fields become available as soon as the module is
    imported.
    """
    db = get_db()

    def columns(table_name):
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}

    def add_column(table_name, column_name, definition):
        if column_name not in columns(table_name):
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    for table_name, column_name, definition in [
        ("projects", "objective", "TEXT DEFAULT ''"),
        ("projects", "target_audience", "TEXT DEFAULT ''"),
        ("projects", "tone", "TEXT DEFAULT '克制、可信、清晰'"),
        ("projects", "forbidden_expressions", "TEXT DEFAULT ''"),
        ("projects", "required_messages", "TEXT DEFAULT ''"),
        ("projects", "approval_owner", "TEXT DEFAULT ''"),
        ("projects", "brief_json", "TEXT DEFAULT '{}'"),
        ("project_sources", "source_authority_level", "TEXT DEFAULT 'internal'"),
        ("project_sources", "source_date", "TEXT DEFAULT ''"),
        ("project_sources", "source_owner", "TEXT DEFAULT ''"),
        ("project_sources", "can_quote", "INTEGER DEFAULT 1"),
        ("project_sources", "can_visualize", "INTEGER DEFAULT 1"),
        ("project_sources", "sensitive_level", "TEXT DEFAULT 'normal'"),
        ("project_sources", "citation_required", "INTEGER DEFAULT 1"),
        ("project_sources", "notes", "TEXT DEFAULT ''"),
        ("project_scenes", "scene_goal", "TEXT DEFAULT ''"),
        ("project_scenes", "source_citations_json", "TEXT DEFAULT '[]'"),
        ("project_scenes", "shot_size", "TEXT DEFAULT ''"),
        ("project_scenes", "camera_movement", "TEXT DEFAULT ''"),
        ("project_scenes", "visual_subject", "TEXT DEFAULT ''"),
        ("project_scenes", "location", "TEXT DEFAULT ''"),
        ("project_scenes", "voiceover_text", "TEXT DEFAULT ''"),
        ("project_scenes", "subtitle_text", "TEXT DEFAULT ''"),
        ("project_scenes", "risk_notes", "TEXT DEFAULT ''"),
        ("project_scenes", "start_frame_url", "TEXT DEFAULT ''"),
        ("project_scenes", "end_frame_url", "TEXT DEFAULT ''"),
        ("project_scenes", "locked_candidate_id", "TEXT DEFAULT ''"),
        ("project_scenes", "generation_mode", "TEXT DEFAULT 'image_to_video'"),
        ("project_scenes", "asset_refs_json", "TEXT DEFAULT '[]'"),
        ("templates", "use_case", "TEXT DEFAULT ''"),
        ("templates", "required_sources_json", "TEXT DEFAULT '[]'"),
        ("templates", "script_structure_json", "TEXT DEFAULT '[]'"),
        ("templates", "shot_structure_json", "TEXT DEFAULT '[]'"),
        ("templates", "tone_rules_json", "TEXT DEFAULT '{}'"),
        ("templates", "risk_rules_json", "TEXT DEFAULT '{}'"),
        ("templates", "default_visual_style", "TEXT DEFAULT ''"),
        ("templates", "default_music_style", "TEXT DEFAULT ''"),
        ("templates", "default_voice_style", "TEXT DEFAULT ''"),
        ("templates", "export_checklist_json", "TEXT DEFAULT '[]'"),
    ]:
        add_column(table_name, column_name, definition)

    db.executescript("""
    CREATE TABLE IF NOT EXISTS visual_assets (
        id TEXT PRIMARY KEY,
        org_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        asset_type TEXT DEFAULT 'reference',
        title TEXT DEFAULT '',
        reference_file_id TEXT DEFAULT '',
        reference_url TEXT DEFAULT '',
        visual_description TEXT DEFAULT '',
        auth_status TEXT DEFAULT 'unchecked',
        source_owner TEXT DEFAULT '',
        usage_restriction TEXT DEFAULT '',
        risk_notes TEXT DEFAULT '',
        tags_json TEXT DEFAULT '[]',
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS brand_kits (
        id TEXT PRIMARY KEY,
        org_id TEXT DEFAULT '',
        project_id TEXT DEFAULT '',
        name TEXT DEFAULT '默认品牌包',
        logo_asset_id TEXT DEFAULT '',
        primary_color TEXT DEFAULT '',
        secondary_color TEXT DEFAULT '',
        font_hint TEXT DEFAULT '',
        subtitle_style TEXT DEFAULT '',
        end_card_rule TEXT DEFAULT '',
        ai_label_rule TEXT DEFAULT '发布前保留 AI 生成内容标识',
        created_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );

    CREATE TABLE IF NOT EXISTS generation_adapter_runs (
        id TEXT PRIMARY KEY,
        task_id TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        model_name TEXT DEFAULT '',
        adapter_name TEXT DEFAULT '',
        request_json TEXT DEFAULT '{}',
        response_json TEXT DEFAULT '{}',
        status TEXT DEFAULT 'planned',
        error_message TEXT DEFAULT '',
        created_at TEXT DEFAULT (CURRENT_TIMESTAMP),
        updated_at TEXT DEFAULT (CURRENT_TIMESTAMP)
    );
    """)
    db.commit()
    db.close()


ensure_platform_schema()


def log_action(action, detail=""):
    user = getattr(request, "current_user", {}) or {}
    try:
        log_operation(
            user_id=user.get("uid", ""),
            user_name=user.get("name", ""),
            action=action,
            detail=detail,
            ip=request.headers.get("X-Forwarded-For", request.remote_addr or ""),
        )
    except Exception as exc:
        print(f"[WARN] platform workflow log failed: {exc}", flush=True)


def get_project_for_user(db, project_id, user):
    project = db.execute(
        "SELECT p.*, o.name as org_name, o.org_type FROM projects p LEFT JOIN organizations o ON p.org_id=o.id WHERE p.id=?",
        (project_id,),
    ).fetchone()
    if not project:
        return None, (jsonify({"error": "项目不存在"}), 404)
    if is_super_admin(user):
        return project, None
    if project["org_id"] and project["org_id"] == user.get("org_id"):
        return project, None
    if project["user_id"] == user.get("uid"):
        return project, None
    return None, (jsonify({"error": "无权访问该项目"}), 403)


def clean_text(text):
    text = re.sub(r"\r\n?", "\n", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def collect_project_facts(db, project_id):
    rows = db.execute(
        """
        SELECT s.id as source_id, s.title, s.source_authority_level, s.can_quote, e.facts_json
        FROM project_sources s
        LEFT JOIN source_extractions e ON e.source_id=s.id
        WHERE s.project_id=?
        ORDER BY s.source_authority_level ASC, s.created_at ASC, e.created_at DESC
        """,
        (project_id,),
    ).fetchall()
    facts = []
    seen = set()
    citation_index = 1
    for row in rows:
        if row["source_id"] in seen:
            continue
        seen.add(row["source_id"])
        data = safe_json(row["facts_json"], {})
        for fact in data.get("facts", [])[:4]:
            claim = clean_text(fact.get("claim", ""))[:260]
            if not claim:
                continue
            facts.append({
                "citation": f"S{citation_index}",
                "source_id": row["source_id"],
                "title": row["title"],
                "authority": row["source_authority_level"],
                "can_quote": row["can_quote"],
                "claim": claim,
            })
            citation_index += 1
    return facts


def split_script_into_beats(content, count):
    text = clean_text(content)
    if not text:
        return [
            "以真实场景建立主题和机构可信度",
            "说明项目背景、公共价值和服务对象需求",
            "呈现关键工作流程和一线行动",
            "展示服务成效、数据或典型案例",
            "回应公众关切并给出清晰行动建议",
            "以品牌片尾和合规 AI 标识收束",
        ][:count]
    lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 8]
    if not lines:
        lines = [text]
    return [lines[i % len(lines)][:160] for i in range(count)]


def infer_visual_subject(beat, project):
    text = f"{beat} {project['name']} {project['org_name'] or ''}"
    if any(k in text for k in ["医院", "医生", "患者", "健康", "医疗", "门诊"]):
        return "医护人员、服务对象与真实医疗服务场景"
    if any(k in text for k in ["学校", "教育", "学生", "课堂", "教师"]):
        return "师生、课堂与校园公共服务场景"
    if any(k in text for k in ["园区", "招商", "企业", "产业", "营商"]):
        return "园区空间、企业团队与产业服务场景"
    if any(k in text for k in ["活动", "大会", "赛事", "展会"]):
        return "活动现场、观众互动与组织执行场景"
    return "工作人员、服务对象与真实业务场景"


def choose_generation_mode(beat, index, total):
    if any(k in beat for k in ["口播", "讲解", "讲话", "采访", "同期声"]):
        return "authorized_talking_head"
    if index == 0 or index == total - 1:
        return "reference_image_to_video"
    if any(k in beat for k in ["动作", "流程", "步骤", "演示"]):
        return "first_last_frame_to_video"
    return "image_to_video"


def build_scene_structure(project, beat, index, total, facts, assets, data):
    shot_sizes = ["远景", "中景", "近景", "特写"]
    movements = ["固定机位，轻微推近", "稳定横移", "缓慢推近", "固定机位，人物自然运动"]
    citation = facts[index % len(facts)]["citation"] if facts else ""
    asset_refs = [a["id"] for a in assets[:4]]
    generation_mode = choose_generation_mode(beat, index, total)
    subtitle = re.sub(r"[。；;，,]", " ", beat)[:34]
    return {
        "scene_goal": f"把脚本要点“{beat[:46]}”转化为可信、可复核的宣传片画面",
        "source_citations": [citation] if citation else [],
        "shot_size": data.get("shot_size") or shot_sizes[index % len(shot_sizes)],
        "camera_movement": data.get("camera_movement") or movements[index % len(movements)],
        "visual_subject": data.get("visual_subject") or infer_visual_subject(beat, project),
        "location": data.get("location") or project["org_name"] or "真实业务场景",
        "voiceover_text": beat,
        "subtitle_text": subtitle,
        "risk_notes": "不得虚构荣誉、数据、现场和人物身份；涉及真实人物、Logo、证书、地图、制服时必须使用已授权素材或保持通用化表达。",
        "generation_mode": generation_mode,
        "asset_refs": asset_refs,
    }


def build_image_prompt(project, scene_name, structure, style):
    citations = "、".join(structure["source_citations"]) or "待补来源"
    return (
        f"{style}；项目：{project['name']}；镜头：{scene_name}；镜头目的：{structure['scene_goal']}；"
        f"主体：{structure['visual_subject']}；场景：{structure['location']}；景别：{structure['shot_size']}；"
        f"画面要求：真实机构宣传片质感，光线干净，构图稳重，人物自然，避免广告大片式夸张特效；"
        f"来源标记：{citations}；合规要求：{structure['risk_notes']}"
    )


def build_video_prompt(scene_name, structure):
    return (
        f"镜头《{scene_name}》：以“{structure['voiceover_text'][:110]}”为叙事核心，生成 6-8 秒视频。"
        f"画面主体为{structure['visual_subject']}，场景为{structure['location']}，景别为{structure['shot_size']}，"
        f"运镜采用{structure['camera_movement']}。节奏克制、动作自然、主体一致，避免突然切镜、夸张转场、虚构证书荣誉和未授权肖像。"
    )


@platform_workflow.route("/projects/<project_id>/brief", methods=["GET", "PUT"])
@login_required
def project_brief(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    if request.method == "GET":
        result = dict(project)
        result["brief"] = safe_json(project["brief_json"], {})
        db.close()
        return jsonify({"project": result})

    data = request.get_json() or {}
    fields = []
    params = []
    for key in [
        "objective", "target_audience", "tone", "forbidden_expressions", "required_messages",
        "approval_owner", "rule_pack", "publish_channel", "aspect_ratio", "duration_target", "data_level",
    ]:
        if key in data:
            fields.append(f"{key}=?")
            params.append(data.get(key, ""))
    if "brief" in data:
        fields.append("brief_json=?")
        params.append(dumps(data.get("brief") or {}))
    if not fields:
        db.close()
        return jsonify({"error": "无更新字段"}), 400
    fields.append("updated_at=?")
    params.append(now())
    params.append(project_id)
    db.execute(f"UPDATE projects SET {','.join(fields)} WHERE id=?", params)
    db.commit()
    updated = dict(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())
    db.close()
    log_action("update_project_brief", f"更新项目 Brief {project_id}")
    return jsonify({"success": True, "project": updated})


@platform_workflow.route("/projects/<project_id>/assets", methods=["GET", "POST"])
@login_required
def project_assets(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    if request.method == "GET":
        rows = [dict(r) for r in db.execute(
            """
            SELECT * FROM visual_assets
            WHERE project_id=? OR (project_id='' AND org_id=?)
            ORDER BY updated_at DESC, created_at DESC
            """,
            (project_id, project["org_id"]),
        ).fetchall()]
        db.close()
        return jsonify({"assets": rows})

    data = request.get_json() or {}
    asset_id = gen_id("asset")
    db.execute(
        """
        INSERT INTO visual_assets (
            id,org_id,project_id,asset_type,title,reference_file_id,reference_url,visual_description,
            auth_status,source_owner,usage_restriction,risk_notes,tags_json,created_by,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            asset_id,
            project["org_id"],
            data.get("project_id", project_id),
            data.get("asset_type", "reference"),
            data.get("title", "未命名资产"),
            data.get("reference_file_id", ""),
            data.get("reference_url", ""),
            data.get("visual_description", ""),
            data.get("auth_status", "unchecked"),
            data.get("source_owner", ""),
            data.get("usage_restriction", ""),
            data.get("risk_notes", ""),
            dumps(data.get("tags", [])),
            request.current_user.get("uid", ""),
            now(),
        ),
    )
    db.commit()
    asset = dict(db.execute("SELECT * FROM visual_assets WHERE id=?", (asset_id,)).fetchone())
    db.close()
    log_action("create_visual_asset", f"创建视觉资产 {asset_id} project={project_id}")
    return jsonify({"success": True, "asset": asset}), 201


@platform_workflow.route("/projects/<project_id>/storyboard/structure", methods=["POST"])
@login_required
def generate_structured_storyboard(project_id):
    ensure_platform_schema()
    data = request.get_json() or {}
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    script = db.execute(
        "SELECT * FROM script_versions WHERE project_id=? ORDER BY locked DESC, version_no DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()
    target_count = int(data.get("scene_count") or len(scenes) or 6)
    if not scenes:
        for i in range(target_count):
            db.execute(
                "INSERT INTO project_scenes (id,project_id,name,scene_order,status,duration) VALUES (?,?,?,?,?,?)",
                (gen_id("s"), project_id, f"镜头 {i + 1:02d}", i + 1, "待结构化", data.get("duration", "6s")),
            )
        scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()

    facts = collect_project_facts(db, project_id)
    assets = [dict(r) for r in db.execute(
        "SELECT * FROM visual_assets WHERE project_id=? OR (project_id='' AND org_id=?) ORDER BY updated_at DESC LIMIT 12",
        (project_id, project["org_id"]),
    ).fetchall()]
    beats = split_script_into_beats(script["content"] if script else "", len(scenes))
    style = data.get("style") or project["tone"] or "纪实、克制、可信、公共服务宣传片"

    structured = []
    for idx, scene in enumerate(scenes):
        scene_name = scene["name"] or f"镜头 {idx + 1:02d}"
        structure = build_scene_structure(project, beats[idx], idx, len(scenes), facts, assets, data)
        image_prompt = build_image_prompt(project, scene_name, structure, style)
        video_prompt = build_video_prompt(scene_name, structure)
        references_json = dumps({
            "source_citations": structure["source_citations"],
            "asset_refs": structure["asset_refs"],
            "generation_mode": structure["generation_mode"],
        })

        for prompt_type, prompt_text, negative in [
            ("image", image_prompt, "夸张特效、虚假荣誉、过度煽情、未授权真实人脸、敏感标识误用"),
            ("video", video_prompt, "夸张运镜、突然切镜、虚构奖牌证书、未授权肖像、主体漂移、手指畸变"),
        ]:
            latest = db.execute(
                "SELECT COALESCE(MAX(version_no),0) FROM shot_prompts WHERE scene_id=? AND prompt_type=?",
                (scene["id"], prompt_type),
            ).fetchone()[0]
            db.execute(
                """
                INSERT INTO shot_prompts (
                    id,scene_id,project_id,prompt_type,version_no,prompt,negative_prompt,references_json,
                    model_provider,model_name,created_by
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    gen_id("sp"), scene["id"], project_id, prompt_type, latest + 1, prompt_text, negative,
                    references_json, data.get(f"{prompt_type}_provider", "platform"), data.get(f"{prompt_type}_model", "default"),
                    request.current_user.get("uid", ""),
                ),
            )

        db.execute(
            """
            UPDATE project_scenes
            SET scene_goal=?,source_citations_json=?,shot_size=?,camera_movement=?,visual_subject=?,location=?,
                voiceover_text=?,subtitle_text=?,risk_notes=?,generation_mode=?,asset_refs_json=?,prompt=?,status='已结构化',prompt_version=prompt_version+1
            WHERE id=?
            """,
            (
                structure["scene_goal"], dumps(structure["source_citations"]), structure["shot_size"],
                structure["camera_movement"], structure["visual_subject"], structure["location"],
                structure["voiceover_text"], structure["subtitle_text"], structure["risk_notes"],
                structure["generation_mode"], dumps(structure["asset_refs"]), video_prompt, scene["id"],
            ),
        )
        structured.append({
            "scene_id": scene["id"],
            "scene_name": scene_name,
            **structure,
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
        })

    db.execute("UPDATE projects SET progress=MAX(progress, 50), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()
    db.close()
    log_action("generate_structured_storyboard", f"结构化项目 {project_id} 分镜 {len(structured)} 条")
    return jsonify({"success": True, "storyboard": structured, "facts_used": facts[:20], "assets_used": assets[:12]}), 201


@platform_workflow.route("/projects/<project_id>/readiness", methods=["GET"])
@login_required
def workflow_readiness(project_id):
    ensure_platform_schema()
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error
    counts = {
        "sources": db.execute("SELECT COUNT(*) FROM project_sources WHERE project_id=?", (project_id,)).fetchone()[0],
        "parsed_sources": db.execute("SELECT COUNT(*) FROM project_sources WHERE project_id=? AND parse_status='parsed'", (project_id,)).fetchone()[0],
        "assets": db.execute("SELECT COUNT(*) FROM visual_assets WHERE project_id=? OR (project_id='' AND org_id=?)", (project_id, project["org_id"])).fetchone()[0],
        "authorized_assets": db.execute("SELECT COUNT(*) FROM visual_assets WHERE (project_id=? OR (project_id='' AND org_id=?)) AND auth_status='authorized'", (project_id, project["org_id"])).fetchone()[0],
        "scripts": db.execute("SELECT COUNT(*) FROM script_versions WHERE project_id=?", (project_id,)).fetchone()[0],
        "structured_scenes": db.execute("SELECT COUNT(*) FROM project_scenes WHERE project_id=? AND scene_goal<>''", (project_id,)).fetchone()[0],
        "tasks": db.execute("SELECT COUNT(*) FROM generation_tasks WHERE project_id=?", (project_id,)).fetchone()[0],
        "review_open": db.execute("SELECT COUNT(*) FROM review_items WHERE project_id=? AND status='open'", (project_id,)).fetchone()[0],
    }
    blockers = []
    if counts["sources"] == 0:
        blockers.append("缺少项目来源资料")
    elif counts["parsed_sources"] < counts["sources"]:
        blockers.append("仍有资料未解析，脚本事实引用不完整")
    if counts["scripts"] == 0:
        blockers.append("缺少脚本版本")
    if counts["structured_scenes"] == 0:
        blockers.append("缺少结构化分镜")
    if counts["assets"] and counts["authorized_assets"] < counts["assets"]:
        blockers.append("存在未确认授权的视觉资产")
    if counts["review_open"] > 0:
        blockers.append("仍有未关闭的自检项")
    db.close()
    return jsonify({
        "project_id": project_id,
        "counts": counts,
        "can_generate": counts["scripts"] > 0 and counts["structured_scenes"] > 0,
        "can_export": len(blockers) == 0,
        "blockers": blockers,
    })
