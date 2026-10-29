# -*- coding: utf-8 -*-
"""Pre-flight conflict check and migration runner for Sewa Setu database."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as portal


def run_migration():
    print("[MIGRATION] Connecting to database...")
    try:
        conn = portal.get_db()
        cur = conn.cursor()
    except Exception as e:
        print("[MIGRATION] Error connecting to database:", e)
        return False

    # Check for conflicts
    print("[MIGRATION] Checking for existing active application conflicts...")
    cur.execute("""
        SELECT mobile, count(*)
        FROM applications
        WHERE status IN ('PENDING', 'APPROVED', 'DEEMED_APPROVED')
        GROUP BY mobile
        HAVING count(*) > 1
    """)
    conflicts = cur.fetchall()
    if conflicts:
        print(f"[MIGRATION] ERROR: Found {len(conflicts)} mobile numbers with duplicate active applications!")
        for m, cnt in conflicts[:5]:
            print(f"  - Mobile {m}: {cnt} active records")
        print("[MIGRATION] Resolve duplicate active records before applying partial unique index.")
        cur.close(); conn.close()
        return False

    print("[MIGRATION] No conflicts found. Applying idx_active_app_mobile index...")
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_active_app_mobile
        ON applications(mobile)
        WHERE status IN ('PENDING', 'APPROVED', 'DEEMED_APPROVED');
    """)
    conn.commit()
    print("[MIGRATION] Successfully created index idx_active_app_mobile.")
    cur.close(); conn.close()
    return True


if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
