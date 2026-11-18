"""Dated eligibility analytics. These snapshots are NEVER used to admit a student."""

from .core import *
from .engine import evaluate
from datetime import timezone
import time


def revision(db):
    return db.val(
        "SELECT COALESCE(max(id),0) FROM academic_audit_logs WHERE action NOT IN('Exported report','Signed in')"
    )


def refresh_snapshots(db, u=None):
    y = year_id(db)
    rev = revision(db)
    scope, params = student_scope(u or {"role": "System"})
    students = db.all(
        """SELECT s.id FROM students s LEFT JOIN eligibility_snapshots a ON a.student_id=s.id AND a.academic_year_id=%s WHERE s.deleted_at IS NULL AND (a.student_id IS NULL OR a.revision<>%s OR a.evaluated_at::date<>current_date) AND """
        + scope
        + " ORDER BY s.id",
        [y, rev] + params,
    )
    if not students:
        return 0
    original = db.read_cache
    db.read_cache = {}
    updated = 0
    try:
        for row in students:
            s = get_student(db, u or {"role": "System"}, row["id"])
            courses = db.all(
                "SELECT id FROM courses WHERE programme_id=%s AND regulation_id=%s AND approved AND deleted_at IS NULL ORDER BY semester_id",
                (s["programme_id"], s["regulation_id"]),
            )
            out = [evaluate(db, s, c["id"]) for c in courses]
            eligible = [r for r in out if r["status"] == "Eligible"]
            blocks = [
                {"course": r["title"], "reason": r["reason"]}
                for r in out
                if r["status"] in ["Blocked", "Pending approval"]
                and (
                    "successful completion" in r["reason"]
                    or "Conditional prerequisite" in r["reason"]
                )
            ]
            summary = (
                "Eligible to add " + str(len(eligible)) + " course(s)."
                if eligible
                else "No additional course passes all current academic, timetable and capacity checks."
            )
            db.c.execute(
                """INSERT INTO eligibility_snapshots(student_id,academic_year_id,revision,eligible_courses,floating_courses,prerequisite_blocks,summary) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(student_id,academic_year_id) DO UPDATE SET revision=EXCLUDED.revision,eligible_courses=EXCLUDED.eligible_courses,floating_courses=EXCLUDED.floating_courses,prerequisite_blocks=EXCLUDED.prerequisite_blocks,summary=EXCLUDED.summary,evaluated_at=now()""",
                (
                    s["id"],
                    y,
                    rev,
                    len(eligible),
                    sum(r["basket"] == "Floating" for r in eligible),
                    Jsonb(blocks),
                    summary,
                ),
            )
            updated += 1
            if len(db.read_cache) > 50000:
                db.read_cache.clear()
    finally:
        db.read_cache = original
    return updated


def full_refresh():
    with connect() as c:
        db = DB(c)
        db.run("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if not db.val("SELECT pg_try_advisory_xact_lock(740127)"):
            return 0
        return refresh_snapshots(db)


if __name__ == "__main__":
    start = time.monotonic()
    print(
        "Updated",
        full_refresh(),
        "student snapshots in",
        round(time.monotonic() - start, 2),
        "seconds.",
    )
