-- Migration 001: Prevent concurrent duplicate active applications
-- Creates a partial unique index on applications(mobile) for active statuses: PENDING, APPROVED, DEEMED_APPROVED

-- Step 1: Pre-flight check (fail early if duplicate active records exist)
-- SELECT mobile, count(*) FROM applications WHERE status IN ('PENDING', 'APPROVED', 'DEEMED_APPROVED') GROUP BY mobile HAVING count(*) > 1;

-- Step 2: Create the partial unique index
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_app_mobile
ON applications(mobile)
WHERE status IN ('PENDING', 'APPROVED', 'DEEMED_APPROVED');
