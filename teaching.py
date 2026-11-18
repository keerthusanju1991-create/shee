"""Roster-scoped teaching actions; no unrelated transcript data is returned to instructors."""

from fastapi import APIRouter, Depends
from pydantic import Field
from typing import Literal
from .core import *

router = APIRouter()


def section_scope(db, u, zid):
    sec = db.one(
        "SELECT z.*,o.department_id,o.academic_year_id FROM course_sections z JOIN course_offerings o ON o.id=z.offering_id WHERE z.id=%s",
        (zid,),
    )
    if not sec:
        fail("Teaching section not found.", 404)
    if u["role"] == "Student":
        fail("Class rosters are restricted to authorised teaching staff.", 403)
    if u["role"] == "Faculty":
        if sec["faculty_id"] != u["faculty_id"]:
            fail("This section is not assigned to you.", 403)
    else:
        check_dept(u, sec["department_id"])
    return sec


@router.get("/sections/{zid}/roster")
def roster(
    zid: int,
    q: str = "",
    page: int = 1,
    size: int = 20,
    db=Depends(get_db),
    u=Depends(user),
):
    section_scope(db, u, zid)
    sc, p = student_scope(u)
    return db.page(
        """SELECT s.id,s.name,s.register_number,c.id AS course_id,c.code,r.code AS regulation,e.basket,e.attempt,a.attended,a.conducted,a.status AS attendance_status FROM course_enrolments e JOIN students s ON s.id=e.student_id JOIN courses c ON c.id=e.course_id JOIN regulations r ON r.id=c.regulation_id LEFT JOIN attendance_records a ON a.student_id=s.id AND a.course_id=c.id AND a.academic_year_id=e.academic_year_id AND a.attempt=e.attempt WHERE e.section_id=%s AND e.status='Registered' AND (s.name ILIKE %s OR s.register_number ILIKE %s) AND """
        + sc
        + " ORDER BY s.register_number",
        [zid, "%" + q + "%", "%" + q + "%"] + p,
        page,
        size,
    )


class Attendance(Input):
    student_id: int
    section_id: int
    attended: int = Field(ge=0)
    conducted: int = Field(gt=0)
    status: Literal["Eligible", "Shortage", "Debarred", "Approved exception", ""] = ""
    repeat_approved: bool = False
    confirm: bool


@router.post("/attendance/record")
def record(b: Attendance, db=Depends(get_db), u=Depends(user)):
    from .imports import apply_row

    permit(u, "attendance")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    section_scope(db, u, b.section_id)
    s = get_student(db, u, b.student_id)
    c = db.one(
        "SELECT c.code,c.id,e.attempt FROM course_enrolments e JOIN courses c ON c.id=e.course_id WHERE e.student_id=%s AND e.section_id=%s AND e.status='Registered'",
        (b.student_id, b.section_id),
    )
    if not c:
        fail("No active registration exists for this student and section.", 409)
    if b.repeat_approved and u["role"] not in [
        "Examination Cell",
        "Super Administrator",
    ]:
        fail("Repeat approval is reserved for the Examination Cell.", 403)
    apply_row(
        db,
        u,
        "attendance",
        {
            "register_number": s["register_number"],
            "course_code": c["code"],
            "attempt": str(c["attempt"]),
            "attended": str(b.attended),
            "conducted": str(b.conducted),
            "status": b.status,
            "repeat_approved": str(b.repeat_approved),
        },
        True,
    )
    db.audit(
        u,
        "Recorded course attendance",
        "students",
        s["id"],
        {
            "course_id": c["id"],
            "attended": b.attended,
            "conducted": b.conducted,
            "status": b.status or "Regulation threshold",
        },
    )
    return {"status": "Attendance recorded"}


class Capacity(Input):
    capacity: int = Field(gt=0, le=2000)
    reserved_seats: int = Field(ge=0)
    confirm: bool


@router.put("/sections/{zid}/capacity")
def capacity(zid: int, b: Capacity, db=Depends(get_db), u=Depends(user)):
    from .engine import staff_check, promote

    permit(u, "publish")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    s = section_scope(db, u, zid)
    o = db.one("SELECT * FROM course_offerings WHERE id=%s", (s["offering_id"],))
    used = db.val(
        "SELECT count(*) FROM course_enrolments WHERE section_id=%s AND status='Registered'",
        (zid,),
    )
    if (
        b.capacity < used
        or b.capacity < s["minimum_seats"]
        or b.reserved_seats > b.capacity
    ):
        fail(
            "Capacity cannot be below enrolled strength or minimum seats, and reservations cannot exceed total capacity.",
            409,
        )
    proposed = {**s, "maximum_seats": b.capacity}
    if o["status"] == "Published":
        error = staff_check(db, proposed, o)
        if error:
            fail(error, 409)
    db.run(
        "UPDATE course_sections SET maximum_seats=%s,reserved_seats=%s WHERE id=%s",
        (b.capacity, b.reserved_seats, zid),
    )
    db.audit(
        u,
        "Updated section capacity",
        "course_sections",
        zid,
        {
            "previous": s["maximum_seats"],
            "capacity": b.capacity,
            "reserved": b.reserved_seats,
        },
    )
    promote(db, u, zid)
    return {"status": "Section capacity updated"}
