import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from flask import Blueprint, jsonify, request, send_from_directory

from auth import login_required, is_admin_role
from database import get_db, gen_id, now, log_operation, UPLOAD_DIR

workflow = Blueprint("workflow", __name__, url_prefix="/api/workflow")

BASE_DIR = Path(__file__).parent
EXPORT_DIR = BASE_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


# ─── Permission Helpers ─────────────────────────────────────

def is_super_admin(user):
    return (user or {}).get("role") == "超级管理员"


def is_admin(user):
    return is_admin_role((user or {}).get("role"))


def log_action(action, detail=""):
    user = getattr(request, "current_user", {}) or {}
    try:
        log_operation(
            user_id=user.get("uid", ""),
            user_name=user.get("name", ""),
            action=action,
            detail=detail,
            ip=request.headers.get("X-Forwarded-For", request.remote_addr or "")
        )
    except Exception as exc:
        print(f"[WARN] workflow log failed: {exc}", flush=True)


def get_project_for_user(db, project_id, user):
    project = db.execute(
        "SELECT p.*, o.name as org_name FROM projects p LEFT JOIN organizations o ON p.org_id=o.id WHERE p.id=?",
        (project_id,)
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


def get_source_for_user(db, source_id, user):
    source = db.execute(
        """
        SELECT s.*, f.filename, f.original_name, f.file_type, f.file_size, f.mime_type, f.uploader_id
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        WHERE s.id=?
        """,
        (source_id,)
    ).fetchone()
    if not source:
        return None, (jsonify({"error": "资料不存在"}), 404)

    project, error = get_project_for_user(db, source["project_id"], user)
    if error:
        return None, error

    return source, None


def get_scene_for_user(db, scene_id, user):
    scene = db.execute("SELECT * FROM project_scenes WHERE id=?", (scene_id,)).fetchone()
    if not scene:
        return None, None, (jsonify({"error": "镜头不存在"}), 404)
    project, error = get_project_for_user(db, scene["project_id"], user)
    if error:
        return None, None, error
    return scene, project, None


def safe_json(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


# ─── Lightweight Source Parsing ─────────────────────────────

def clean_text(text):
    text = re.sub(r"\r\n?", "\n", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_xml_text_from_docx(file_path):
    with zipfile.ZipFile(file_path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    texts = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            texts.append(node.text)
    return clean_text("".join(texts))


def extract_xml_text_from_pptx(file_path):
    texts = []
    with zipfile.ZipFile(file_path) as zf:
        for name in sorted(zf.namelist()):
            if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                root = ET.fromstring(zf.read(name))
                for node in root.iter():
                    if node.tag.endswith("}t") and node.text:
                        texts.append(node.text)
    return clean_text("\n".join(texts))


def extract_xml_text_from_xlsx(file_path):
    texts = []
    with zipfile.ZipFile(file_path) as zf:
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    texts.append(node.text)
    return clean_text("\n".join(texts))


def extract_file_text(record):
    file_type = (record["file_type"] or "").lower()
    filename = record["filename"] or ""
    file_path = UPLOAD_DIR / filename

    if not filename or not file_path.exists():
        return {
            "status": "failed",
            "text": "",
            "message": "文件不存在，可能尚未迁移到对象存储或本地上传目录丢失。",
        }

    try:
        if file_type == ".txt":
            return {"status": "parsed", "text": clean_text(file_path.read_text(encoding="utf-8", errors="ignore")), "message": "TXT 已解析"}
        if file_type == ".docx":
            return {"status": "parsed", "text": extract_xml_text_from_docx(file_path), "message": "DOCX 已解析"}
        if file_type == ".pptx":
            return {"status": "parsed", "text": extract_xml_text_from_pptx(file_path), "message": "PPTX 已解析"}
        if file_type == ".xlsx":
            return {"status": "parsed", "text": extract_xml_text_from_xlsx(file_path), "message": "XLSX 共享文本已解析"}
        if file_type == ".pdf":
            return {
                "status": "needs_external_parser",
                "text": "",
                "message": "PDF 需要接入 OCR / PDF 解析服务。当前已记录资料，等待后续解析。",
            }
        if file_type in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return {
                "status": "needs_ocr",
                "text": "",
                "message": "图片资料需要 OCR。当前已记录资料，等待后续解析。",
            }
        if file_type in {".mp4", ".mov", ".avi", ".mp3", ".wav"}:
            return {
                "status": "needs_media_transcription",
                "text": "",
                "message": "音视频资料需要语音转写 / 画面抽帧。当前已记录资料，等待后续解析。",
            }
        return {"status": "unsupported", "text": "", "message": f"暂不支持解析 {file_type}"}
    except Exception as exc:
        return {"status": "failed", "text": "", "message": f"解析失败：{exc}"}


def extract_facts(text, source_title):
    lines = [line.strip() for line in clean_text(text).split("\n") if line.strip()]
    facts = []
    for line in lines[:12]:
        if len(line) < 8:
            continue
        facts.append({
            "source": source_title,
            "claim": line[:240],
            "confidence": "source_text",
        })
    return facts


@workflow.route("/projects/<project_id>/sources", methods=["GET"])
@login_required
def list_project_sources(project_id):
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    rows = [dict(r) for r in db.execute(
        """
        SELECT s.*, f.original_name, f.file_type, f.file_size, f.mime_type
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        WHERE s.project_id=?
        ORDER BY s.created_at DESC
        """,
        (project_id,)
    ).fetchall()]
    db.close()
    return jsonify({"sources": rows})


@workflow.route("/sources/<source_id>/parse", methods=["POST"])
@login_required
def parse_source(source_id):
    db = get_db()
    source, error = get_source_for_user(db, source_id, request.current_user)
    if error:
        db.close()
        return error

    parsed = extract_file_text(source)
    facts = extract_facts(parsed["text"], source["title"] or source["original_name"] or source_id)

    extraction_id = gen_id("ext")
    db.execute(
        """
        INSERT INTO source_extractions (id,source_id,content_type,extracted_text,facts_json,risk_json)
        VALUES (?,?,?,?,?,?)
        """,
        (
            extraction_id,
            source_id,
            "text",
            parsed["text"],
            json.dumps({"facts": facts}, ensure_ascii=False),
            json.dumps({"parse_status": parsed["status"], "message": parsed["message"]}, ensure_ascii=False),
        )
    )
    db.execute("UPDATE project_sources SET parse_status=? WHERE id=?", (parsed["status"], source_id))
    db.commit()
    db.close()

    log_action("parse_source", f"解析资料 {source_id}: {parsed['status']}")
    return jsonify({
        "success": True,
        "source_id": source_id,
        "extraction_id": extraction_id,
        "parse_status": parsed["status"],
        "message": parsed["message"],
        "facts": facts,
        "text_preview": parsed["text"][:600],
    })


@workflow.route("/projects/<project_id>/sources/parse-all", methods=["POST"])
@login_required
def parse_all_sources(project_id):
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    sources = db.execute(
        """
        SELECT s.*, f.filename, f.original_name, f.file_type, f.file_size, f.mime_type, f.uploader_id
        FROM project_sources s
        LEFT JOIN uploaded_files f ON s.file_id=f.id
        WHERE s.project_id=?
        """,
        (project_id,)
    ).fetchall()

    results = []
    for source in sources:
        parsed = extract_file_text(source)
        facts = extract_facts(parsed["text"], source["title"] or source["original_name"] or source["id"])
        extraction_id = gen_id("ext")
        db.execute(
            """
            INSERT INTO source_extractions (id,source_id,content_type,extracted_text,facts_json,risk_json)
            VALUES (?,?,?,?,?,?)
            """,
            (
                extraction_id,
                source["id"],
                "text",
                parsed["text"],
                json.dumps({"facts": facts}, ensure_ascii=False),
                json.dumps({"parse_status": parsed["status"], "message": parsed["message"]}, ensure_ascii=False),
            )
        )
        db.execute("UPDATE project_sources SET parse_status=? WHERE id=?", (parsed["status"], source["id"]))
        results.append({"source_id": source["id"], "status": parsed["status"], "message": parsed["message"], "facts": len(facts)})

    db.commit()
    db.close()
    log_action("parse_all_sources", f"批量解析项目 {project_id} 资料 {len(results)} 份")
    return jsonify({"success": True, "results": results})


# ─── Script Workflow ────────────────────────────────────────

def build_script_from_sources(project, facts):
    title = project["name"] or "宣传片"
    if not facts:
        return f"""《{title}》脚本初稿

一、开场
以单位真实场景和服务对象需求切入，说明本片主题与公共价值。

二、背景
补充政策依据、项目背景和工作目标。此处需要上传权威资料后再完善引用。

三、做法
呈现工作流程、服务举措、典型场景和一线人员行动。

四、成效
展示阶段性成果、服务改善、群众反馈或项目数据。涉及数据必须补充来源。

五、结尾
回到公共服务价值，形成温和、可信、不过度煽情的收束。
"""

    selected = facts[:8]
    lines = [f"《{title}》脚本初稿", ""]
    lines.append("一、开场")
    lines.append(f"围绕“{title}”，以真实工作场景切入，说明本片关注的公共服务主题。[S1]")
    lines.append("")
    lines.append("二、背景与依据")
    for idx, fact in enumerate(selected[:2], start=1):
        lines.append(f"根据资料显示，{fact['claim']} [S{idx}]")
    lines.append("")
    lines.append("三、工作做法")
    for idx, fact in enumerate(selected[2:5], start=3):
        lines.append(f"在具体推进过程中，重点呈现：{fact['claim']} [S{idx}]")
    lines.append("")
    lines.append("四、服务成效")
    for idx, fact in enumerate(selected[5:7], start=6):
        lines.append(f"成效表达应保持克制，并以来源材料为依据：{fact['claim']} [S{idx}]")
    lines.append("")
    lines.append("五、结尾")
    lines.append("以面向公众的服务承诺收束，保留 AI 生成内容标识和发布前人工复核流程。")
    return "\n".join(lines)


def collect_project_facts(db, project_id):
    rows = db.execute(
        """
        SELECT s.id as source_id, s.title, e.facts_json
        FROM project_sources s
        LEFT JOIN source_extractions e ON e.source_id=s.id
        WHERE s.project_id=?
        ORDER BY s.created_at ASC, e.created_at DESC
        """,
        (project_id,)
    ).fetchall()

    facts = []
    seen_sources = set()
    citation_index = 1
    for row in rows:
        if row["source_id"] in seen_sources:
            continue
        seen_sources.add(row["source_id"])
        data = safe_json(row["facts_json"], {})
        for fact in data.get("facts", [])[:3]:
            facts.append({
                "citation": f"S{citation_index}",
                "source_id": row["source_id"],
                "title": row["title"],
                "claim": fact.get("claim", ""),
            })
            citation_index += 1
    return facts


@workflow.route("/projects/<project_id>/scripts", methods=["GET"])
@login_required
def list_scripts(project_id):
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    rows = [dict(r) for r in db.execute(
        "SELECT * FROM script_versions WHERE project_id=? ORDER BY version_no DESC",
        (project_id,)
    ).fetchall()]
    db.close()
    return jsonify({"scripts": rows})


@workflow.route("/projects/<project_id>/scripts/generate", methods=["POST"])
@login_required
def generate_script(project_id):
    data = request.get_json() or {}
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    facts = collect_project_facts(db, project_id)
    content = data.get("content") or build_script_from_sources(project, facts)
    latest = db.execute("SELECT COALESCE(MAX(version_no),0) FROM script_versions WHERE project_id=?", (project_id,)).fetchone()[0]
    version_no = latest + 1
    source_coverage = min(1, len(facts) / 6) if facts else 0
    risk_status = "needs_sources" if source_coverage < 0.5 else "draft_checked"
    script_id = gen_id("sv")

    db.execute(
        """
        INSERT INTO script_versions (id,project_id,version_no,title,content,source_coverage,risk_status,created_by)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (script_id, project_id, version_no, data.get("title", f"{project['name']} · 脚本 V{version_no}"), content, source_coverage, risk_status, request.current_user["uid"])
    )
    db.execute("UPDATE projects SET progress=MAX(progress, 20), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()
    db.close()

    log_action("generate_script", f"生成项目 {project_id} 脚本 V{version_no}")
    return jsonify({
        "success": True,
        "script": {
            "id": script_id,
            "project_id": project_id,
            "version_no": version_no,
            "content": content,
            "source_coverage": source_coverage,
            "risk_status": risk_status,
            "citations": facts,
        }
    }), 201


@workflow.route("/scripts/<script_id>/lock", methods=["POST"])
@login_required
def lock_script(script_id):
    db = get_db()
    script = db.execute("SELECT * FROM script_versions WHERE id=?", (script_id,)).fetchone()
    if not script:
        db.close()
        return jsonify({"error": "脚本不存在"}), 404

    project, error = get_project_for_user(db, script["project_id"], request.current_user)
    if error:
        db.close()
        return error

    db.execute("UPDATE script_versions SET locked=0 WHERE project_id=?", (script["project_id"],))
    db.execute("UPDATE script_versions SET locked=1 WHERE id=?", (script_id,))
    db.execute("UPDATE projects SET progress=MAX(progress, 35), updated_at=? WHERE id=?", (now(), script["project_id"]))
    db.commit()
    db.close()

    log_action("lock_script", f"锁定脚本 {script_id}")
    return jsonify({"success": True})


# ─── Storyboard / Prompt Workflow ───────────────────────────

def split_script_into_beats(content, count):
    text = clean_text(content)
    if not text:
        return ["单位外景与主题引入", "服务场景展示", "工作人员行动", "群众获得感", "重点成果", "品牌片尾"][:count]

    lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 8]
    if not lines:
        lines = [text]

    beats = []
    for idx in range(count):
        beats.append(lines[idx % len(lines)][:140])
    return beats


def build_image_prompt(project, scene_name, beat, style="纪实、克制、可信、公共服务宣传片"):
    return f"{style}；主题：{project['name']}；镜头：{scene_name}；画面内容：{beat}；要求真实机构宣传片质感，避免夸张特效，人物自然，光线干净，构图稳重，适合行政事业单位对外发布。"


def build_video_prompt(scene_name, beat):
    return f"镜头《{scene_name}》：围绕“{beat}”进行 6-8 秒视频生成。运镜平稳，节奏舒缓，人物动作自然，避免炫技转场，保留真实宣传片可信感。"


@workflow.route("/projects/<project_id>/storyboard/generate", methods=["POST"])
@login_required
def generate_storyboard(project_id):
    data = request.get_json() or {}
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    script = db.execute(
        "SELECT * FROM script_versions WHERE project_id=? ORDER BY locked DESC, version_no DESC LIMIT 1",
        (project_id,)
    ).fetchone()

    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()
    target_count = int(data.get("scene_count") or len(scenes) or 6)

    if not scenes:
        for i in range(target_count):
            db.execute(
                "INSERT INTO project_scenes (id,project_id,name,scene_order,status,duration) VALUES (?,?,?,?,?,?)",
                (gen_id("s"), project_id, f"镜头 {i + 1:02d}", i + 1, "待生成", data.get("duration", "6s"))
            )
        scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()

    beats = split_script_into_beats(script["content"] if script else "", len(scenes))
    prompts = []
    for idx, scene in enumerate(scenes):
        beat = beats[idx]
        scene_name = scene["name"] or f"镜头 {idx + 1:02d}"
        image_prompt = build_image_prompt(project, scene_name, beat, data.get("style", "纪实、克制、可信、公共服务宣传片"))
        video_prompt = build_video_prompt(scene_name, beat)

        image_prompt_id = gen_id("sp")
        video_prompt_id = gen_id("sp")
        db.execute(
            """
            INSERT INTO shot_prompts (id,scene_id,project_id,prompt_type,version_no,prompt,negative_prompt,model_provider,model_name,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (image_prompt_id, scene["id"], project_id, "image", 1, image_prompt, "夸张特效、虚假荣誉、过度煽情、未授权真实人脸、敏感标识误用", data.get("image_provider", "platform"), data.get("image_model", "default"), request.current_user["uid"])
        )
        db.execute(
            """
            INSERT INTO shot_prompts (id,scene_id,project_id,prompt_type,version_no,prompt,negative_prompt,model_provider,model_name,created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (video_prompt_id, scene["id"], project_id, "video", 1, video_prompt, "夸张运镜、赛博朋克、商业广告感、虚构奖牌证书、未授权肖像", data.get("video_provider", "platform"), data.get("video_model", "default"), request.current_user["uid"])
        )
        db.execute("UPDATE project_scenes SET prompt=?, status='已生成Prompt', prompt_version=prompt_version+1 WHERE id=?", (video_prompt, scene["id"]))
        prompts.append({
            "scene_id": scene["id"],
            "scene_name": scene_name,
            "beat": beat,
            "image_prompt_id": image_prompt_id,
            "video_prompt_id": video_prompt_id,
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
        })

    db.execute("UPDATE projects SET progress=MAX(progress, 45), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()
    db.close()

    log_action("generate_storyboard", f"生成项目 {project_id} 分镜 Prompt {len(prompts)} 条")
    return jsonify({"success": True, "prompts": prompts}), 201


@workflow.route("/scenes/<scene_id>/prompts", methods=["GET"])
@login_required
def list_scene_prompts(scene_id):
    db = get_db()
    scene, project, error = get_scene_for_user(db, scene_id, request.current_user)
    if error:
        db.close()
        return error

    prompts = [dict(r) for r in db.execute(
        "SELECT * FROM shot_prompts WHERE scene_id=? ORDER BY created_at DESC",
        (scene_id,)
    ).fetchall()]
    db.close()
    return jsonify({"scene": dict(scene), "prompts": prompts})


@workflow.route("/prompts/<prompt_id>/lock", methods=["POST"])
@login_required
def lock_prompt(prompt_id):
    db = get_db()
    prompt = db.execute("SELECT * FROM shot_prompts WHERE id=?", (prompt_id,)).fetchone()
    if not prompt:
        db.close()
        return jsonify({"error": "Prompt 不存在"}), 404

    project, error = get_project_for_user(db, prompt["project_id"], request.current_user)
    if error:
        db.close()
        return error

    db.execute(
        "UPDATE shot_prompts SET locked=0 WHERE scene_id=? AND prompt_type=?",
        (prompt["scene_id"], prompt["prompt_type"])
    )
    db.execute("UPDATE shot_prompts SET locked=1 WHERE id=?", (prompt_id,))
    db.commit()
    db.close()

    log_action("lock_prompt", f"锁定 Prompt {prompt_id}")
    return jsonify({"success": True})


# ─── Review / Evidence Package ──────────────────────────────

def add_review_item(db, project_id, org_id, item_type, severity, title, evidence, owner_user_id=""):
    rid = gen_id("rv")
    db.execute(
        """
        INSERT INTO review_items (id,project_id,org_id,item_type,severity,title,evidence,status,owner_user_id,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (rid, project_id, org_id, item_type, severity, title, evidence, "open", owner_user_id, now())
    )
    return rid


@workflow.route("/projects/<project_id>/review/run", methods=["POST"])
@login_required
def run_project_review(project_id):
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    db.execute("UPDATE review_items SET status='superseded', updated_at=? WHERE project_id=? AND status='open'", (now(), project_id))

    sources = db.execute("SELECT * FROM project_sources WHERE project_id=?", (project_id,)).fetchall()
    parsed_sources = [s for s in sources if s["parse_status"] == "parsed"]
    scripts = db.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY locked DESC, version_no DESC", (project_id,)).fetchall()
    scenes = db.execute("SELECT * FROM project_scenes WHERE project_id=?", (project_id,)).fetchall()
    prompts = db.execute("SELECT * FROM shot_prompts WHERE project_id=?", (project_id,)).fetchall()
    tasks = db.execute("SELECT * FROM generation_tasks WHERE project_id=?", (project_id,)).fetchall()
    files = db.execute("SELECT * FROM uploaded_files WHERE project_id=?", (project_id,)).fetchall()

    created = []
    if not sources:
        created.append(add_review_item(db, project_id, project["org_id"], "source", "R2", "缺少权威资料", "项目尚未上传任何来源资料，不能进入正式发布。"))
    elif len(parsed_sources) < len(sources):
        created.append(add_review_item(db, project_id, project["org_id"], "source", "R2", "部分资料未解析", f"共 {len(sources)} 份资料，已解析 {len(parsed_sources)} 份。未解析资料不能用于事实引用。"))

    if not scripts:
        created.append(add_review_item(db, project_id, project["org_id"], "script", "R2", "缺少脚本版本", "尚未生成或保存脚本版本。"))
    else:
        latest_script = scripts[0]
        if "[S" not in latest_script["content"]:
            created.append(add_review_item(db, project_id, project["org_id"], "script", "R2", "脚本缺少来源引用", "最新脚本未发现 [S1] 等来源引用标记。"))
        if latest_script["locked"] != 1:
            created.append(add_review_item(db, project_id, project["org_id"], "script", "R1", "脚本尚未锁定", "建议由项目负责人确认脚本定稿后再进入批量视频生成。"))

    if not scenes:
        created.append(add_review_item(db, project_id, project["org_id"], "storyboard", "R2", "缺少分镜", "项目尚未生成镜头结构。"))
    elif len(prompts) < len(scenes):
        created.append(add_review_item(db, project_id, project["org_id"], "storyboard", "R1", "部分镜头缺少 Prompt", f"镜头数 {len(scenes)}，Prompt 记录 {len(prompts)}。"))

    unchecked_files = [f for f in files if (f["auth_status"] or "unchecked") == "unchecked"]
    if unchecked_files:
        created.append(add_review_item(db, project_id, project["org_id"], "authorization", "R2", "素材授权未确认", f"仍有 {len(unchecked_files)} 个素材授权状态为 unchecked。"))

    failed_tasks = [t for t in tasks if t["status"] in {"failed", "error"}]
    if failed_tasks:
        created.append(add_review_item(db, project_id, project["org_id"], "generation", "R1", "存在失败的生成任务", f"失败任务数量：{len(failed_tasks)}。"))

    if not created:
        add_review_item(db, project_id, project["org_id"], "summary", "OK", "自动自检通过", "未发现阻断发布的问题，但仍需人工确认内容事实、版权授权和 AI 标识。")

    db.execute("UPDATE projects SET progress=MAX(progress, 70), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()

    items = [dict(r) for r in db.execute(
        "SELECT * FROM review_items WHERE project_id=? ORDER BY created_at DESC LIMIT 30",
        (project_id,)
    ).fetchall()]
    db.close()

    log_action("run_project_review", f"项目 {project_id} 自检完成，问题 {len(created)} 项")
    blocking = [i for i in items if i["status"] == "open" and i["severity"] in {"R2", "R3"}]
    return jsonify({
        "success": True,
        "summary": {
            "open_items": len([i for i in items if i["status"] == "open"]),
            "blocking_items": len(blocking),
            "can_export": len(blocking) == 0,
        },
        "items": items,
    })


@workflow.route("/review-items/<item_id>/resolve", methods=["POST"])
@login_required
def resolve_review_item(item_id):
    data = request.get_json() or {}
    db = get_db()
    item = db.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        db.close()
        return jsonify({"error": "自检项不存在"}), 404

    project, error = get_project_for_user(db, item["project_id"], request.current_user)
    if error:
        db.close()
        return error

    db.execute(
        "UPDATE review_items SET status='resolved', evidence=?, updated_at=? WHERE id=?",
        (data.get("evidence", item["evidence"]), now(), item_id)
    )
    db.commit()
    db.close()

    log_action("resolve_review_item", f"关闭自检项 {item_id}")
    return jsonify({"success": True})


def write_json_to_zip(zf, name, data):
    zf.writestr(name, json.dumps(data, ensure_ascii=False, indent=2))


@workflow.route("/projects/<project_id>/exports/evidence-package", methods=["POST"])
@login_required
def create_evidence_package(project_id):
    body = request.get_json(silent=True) or {}
    db = get_db()
    project, error = get_project_for_user(db, project_id, request.current_user)
    if error:
        db.close()
        return error

    sources = [dict(r) for r in db.execute("SELECT * FROM project_sources WHERE project_id=?", (project_id,)).fetchall()]
    scripts = [dict(r) for r in db.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version_no DESC", (project_id,)).fetchall()]
    scenes = [dict(r) for r in db.execute("SELECT * FROM project_scenes WHERE project_id=? ORDER BY scene_order", (project_id,)).fetchall()]
    prompts = [dict(r) for r in db.execute("SELECT * FROM shot_prompts WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()]
    tasks = [dict(r) for r in db.execute("SELECT * FROM generation_tasks WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()]
    review_items = [dict(r) for r in db.execute("SELECT * FROM review_items WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall()]
    files = [dict(r) for r in db.execute("SELECT id,original_name,file_type,file_size,data_level,auth_status,created_at FROM uploaded_files WHERE project_id=?", (project_id,)).fetchall()]

    export_id = gen_id("exp")
    package_name = f"{export_id}.zip"
    package_path = EXPORT_DIR / package_name

    manifest = {
        "export_id": export_id,
        "project": dict(project),
        "created_at": now(),
        "created_by": request.current_user.get("uid"),
        "ai_label_required": True,
        "package_items": [
            "manifest.json",
            "sources.json",
            "scripts.json",
            "storyboard.json",
            "prompts.json",
            "generation_tasks.json",
            "review_items.json",
            "uploaded_files.json",
            "README.txt",
        ],
    }

    readme = f"""政宣智作 · 发布证据包

项目：{project['name']}
导出时间：{now()}
导出人：{request.current_user.get('name')}

本包用于行政事业单位 AI 宣传片发布前留痕，包含：
1. 项目基本信息
2. 来源资料清单
3. 脚本版本
4. 分镜与 Prompt 记录
5. AI 生成任务记录
6. 自检问题与处理记录
7. 上传素材授权状态

注意：本包不等同于正式审批意见。正式发布前仍需人工确认事实、版权、肖像、涉密、地图国旗国徽等风险。
"""

    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as zf:
        write_json_to_zip(zf, "manifest.json", manifest)
        write_json_to_zip(zf, "sources.json", sources)
        write_json_to_zip(zf, "scripts.json", scripts)
        write_json_to_zip(zf, "storyboard.json", scenes)
        write_json_to_zip(zf, "prompts.json", prompts)
        write_json_to_zip(zf, "generation_tasks.json", tasks)
        write_json_to_zip(zf, "review_items.json", review_items)
        write_json_to_zip(zf, "uploaded_files.json", files)
        zf.writestr("README.txt", readme)

    package_url = f"/api/workflow/exports/{export_id}/download"
    db.execute(
        """
        INSERT INTO exports (id,project_id,org_id,version_label,status,package_url,ai_label_enabled,evidence_package_json,confirmed_by,confirmed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            export_id,
            project_id,
            project["org_id"],
            body.get("version_label", "证据包"),
            "generated",
            package_url,
            1,
            json.dumps(manifest, ensure_ascii=False),
            request.current_user.get("uid"),
            now(),
        )
    )
    db.execute("UPDATE projects SET progress=MAX(progress, 90), updated_at=? WHERE id=?", (now(), project_id))
    db.commit()
    db.close()

    log_action("create_evidence_package", f"导出项目 {project_id} 证据包 {export_id}")
    return jsonify({"success": True, "export_id": export_id, "package_url": package_url, "manifest": manifest}), 201


@workflow.route("/exports/<export_id>/download", methods=["GET"])
@login_required
def download_evidence_package(export_id):
    db = get_db()
    export = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
    if not export:
        db.close()
        return jsonify({"error": "导出包不存在"}), 404

    project, error = get_project_for_user(db, export["project_id"], request.current_user)
    if error:
        db.close()
        return error

    db.close()
    package_name = f"{export_id}.zip"
    if not (EXPORT_DIR / package_name).exists():
        return jsonify({"error": "导出包文件不存在，请重新生成"}), 404

    return send_from_directory(EXPORT_DIR, package_name, as_attachment=True, download_name=f"{project['name']}-证据包-{export_id}.zip")
