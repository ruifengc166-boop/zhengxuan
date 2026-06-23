"""Local Python startup hook for the lightweight Flask prototype.

When running `python app.py` from the `work` directory, Python imports this
module before importing the application. This guarantees that the SQLite base
schema exists before workflow route modules run their idempotent schema upgrade.

It specifically fixes the local edge case where `data/zhengxuan.db` exists but
was created empty during a failed first startup.
"""

try:
    from database import init_db, seed_data

    init_db()
    seed_data()
except Exception as exc:  # Keep startup diagnostics visible without hiding app errors.
    print(f"[DB] startup bootstrap skipped or failed: {exc}", flush=True)
