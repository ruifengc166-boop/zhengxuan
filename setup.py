#!/usr/bin/env python3
"""政宣智作 - 首次部署初始化脚本

在 Railway 部署后运行一次，创建管理员账号和初始数据。
用法: python setup.py
"""
import sys
import os

# Add work directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "work"))

from database import get_db, init_db, hash_password, gen_id, now

def main():
    print("=" * 50)
    print("  政宣智作 · 初始化脚本")
    print("=" * 50)
    
    # 1. Initialize database schema
    print("\n[1/3] 初始化数据库结构...")
    init_db()
    print("  [OK] 数据库表已创建")
    
    # 2. Create default organization and admin user
    db = get_db()
    
    # Check if already initialized
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        print("  [SKIP]  已有用户数据，跳过初始化")
        db.close()
        print("\n系统已就绪!")
        return
    
    # Create default org
    print("[2/3] 创建默认组织和管理员...")
    org_id = "org-default"
    db.execute(
        "INSERT OR IGNORE INTO organizations (id, name, short_name, status) VALUES (?, ?, ?, ?)",
        (org_id, "默认组织", "默认", "active")
    )
    
    # Create admin user
    admin_phone = os.environ.get("ADMIN_PHONE", "18800000000")
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
    admin_name = os.environ.get("ADMIN_NAME", "系统管理员")
    
    admin_id = gen_id("u")
    db.execute(
        "INSERT INTO users (id, name, org_id, role, phone, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
        (admin_id, admin_name, org_id, "超级管理员", admin_phone, hash_password(admin_password))
    )
    print(f"  [OK] 管理员账号创建成功")
    print(f"     手机号: {admin_phone}")
    print(f"     密码:   {admin_password}")
    
    # 3. Create default templates
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
    print(f"  [OK] {len(templates)} 个默认模板已创建")
    
    db.commit()
    db.close()
    
    print("\n" + "=" * 50)
    print("  [DONE] 初始化完成！系统已就绪")
    print("=" * 50)
    print(f"\n  管理后台: /admin/")
    print(f"  账号:     {admin_phone}")
    print(f"  密码:     {admin_password}")
    print()

if __name__ == "__main__":
    main()
