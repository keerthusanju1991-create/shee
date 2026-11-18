import io, csv, re, zipfile, logging, time
from typing import Literal
from collections import defaultdict
from fastapi import FastAPI, Depends, Request, Query, UploadFile, File
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from .core import *
from .engine import *
from .queries import *
from .lifecycle import lifespan

app = FastAPI(
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
    title="DSU–CFCS Academic Workspace",
    version="1.0.1",
    description="Controlled flexible credit system. Scoped endpoints require an expiring bearer session. Preview uses synthetic test records only.",
)


@app.middleware("http")
async def safe_headers(req, call_next):
    body_limit = (UPLOAD_LIMIT_MIB * 1024 * 1024 + 128 * 1024) if SERVERLESS else 6 * 1024 * 1024
    if int(req.headers.get("content-length", "0") or 0) > body_limit:
        return JSONResponse({"detail": f"Upload exceeds the {UPLOAD_LIMIT_MIB} MiB file limit."}, 413)
    res = await call_next(req)
    res.headers["X-Content-Type-Options"] = "nosniff"
    res.headers["Referrer-Policy"] = "same-origin"
    res.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if req.url.path.startswith("/api"):
        res.headers["Cache-Control"] = "no-store"
    if req.url.path == "/":
        res.headers["Cache-Control"] = "no-cache"
        res.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
        )
    return res


@app.exception_handler(psycopg.IntegrityError)
async def bad_record(req, e):
    return JSONResponse(
        {
            "detail": "This change conflicts with an existing record or academic data constraint. No changes were committed."
        },
        409,
    )


@app.exception_handler(psycopg.DataError)
async def invalid_value(req, e):
    return JSONResponse(
        {
            "detail": "An input has an invalid value or data type. No changes were committed."
        },
        422,
    )


@app.get("/docs", include_in_schema=False)
def api_docs():
    from fastapi.openapi.docs import get_swagger_ui_html

    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="DSU–CFCS API reference",
        swagger_js_url="/assets/api-docs/swagger-ui-bundle.js",
        swagger_css_url="/assets/api-docs/swagger-ui.css",
        swagger_favicon_url="/assets/favicon.svg",
    )


@app.get("/api/health")
def health(db=Depends(get_db)):
    return {
        "status": "online",
        "database": db.val("SELECT 1") == 1,
        "mode": "synthetic" if DEMO else "production",
    }


@app.get("/api/workspace")
def workspace():
    return {
        "upload_limit_mib": UPLOAD_LIMIT_MIB,
        "synthetic": DEMO,
        "roles": ROLES if DEMO else [],
        "name": "DSU–CFCS",
        "institution": "Dhanalakshmi Srinivasan University",
    }


class Login(Input):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=512)


class Demo(Input):
    role: str = "Dean – SET"


def rate(db, request, identity, limit=15):
    key = hashlib.sha256(
        ((request.client.host if request.client else "local") + identity).encode()
    ).hexdigest()
    if (
        db.val(
            "SELECT count(*) FROM login_attempts WHERE client_key=%s AND attempted_at>now()-interval '10 minutes'",
            (key,),
        )
        >= limit
    ):
        fail("Too many sign-in attempts. Try again in ten minutes.", 429)
    db.run("INSERT INTO login_attempts(client_key) VALUES(%s)", (key,))
    db.c.commit()


@app.post("/api/auth/login")
def login(body: Login, request: Request, db=Depends(get_db)):
    rate(db, request, "all-password-sign-ins", 100)
    rate(db, request, body.username.lower())
    u = db.one(
        "SELECT * FROM users WHERE username=%s AND active AND NOT demo",
        (body.username,),
    )
    try:
        good = u and u["password_hash"] and PH.verify(u["password_hash"], body.password)
    except Exception:
        good = False
    if not good:
        fail("The username or password is incorrect.", 401)
    db.audit(u, "Signed in", "users", u["id"])
    issued = session(db, u["id"])
    db.c.commit()  # The next request may arrive before a yielded dependency exits.
    return issued


@app.post("/api/auth/demo")
def demo(body: Demo, request: Request, db=Depends(get_db)):
    if (
        not DEMO
        or db.val("SELECT value FROM workspace_metadata WHERE key='dataset'")
        != "synthetic"
    ):
        fail("Demo access is disabled.", 403)
    rate(db, request, "synthetic-session", 120)
    u = db.one(
        "SELECT u.id FROM users u JOIN roles r ON r.id=u.role_id WHERE u.demo AND u.active AND r.name=%s",
        (body.role,),
    )
    if not u:
        fail("Unknown demo role.")
    issued = session(db, u["id"])
    db.c.commit()  # The next request may arrive before a yielded dependency exits.
    return issued


@app.get("/api/auth/me")
def me(u=Depends(user)):
    return u


@app.post("/api/auth/logout")
def logout(request: Request, db=Depends(get_db), u=Depends(user)):
    db.run(
        "DELETE FROM sessions WHERE token_hash=%s",
        (request_session_digest(request),),
    )
    return {"status": "Signed out"}


@app.get("/api/dashboard")
def dash(days: int = 7, db=Depends(get_db), u=Depends(user)):
    return dashboard(db, u, days)


@app.get("/api/students")
def students(
    q: str = "",
    department: str = "",
    regulation: str = "",
    page: int = 1,
    size: int = 20,
    db=Depends(get_db),
    u=Depends(user),
):
    sc, p = student_scope(u)
    return db.page(
        STUDENTS
        + " AND "
        + sc
        + " AND (s.name ILIKE %s OR s.register_number ILIKE %s) AND (%s='' OR d.code=%s) AND (%s='' OR r.code=%s) ORDER BY s.id",
        p + ["%" + q + "%"] * 2 + [department] * 2 + [regulation] * 2,
        page,
        size,
    )


@app.get("/api/students/{sid:int}")
def student(sid: int, db=Depends(get_db), u=Depends(user)):
    if u["role"] == "Faculty":
        fail(
            "Academic transcripts are restricted to the student and authorised academic reviewers.",
            403,
        )
    s = get_student(db, u, sid)
    s["results"] = db.all(
        "SELECT c.title,c.code,c.credits,a.attempt,a.outcome,a.verified FROM student_course_results a JOIN courses c ON c.id=a.course_id WHERE a.student_id=%s ORDER BY c.semester_id,c.code",
        (sid,),
    )
    s["attendance"] = db.all(
        "SELECT c.title,c.code,a.attended,a.conducted,a.status,a.repeat_approved FROM attendance_records a JOIN courses c ON c.id=a.course_id WHERE student_id=%s ORDER BY a.id",
        (sid,),
    )
    return s


@app.get("/api/students/{sid}/credit-audit")
def credits(sid: int, db=Depends(get_db), u=Depends(user)):
    if u["role"] == "Faculty":
        fail("Individual credit audits require academic review authority.", 403)
    return credit_audit(db, get_student(db, u, sid))


@app.get("/api/students/{sid}/eligibility")
@app.get("/api/students/{sid}/eligible-courses")
def eligible(
    sid: int, only_eligible: bool = False, db=Depends(get_db), u=Depends(user)
):
    if u["role"] == "Faculty":
        fail("Individual eligibility requires academic review authority.", 403)
    s = get_student(db, u, sid)
    items = [
        evaluate(db, s, c["id"])
        for c in db.all(
            "SELECT id FROM courses WHERE regulation_id=%s AND programme_id=%s AND deleted_at IS NULL ORDER BY semester_id,code",
            (s["regulation_id"], s["programme_id"]),
        )
    ]
    return {
        "items": [r for r in items if not only_eligible or r["status"] == "Eligible"],
        "student": s["name"],
    }


@app.get("/api/regulations")
def regulations(db=Depends(get_db), u=Depends(user)):
    return {
        "items": db.all(
            "SELECT r.*,(SELECT count(*) FROM students s WHERE s.regulation_id=r.id AND deleted_at IS NULL) AS students FROM regulations r WHERE deleted_at IS NULL ORDER BY code"
        )
    }


@app.get("/api/courses")
def courses(
    q: str = "",
    department: str = "",
    regulation: str = "",
    semester: int = 0,
    page: int = 1,
    size: int = 20,
    db=Depends(get_db),
    u=Depends(user),
):
    ds, p = dept_scope(u, "d.id")
    sql = (
        COURSES
        + " AND "
        + ds
        + " AND (c.title ILIKE %s OR c.code ILIKE %s) AND (%s='' OR d.code=%s) AND (%s='' OR r.code=%s) AND (%s=0 OR cs.number=%s)"
    )
    pa = p + ["%" + q + "%"] * 2 + [department] * 2 + [regulation] * 2 + [semester] * 2
    if u["role"] == "Student":
        s = get_student(db, u, u["student_id"])
        sql += " AND c.regulation_id=%s AND c.programme_id=%s"
        pa.extend([s["regulation_id"], s["programme_id"]])
    return db.page(sql + " ORDER BY c.code,r.code DESC", pa, page, size)


@app.get("/api/courses/{cid}/prerequisites")
def prereqs(cid: int, db=Depends(get_db), u=Depends(user)):
    c = db.one(COURSES + " AND c.id=%s", (cid,))
    if not c:
        fail("Course not found.", 404)
    return {
        "course": c,
        "groups": db.all(
            "SELECT g.name,g.kind,g.operator,c.title,c.code FROM course_prerequisite_groups g JOIN course_prerequisites p ON p.group_id=g.id JOIN courses c ON c.id=p.prerequisite_id WHERE g.course_id=%s",
            (cid,),
        ),
        "equivalences": db.all(
            "SELECT c.code,c.title,r.code AS regulation,e.approved,e.valid_until FROM course_equivalences e JOIN courses c ON c.id=CASE WHEN e.course_a=%s THEN e.course_b ELSE e.course_a END JOIN regulations r ON r.id=c.regulation_id WHERE e.course_a=%s OR e.course_b=%s",
            (cid, cid, cid),
        ),
    }


@app.get("/api/offerings")
def offerings(
    q: str = "",
    status: str = "",
    department: str = "",
    page: int = 1,
    size: int = 20,
    db=Depends(get_db),
    u=Depends(user),
):
    ds, p = dept_scope(u, "d.id")
    if u["role"] == "Faculty":
        ds, p = "z.faculty_id=%s", [u["faculty_id"]]
    return db.page(
        OFFERINGS
        + " AND "
        + ds
        + " AND (c.title ILIKE %s OR c.code ILIKE %s) AND (%s='' OR o.status=%s) AND (%s='' OR d.code=%s) ORDER BY o.id",
        p + ["%" + q + "%"] * 2 + [status] * 2 + [department] * 2,
        page,
        size,
    )


@app.get("/api/offerings/{oid}")
def offering(oid: int, db=Depends(get_db), u=Depends(user)):
    ds, p = dept_scope(u, "d.id")
    if u["role"] == "Faculty":
        ds, p = "z.faculty_id=%s", [u["faculty_id"]]
    if u["role"] == "Student":
        ds, p = (
            "(d.id=%s OR EXISTS(SELECT 1 FROM course_enrolments en WHERE en.section_id=z.id AND en.student_id=%s))",
            [u["department_id"], u["student_id"]],
        )
    o = db.one(OFFERINGS + " AND o.id=%s AND " + ds, [oid] + p)
    if not o:
        fail("Offering not found in your scope.", 404)
    o["slots"] = db.all(
        "SELECT t.*,r.code AS room FROM timetable_slots t JOIN classrooms r ON r.id=t.room_id WHERE t.section_id=%s",
        (o["section_id"],),
    )
    o["mapping_details"] = db.all(
        "SELECT c.code,r.code AS regulation,c.credits,a.name AS assessment FROM offering_mappings m JOIN courses c ON c.id=m.course_id JOIN regulations r ON r.id=c.regulation_id JOIN assessment_schemes a ON a.id=c.assessment_id WHERE m.offering_id=%s",
        (oid,),
    )
    return o


class Confirm(Input):
    confirm: bool
    remarks: str = Field(default="", max_length=1000)


class Slot(Input):
    room_id: int = Field(gt=0)
    weekday: int = Field(ge=1, le=7)
    starts: int = Field(ge=0, le=1439)
    ends: int = Field(ge=1, le=1440)
    kind: Literal["Theory", "Tutorial", "Practical"] = "Theory"


class Offer(Input):
    course_id: int = Field(gt=0)
    faculty_id: int | None = None
    section: str = Field(default="A", min_length=1, max_length=30)
    capacity: int = Field(gt=0, le=1000)
    minimum_seats: int = Field(default=0, ge=0)
    reserved_seats: int = Field(default=0, ge=0)
    window_id: int
    recovery_allowed: bool = False
    slots: list[Slot] = Field(min_length=1, max_length=20)
    mappings: list[int] = Field(default_factory=list, max_length=50)
    confirm: bool


@app.post("/api/offerings")
def create_offer(b: Offer, db=Depends(get_db), u=Depends(user)):
    permit(u, "publish")
    confirmed(b.confirm)
    c = db.one(
        "SELECT c.*,p.department_id FROM courses c JOIN programmes p ON p.id=c.programme_id WHERE c.id=%s AND c.approved AND c.deleted_at IS NULL",
        (b.course_id,),
    )
    if not c:
        fail("Choose an approved curriculum course.")
    check_dept(u, c["department_id"])
    y = year_id(db)
    if not db.val(
        "SELECT id FROM registration_windows WHERE id=%s AND academic_year_id=%s",
        (b.window_id, y),
    ):
        fail("The registration window must belong to the current academic year.")
    oid = db.insert(
        "course_offerings",
        course_id=c["id"],
        department_id=c["department_id"],
        academic_year_id=y,
        window_id=b.window_id,
        status="Draft",
        recovery_allowed=b.recovery_allowed,
    )
    z = db.insert(
        "course_sections",
        offering_id=oid,
        label=b.section,
        faculty_id=b.faculty_id,
        maximum_seats=b.capacity,
        minimum_seats=b.minimum_seats,
        reserved_seats=b.reserved_seats,
    )
    for m in set(b.mappings + [c["id"]]):
        if m not in recognised(db, c["id"], False):
            fail("A shared section requires an approved, valid equivalence.")
        db.run("INSERT INTO offering_mappings VALUES(%s,%s)", (oid, m))
    for t in b.slots:
        if t.starts >= t.ends:
            fail("End time must be after start time.")
        db.insert("timetable_slots", section_id=z, **t.model_dump())
    db.audit(u, "Created draft offering", "course_offerings", oid)
    return {"id": oid, "status": "Draft"}


@app.post("/api/offerings/{oid}/publish")
def publish(oid: int, b: Confirm, db=Depends(get_db), u=Depends(user)):
    permit(u, "publish")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    o = db.one("SELECT * FROM course_offerings WHERE id=%s FOR UPDATE", (oid,))
    if not o:
        fail("Offering not found.", 404)
    check_dept(u, o["department_id"])
    if o["status"] not in ["Draft", "Pending approval"]:
        fail("Only a draft or pending offering can be published.", 409)
    sections = db.all("SELECT * FROM course_sections WHERE offering_id=%s", (oid,))
    if not sections:
        fail("At least one section is required.")
    for m in db.all(
        "SELECT course_id FROM offering_mappings WHERE offering_id=%s", (oid,)
    ):
        if m["course_id"] not in recognised(db, o["course_id"], False):
            fail("A mapped course lacks current approved equivalence.")
    if not db.val(
        "SELECT id FROM courses WHERE id=%s AND approved AND deleted_at IS NULL",
        (o["course_id"],),
    ):
        fail("The base curriculum course is not currently approved.")
    for sec in sections:
        error = staff_check(db, sec, o)
        if error:
            fail(error, 409)
    db.run(
        "UPDATE course_offerings SET status='Published',approved_by=%s WHERE id=%s",
        (u["id"], oid),
    )
    db.audit(u, "Published offering", "course_offerings", oid, {"remarks": b.remarks})
    return {"status": "Published"}


@app.post("/api/offerings/{oid}/withdraw")
def withdraw(oid: int, b: Confirm, db=Depends(get_db), u=Depends(user)):
    permit(u, "publish")
    confirmed(b.confirm)
    o = offering(oid, db, u)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    if not b.remarks:
        fail("A withdrawal reason is required.")
    if db.val(
        "SELECT count(*) FROM course_enrolments e JOIN course_sections z ON z.id=e.section_id WHERE z.offering_id=%s AND e.status IN('Registered','Waitlisted','Pending approval')",
        (oid,),
    ):
        fail(
            "Resolve active enrolments and waitlist entries before withdrawing this offering.",
            409,
        )
    db.run("UPDATE course_offerings SET status='Withdrawn' WHERE id=%s", (oid,))
    db.audit(u, "Withdrew offering", "course_offerings", oid, {"reason": b.remarks})
    return {"status": "Withdrawn", "preferences": "Retained for demand planning"}


@app.get("/api/faculty/pool")
def faculty(
    q: str = "",
    department: str = "",
    page: int = 1,
    size: int = 20,
    db=Depends(get_db),
    u=Depends(user),
):
    ds, p = dept_scope(u, "d.id")
    return db.page(
        FACULTY
        + " AND "
        + ds
        + " AND (f.name ILIKE %s OR f.specialisation ILIKE %s) AND (%s='' OR d.code=%s) ORDER BY d.id,f.id",
        p + ["%" + q + "%"] * 2 + [department] * 2,
        page,
        size,
    )


@app.get("/api/faculty/{fid:int}")
def faculty_profile(fid: int, db=Depends(get_db), u=Depends(user)):
    r = db.one(FACULTY + " AND f.id=%s", (fid,))
    if not r:
        fail("Faculty member not found.", 404)
    check_dept(u, r["department_id"])
    r["courses"] = db.all(
        "SELECT DISTINCT c.title FROM faculty_course_competencies f JOIN courses c ON c.id=f.course_id WHERE f.faculty_id=%s AND f.approved",
        (fid,),
    )
    r["assignments"] = db.all(OFFERINGS + " AND z.faculty_id=%s", (fid,))
    return r


class Assign(Input):
    section_id: int
    faculty_id: int
    confirm: bool


@app.post("/api/faculty/assign")
def assign(b: Assign, db=Depends(get_db), u=Depends(user)):
    permit(u, "assign")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    s = db.one("SELECT * FROM course_sections WHERE id=%s FOR UPDATE", (b.section_id,))
    if not s:
        fail("Section not found.", 404)
    o = db.one("SELECT * FROM course_offerings WHERE id=%s", (s["offering_id"],))
    check_dept(u, o["department_id"])
    err = staff_check(db, s, o, b.faculty_id)
    if err:
        fail(err, 409)
    db.run(
        "UPDATE course_sections SET faculty_id=%s WHERE id=%s",
        (b.faculty_id, b.section_id),
    )
    db.audit(
        u,
        "Approved faculty allocation",
        "course_sections",
        b.section_id,
        {"from": s["faculty_id"], "to": b.faculty_id},
    )
    return {"status": "Assigned"}


@app.get("/api/sections/{sid}/suggestions")
def suggestions(sid: int, db=Depends(get_db), u=Depends(user)):
    permit(u, "assign")
    s = db.one("SELECT * FROM course_sections WHERE id=%s", (sid,))
    if not s:
        fail("Section not found.", 404)
    o = db.one("SELECT * FROM course_offerings WHERE id=%s", (s["offering_id"],))
    check_dept(u, o["department_id"])
    return {
        "items": [
            f
            for f in db.all(FACULTY + " ORDER BY allocated")
            if not staff_check(db, s, o, f["id"])
        ]
    }


class Registration(Input):
    student_id: int
    course_id: int
    section_id: int | None = None
    basket: Literal["Auto", "Normal", "Recovery"] = "Auto"
    confirm: bool = False


@app.post("/api/registration/validate")
def validate(b: Registration, db=Depends(get_db), u=Depends(user)):
    return evaluate(
        db, get_student(db, u, b.student_id), b.course_id, b.section_id, b.basket
    )


@app.post("/api/registration/submit")
def register(b: Registration, db=Depends(get_db), u=Depends(user)):
    permit(u, "register")
    return submit(
        db,
        u,
        get_student(db, u, b.student_id),
        b.course_id,
        b.section_id,
        b.basket,
        b.confirm,
    )


@app.get("/api/registration/student/{sid}")
def registrations(sid: int, db=Depends(get_db), u=Depends(user)):
    get_student(db, u, sid)
    extra = " AND z.faculty_id=%s" if u["role"] == "Faculty" else ""
    return {
        "items": db.all(
            ENROL + " AND s.id=%s" + extra + " ORDER BY e.created_at DESC",
            [sid] + ([u["faculty_id"]] if extra else []),
        )
    }


@app.post("/api/registration/{eid}/drop")
def drop(eid: int, b: Confirm, db=Depends(get_db), u=Depends(user)):
    permit(u, "register")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    r = db.one("SELECT * FROM course_enrolments WHERE id=%s FOR UPDATE", (eid,))
    if not r:
        fail("Registration not found.", 404)
    get_student(db, u, r["student_id"])
    if r["status"] not in ["Registered", "Waitlisted", "Pending approval"]:
        fail("This enrolment is not active.", 409)
    db.run(
        "UPDATE course_enrolments SET status='Dropped',changed_at=now() WHERE id=%s",
        (eid,),
    )
    db.audit(u, "Dropped registration", "course_enrolments", eid, {"reason": b.remarks})
    promote(db, u, r["section_id"])
    return {"status": "Dropped"}


@app.post("/api/registration/{eid}/{decision}")
def reg_decide(
    eid: int,
    decision: Literal["approve", "reject"],
    b: Confirm,
    db=Depends(get_db),
    u=Depends(user),
):
    permit(u, "review")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    r = db.one(
        "SELECT * FROM course_enrolments WHERE id=%s AND status='Pending approval' FOR UPDATE",
        (eid,),
    )
    if not r:
        fail("No pending registration to review.", 409)
    s = get_student(db, u, r["student_id"])
    state = "Rejected"
    if decision == "approve":
        res = evaluate(db, s, r["course_id"], r["section_id"], "Auto", eid)
        if res["status"] not in ["Eligible", "Waitlisted"]:
            fail(res["reason"], 409)
        state = "Registered" if res["status"] == "Eligible" else "Waitlisted"
    db.run(
        "UPDATE course_enrolments SET status=%s,changed_at=now() WHERE id=%s",
        (state, eid),
    )
    db.insert(
        "registration_approvals",
        enrolment_id=eid,
        student_id=s["id"],
        academic_year_id=year_id(db),
        authority_id=u["id"],
        decision=state,
        remarks=b.remarks,
    )
    db.audit(u, "Registration " + state, "course_enrolments", eid)
    return {"status": state}


@app.post("/api/students/{sid}/finalise")
def finalise(sid: int, b: Confirm, db=Depends(get_db), u=Depends(user)):
    permit(u, "register")
    confirmed(b.confirm)
    s = get_student(db, u, sid)
    a = credit_audit(db, s)
    if a["minimum"] is None or a["registered"] < a["minimum"]:
        fail("The minimum approved credit requirement has not been met.", 409)
    db.insert(
        "registration_approvals",
        student_id=sid,
        academic_year_id=year_id(db),
        authority_id=u["id"],
        decision="Load finalised",
        remarks=b.remarks,
    )
    db.audit(u, "Finalised credit load", "students", sid, {"credits": a["registered"]})
    return {"status": "Load finalised"}


@app.get("/api/timetable")
def timetable(
    student_id: int = 0,
    faculty_id: int = 0,
    room_id: int = 0,
    department: str = "",
    db=Depends(get_db),
    u=Depends(user),
):
    ds, p = dept_scope(u, "d.id")
    if u["role"] in ["Faculty", "Student"]:
        ds, p = "true", []
    sc = " AND " + ds
    sid = u["student_id"] if u["role"] == "Student" else student_id
    if sid:
        get_student(db, u, sid)
        sc += " AND EXISTS(SELECT 1 FROM course_enrolments e WHERE e.section_id=z.id AND student_id=%s AND e.status='Registered')"
        p.append(sid)
    fid = u["faculty_id"] if u["role"] == "Faculty" else faculty_id
    if fid:
        sc += " AND z.faculty_id=%s"
        p.append(fid)
    if room_id:
        sc += " AND t.room_id=%s"
        p.append(room_id)
    if department:
        sc += " AND d.code=%s"
        p.append(department)
    return {
        "items": db.all(
            "SELECT t.*,c.code,c.title,f.name AS faculty,r.code AS room,d.code AS department,z.label AS section,o.id AS offering_id FROM timetable_slots t JOIN course_sections z ON z.id=t.section_id JOIN course_offerings o ON o.id=z.offering_id JOIN courses c ON c.id=o.course_id JOIN departments d ON d.id=o.department_id JOIN classrooms r ON r.id=t.room_id LEFT JOIN faculty f ON f.id=z.faculty_id WHERE o.status='Published' AND o.academic_year_id=%s"
            + sc
            + " ORDER BY t.weekday,t.starts,c.code",
            [year_id(db)] + p,
        )
    }


@app.get("/api/exceptions")
def exceptions(
    status: str = "",
    q: str = "",
    page: int = 1,
    size: int = 20,
    db=Depends(get_db),
    u=Depends(user),
):
    permit(u, "requests", "review")
    sc, p = student_scope(u)
    return db.page(
        EXCEPT
        + " AND "
        + sc
        + " AND (%s='' OR x.status=%s) AND (s.name ILIKE %s OR x.kind ILIKE %s) ORDER BY x.requested_at DESC",
        p + [status] * 2 + ["%" + q + "%"] * 2,
        page,
        size,
    )


class ExceptionIn(Input):
    student_id: int
    course_id: int | None = None
    kind: Literal[
        "Conditional prerequisite",
        "Credit overload",
        "Course equivalence",
        "Special offering",
        "Timetable",
        "Academic progression",
        "Attendance exception",
    ]
    reason: str = Field(min_length=8, max_length=2000)
    evidence: str = Field(min_length=3, max_length=1000)
    extra_credits: float = Field(default=0, ge=0, le=100)
    confirm: bool


@app.post("/api/exceptions")
def exception_new(b: ExceptionIn, db=Depends(get_db), u=Depends(user)):
    permit(u, "requests")
    confirmed(b.confirm)
    s = get_student(db, u, b.student_id)
    if b.course_id and not db.val(
        "SELECT id FROM courses WHERE id=%s AND programme_id=%s AND regulation_id=%s",
        (b.course_id, s["programme_id"], s["regulation_id"]),
    ):
        fail("The requested course is not in this student’s curriculum.")
    if (
        b.kind
        in [
            "Conditional prerequisite",
            "Attendance exception",
            "Course equivalence",
            "Timetable",
            "Special offering",
        ]
        and not b.course_id
    ):
        fail("Select a course for this exception.")
    policy = db.one(
        "SELECT authority FROM exception_policies WHERE regulation_id=%s AND kind=%s AND active",
        (s["regulation_id"], b.kind),
    )
    if not policy:
        fail(
            "An authorised exception policy has not been configured for this regulation and request category."
        )
    authority = policy["authority"]
    eid = db.insert(
        "academic_exceptions",
        **b.model_dump(exclude={"confirm"}),
        requested_by=u["id"],
        authority=authority,
        status="Pending",
        academic_year_id=year_id(db),
    )
    db.audit(u, "Submitted exception", "academic_exceptions", eid, {"kind": b.kind})
    return {"id": eid, "status": "Pending", "authority": authority}


class Decision(Input):
    decision: Literal["Approved", "Rejected"]
    remarks: str = Field(min_length=3, max_length=1500)
    confirm: bool


@app.post("/api/exceptions/{eid}/decision")
def decide(eid: int, b: Decision, db=Depends(get_db), u=Depends(user)):
    permit(u, "review")
    confirmed(b.confirm)
    e = db.one("SELECT * FROM academic_exceptions WHERE id=%s FOR UPDATE", (eid,))
    if not e:
        fail("Request not found.", 404)
    get_student(db, u, e["student_id"])
    if e["status"] != "Pending":
        fail("This request has already been decided.", 409)
    if u["role"] not in [e["authority"], "Super Administrator"]:
        fail("This decision is reserved for " + e["authority"] + ".", 403)
    if e["requested_by"] == u["id"]:
        fail("An independent approving authority is required.", 403)
    db.run(
        "UPDATE academic_exceptions SET status=%s,remarks=%s,decided_by=%s,decided_at=now() WHERE id=%s",
        (b.decision, b.remarks, u["id"], eid),
    )
    if e["kind"] == "Conditional prerequisite":
        db.run("SELECT pg_advisory_xact_lock(740126)")
        for en in db.all(
            "SELECT * FROM course_enrolments WHERE student_id=%s AND course_id=%s AND academic_year_id=%s AND status='Pending approval' FOR UPDATE",
            (e["student_id"], e["course_id"], e["academic_year_id"]),
        ):
            state = "Rejected"
            if b.decision == "Approved":
                res = evaluate(
                    db,
                    get_student(db, u, e["student_id"]),
                    en["course_id"],
                    en["section_id"],
                    "Auto",
                    en["id"],
                )
                if res["status"] not in ["Eligible", "Waitlisted"]:
                    continue
                state = "Registered" if res["status"] == "Eligible" else "Waitlisted"
            db.run(
                "UPDATE course_enrolments SET status=%s,changed_at=now() WHERE id=%s",
                (state, en["id"]),
            )
            db.insert(
                "registration_approvals",
                enrolment_id=en["id"],
                student_id=e["student_id"],
                academic_year_id=e["academic_year_id"],
                authority_id=u["id"],
                decision=state,
                remarks=b.remarks,
            )
            db.audit(
                u,
                "Reviewed provisional registration",
                "course_enrolments",
                en["id"],
                {"status": state},
            )
    db.audit(
        u,
        b.decision + " exception",
        "academic_exceptions",
        eid,
        {"remarks": b.remarks, "student_id": e["student_id"], "type": e["kind"]},
    )
    return {"status": b.decision}


class Preference(Input):
    student_id: int
    course_id: int
    rank: int = Field(ge=1, le=3)
    interest: Literal["Core", "Floating", "Open elective"]
    confirm: bool


@app.post("/api/preferences")
def prefer(b: Preference, db=Depends(get_db), u=Depends(user)):
    permit(u, "preferences")
    confirmed(b.confirm)
    s = get_student(db, u, b.student_id)
    if not db.val(
        "SELECT id FROM courses WHERE id=%s AND programme_id=%s AND regulation_id=%s AND approved",
        (b.course_id, s["programme_id"], s["regulation_id"]),
    ):
        fail("Select a course in your approved curriculum.")
    db.run(
        "INSERT INTO course_preferences(student_id,course_id,academic_year_id,rank,interest) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(student_id,academic_year_id,rank) DO UPDATE SET course_id=EXCLUDED.course_id,interest=EXCLUDED.interest",
        (s["id"], b.course_id, year_id(db), b.rank, b.interest),
    )
    db.audit(
        u,
        "Updated course preference",
        "students",
        s["id"],
        {"rank": b.rank, "course_id": b.course_id},
    )
    return {"status": "Preference saved"}


@app.get("/api/preferences")
def preferences(db=Depends(get_db), u=Depends(user)):
    sc, p = student_scope(u)
    return {
        "items": db.all(
            "SELECT x.*,s.name AS student,c.code,c.title FROM course_preferences x JOIN students s ON s.id=x.student_id JOIN courses c ON c.id=x.course_id WHERE "
            + sc
            + " ORDER BY s.id,x.rank LIMIT 500",
            p,
        )
    }


@app.get("/api/attendance/debarred")
def debarred(db=Depends(get_db), u=Depends(user)):
    return {"items": report_data(db, u, "attendance-debarred")}


@app.get("/api/reports")
def reportlist(u=Depends(user)):
    if u["role"] == "Faculty":
        return {
            "items": [
                {"key": k, "label": REPORTS[k]}
                for k in [
                    "registration",
                    "floating",
                    "faculty-workload",
                    "attendance",
                    "attendance-debarred",
                ]
            ]
        }
    return {
        "items": [
            {"key": k, "label": v}
            for k, v in REPORTS.items()
            if u["role"] not in ["Student", "Faculty", "Faculty Advisor"]
            or k
            not in [
                "department",
                "course-utilisation",
                "section-strength",
                "regulation-distribution",
                "faculty-shortage",
                "course-demand",
                "faculty-workload",
            ]
        ]
    }


@app.get("/api/reports/{key}")
def report(
    key: str, page: int = 1, size: int = 20, db=Depends(get_db), u=Depends(user)
):
    permit(u, "report")
    page = max(1, page)
    size = min(100, max(1, size))
    rows = report_data(db, u, key)
    return {
        "items": rows[(page - 1) * size : page * size],
        "total": len(rows),
        "page": page,
        "size": size,
        "title": REPORTS[key],
    }


def export_file(rows, fmt, name):
    from openpyxl import Workbook

    keys = list(rows[0]) if rows else ["message"]
    keys = [k for k in keys if k != "id" and not k.endswith("_id")]
    labels = [k.replace("_", " ").title() for k in keys]

    def safe(v):
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, str) and v[:1] in ["=", "+", "-", "@", "\t", "\r"]:
            return "'" + v
        return v if v is not None else ""

    values = [[safe(r.get(k, "")) for k in keys] for r in rows]
    out = io.BytesIO()
    if fmt == "xlsx":
        from openpyxl.styles import Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = "Academic report"
        ws.append(labels)
        for v in values:
            ws.append(v)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="18294A")
            ws.column_dimensions[c.column_letter].width = 24
        ws.auto_filter.ref = ws.dimensions
        ws.freeze_panes = "A2"
        wb.save(out)
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "csv":
        st = io.StringIO()
        w = csv.writer(st)
        w.writerow(labels)
        w.writerows(values)
        out.write(st.getvalue().encode("utf-8-sig"))
        mime = "text/csv"
    else:
        from reportlab.lib.pagesizes import landscape, A3
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from xml.sax.saxutils import escape

        doc = SimpleDocTemplate(
            out,
            pagesize=landscape(A3),
            leftMargin=24,
            rightMargin=24,
            topMargin=24,
            bottomMargin=24,
        )
        style = getSampleStyleSheet()
        tiny = ParagraphStyle("cell", fontSize=6.5, leading=8, wordWrap="CJK")
        data = [
            [Paragraph(escape(str(x)), tiny) for x in row] for row in [labels] + values
        ]
        t = Table(
            data,
            repeatRows=1,
            colWidths=[(landscape(A3)[0] - 48) / len(keys)] * len(keys),
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dce5f6")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f3f6fb")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d4dce9")),
                ]
            )
        )
        doc.build(
            [
                Paragraph("DSU-CFCS | " + escape(name), style["Heading1"]),
                Paragraph(
                    ("Synthetic pilot data · " if DEMO else "")
                    + "School of Engineering & Technology | "
                    + date.today().isoformat(),
                    style["Normal"],
                ),
                Spacer(1, 12),
                t,
            ]
        )
        mime = "application/pdf"
    return Response(
        out.getvalue(),
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="DSU-{re.sub(r"[^a-zA-Z0-9-]", "-", name)}.{fmt}"'
        },
    )


@app.get("/api/reports/{key}/export")
def export(
    key: str,
    format: Literal["csv", "xlsx", "pdf"] = "xlsx",
    db=Depends(get_db),
    u=Depends(user),
):
    permit(u, "report")
    rows = report_data(db, u, key)
    db.audit(
        u,
        "Exported report",
        "reports",
        None,
        {"report": key, "format": format, "rows": len(rows)},
    )
    return export_file(rows, format, REPORTS[key])


@app.get("/api/audit")
def audit(page: int = 1, size: int = 25, db=Depends(get_db), u=Depends(user)):
    permit(u, "audit")
    scope = (
        " WHERE a.actor_id=%s OR a.actor_id IS NULL"
        if u["role"] == "Dean – SET"
        else ""
    )
    return db.page(
        "SELECT a.id,a.action,a.entity,a.entity_id,a.detail,a.recorded_at,COALESCE(u.name,'System') AS actor FROM academic_audit_logs a LEFT JOIN users u ON u.id=a.actor_id"
        + scope
        + " ORDER BY a.id DESC",
        [u["id"]] if scope else [],
        page,
        size,
    )


@app.get("/api/search")
def search(
    q: str = Query(min_length=2, max_length=90), db=Depends(get_db), u=Depends(user)
):
    return {
        "students": students(q=q, size=4, db=db, u=u)["items"],
        "courses": courses(q=q, size=4, db=db, u=u)["items"],
        "faculty": faculty(q=q, size=3, db=db, u=u)["items"],
    }


@app.get("/api/lookups")
def lookups(db=Depends(get_db), u=Depends(user)):
    ds, p = dept_scope(u, "id")
    return {
        "schools": db.all("SELECT id,code,name FROM schools WHERE deleted_at IS NULL"),
        "departments": db.all(
            "SELECT id,code,name FROM departments WHERE deleted_at IS NULL AND "
            + ds
            + " ORDER BY id",
            p,
        ),
        "regulations": db.all(
            "SELECT id,code,name FROM regulations WHERE deleted_at IS NULL ORDER BY code DESC"
        ),
        "programmes": db.all(
            "SELECT id,code,name FROM programmes WHERE deleted_at IS NULL"
        ),
        "rooms": db.all(
            "SELECT id,code,capacity FROM classrooms WHERE deleted_at IS NULL ORDER BY code"
        ),
        "windows": db.all(
            "SELECT id,name FROM registration_windows WHERE academic_year_id IN(SELECT id FROM academic_years WHERE current_date BETWEEN starts_on AND ends_on)"
        ),
        "years": db.all("SELECT id,label FROM academic_years ORDER BY starts_on DESC"),
        "categories": db.all(
            "SELECT id,code,name FROM course_categories WHERE deleted_at IS NULL"
        ),
    }


from .admin import router as admin_router
from .imports import router as import_router
from .teaching import router as teaching_router

app.include_router(teaching_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(import_router, prefix="/api")
app.mount("/assets", StaticFiles(directory=ROOT / "frontend/public"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path.startswith("api/"):
        fail("API endpoint not found.", 404)
    return FileResponse(ROOT / "frontend/index.html")
