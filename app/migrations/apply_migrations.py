# -*- coding: utf-8 -*-
"""Pre-flight conflict check and migration runner for Sewa Setu database.

Ensures safe, consistent, and idempotent schema migrations:
- Never deletes or silently alters citizen records to force a migration to pass.
- Halts and reports actionable duplicate conflict data if active unique index preflight fails.
- Applies migrations in sequence:
    001_active_unique.sql (concurrency uniqueness on active applications)
    002_officer_blocks.sql (trusted block-level officer authorization table)
- Records applied migrations in schema_migrations table.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as portal

MIGRATIONS_DIR = Path(__file__).resolve().parent


def init_migrations_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(50) PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


def check_preflight_001(cur) -> bool:
    print("[MIGRATION 001] Preflight: checking for duplicate active applications...")
    cur.execute("""
        SELECT mobile, count(*), string_agg(application_no, ', ')
        FROM applications
        WHERE status IN ('PENDING', 'APPROVED', 'DEEMED_APPROVED')
        GROUP BY mobile
        HAVING count(*) > 1
    """)
    conflicts = cur.fetchall()
    if conflicts:
        print(f"[MIGRATION 001] PREFLIGHT FAILED: Found {len(conflicts)} mobile numbers with duplicate active applications!")
        print("[MIGRATION 001] Actionable conflict details (no citizen data was altered):")
        for m, cnt, app_nos in conflicts[:10]:
            print(f"  - Mobile: {m} | Active count: {cnt} | Applications: {app_nos}")
        print("[MIGRATION 001] Please resolve conflicting records administratively before applying migration.")
        return False
    print("[MIGRATION 001] Preflight passed: zero duplicate active applications found.")
    return True


def run_migrations():
    print("[MIGRATION] Connecting to database...")
    try:
        conn = portal.get_db()
        cur = conn.cursor()
    except Exception as e:
        print("[MIGRATION] Error connecting to database:", e)
        return False

    init_migrations_table(cur)
    conn.commit()

    # Query already applied migrations
    cur.execute("SELECT version FROM schema_migrations")
    applied = {r[0] for r in cur.fetchall()}

    # Migration 001
    if "001_active_unique" not in applied:
        print("[MIGRATION] Running migration 001_active_unique...")
        if not check_preflight_001(cur):
            cur.close(); conn.close()
            return False
        sql_001 = (MIGRATIONS_DIR / "001_active_unique.sql").read_text(encoding="utf-8")
        cur.execute(sql_001)
        cur.execute("INSERT INTO schema_migrations (version) VALUES ('001_active_unique')")
        conn.commit()
        print("[MIGRATION] Migration 001_active_unique applied successfully.")
    else:
        print("[MIGRATION] Migration 001_active_unique already applied.")

    # Migration 002
    if "002_officer_blocks" not in applied:
        print("[MIGRATION] Running migration 002_officer_blocks...")
        sql_002 = (MIGRATIONS_DIR / "002_officer_blocks.sql").read_text(encoding="utf-8")
        cur.execute(sql_002)
        cur.execute("INSERT INTO schema_migrations (version) VALUES ('002_officer_blocks')")
        conn.commit()
        print("[MIGRATION] Migration 002_officer_blocks applied successfully.")
    else:
        print("[MIGRATION] Migration 002_officer_blocks already applied.")

    cur.close(); conn.close()
    print("[MIGRATION] All database migrations are up to date.")
    return True


if __name__ == "__main__":
    success = run_migrations()
    sys.exit(0 if success else 1)
