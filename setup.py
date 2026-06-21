#!/usr/bin/env python3
"""政宣智作 - 首次部署初始化脚本

用法:
  python setup.py

可选环境变量:
  ADMIN_PHONE       管理员手机号，默认 18800000000
  ADMIN_PASSWORD    管理员初始密码；不设置时自动生成强密码
  ADMIN_NAME        管理员姓名，默认 系统管理员
  ORG_NAME          默认组织名称
  ORG_SHORT_NAME    默认组织简称
"""
import os
import secrets
import string
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "work"))

from database import get_db, init_db, hash_password, gen_id


def generate_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main():
    print("=" * 50)
    print("  政宣智作 · 初始化脚本")
    print("=" * 50)

    print("\n[1/3] 初始化数据库结构与迁移...")
    init_db()
    print("  [OK] 数据库表已创建 / 迁移已检查")

    db = get_db()

    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        print("  [SKIP] 已有用户数据，跳过初始化")
        db.close()
        print("\n系统已就绪!")
        return

    print("[2/3] 创建默认组织和管理员...")
    org_id = "org-default"
    org_name = os.environ.get("ORG_NAME", "默认组织")
    org_short_name = os.environ.get("ORG_SHORT_NAME", "默认")

    db.execute(
        "INSERT OR IGNORE INTO organizations (id, name, short_name, status) VALUES (?, ?, ?, ?)",
        (org_id, org_name, org_short_name, "active")
    )

    admin_phone = os.environ.get("ADMIN_PHONE", "18800000000")
    admin_password = os.environ.get("ADMIN_PASSWORD") or generate_password()
    admin_name = os.environ.get("ADMIN_NAME", "系统管理员")

    admin_id = gen_id("u")
    db.execute(
        "INSERT INTO users (id, name, org_id, role, phone, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
        (admin_id, admin_name, org_id, "超级管理员", admin_phone, hash_password(admin_password))
    )
    print("  [OK] 管理员账号创建成功")
    print(f"     手机号: {admin_phone}")
    print(f"     初始密码: {admin_password}")
    if not os.environ.get("ADMIN_PASSWORD"):
        print("     [IMPORTANT] 系统自动生成了强密码，请立即保存并首次登录后修改。")

    print("[3/3] 创建默认模板...")
    templates = [
        ("政宣系列·标准版", 6, "60秒", "适用政策解读、年度工作汇报", "政宣", "published"),
        ("科普教育·简明版", 4, "45秒", "适用健康教育、科普宣传", "科普", "published"),
        ("专题纪实·深度版", 10, "120秒", "适用人物专题、项目纪实", "专题", "published"),
    ]
    for t in templates:
        db.execute(
            "INSERT INTO templates (id, name, scenes, duration, description, category, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gen_id("t"), t[0], t[1], t[2], t[3], t[4], t[5])
        )

    db.commit()
    db.close()

    print("\n" + "=" * 50)
    print("  [DONE] 初始化完成！系统已就绪")
    print("=" * 50)
    print("\n  管理后台: /admin/")
    print(f"  账号:     {admin_phone}")
    print("  请妥善保存初始密码，并在正式部署后设置 JWT_SECRET、CORS_ORIGINS。")
    print()


if __name__ == "__main__":
    main()
