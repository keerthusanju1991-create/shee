"""Bounded, all-or-nothing, two-stage XLSX/CSV imports with row-level diagnostics."""

import io, csv, zipfile, math
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from fastapi.responses import Response
from .core import *
from .engine import recognised, submit

router = APIRouter()
FORMATS = {
    "students": [
        "register_number",
        "name",
        "department",
        "programme",
        "admission_year",
        "regulation",
        "semester",
        "completed_courses",
        "failed_courses",
        "debarred_courses",
        "registered_courses",
        "earned_credits",
        "cgpa",
        "status",
    ],
    "curriculum": [
        "regulation",
        "programme",
        "semester",
        "course_code",
        "title",
        "credits",
        "category",
        "lecture",
        "tutorial",
        "practical",
        "prerequisites",
        "assessment",
        "approved",
    ],
    "prerequisites": [
        "regulation",
        "programme",
        "course_code",
        "group",
        "operator",
        "kind",
        "prerequisite_code",
    ],
    "equivalences": [
        "regulation",
        "programme",
        "course_code",
        "target_regulation",
        "target_programme",
        "target_code",
        "valid_from",
        "valid_until",
        "recognise_credit",
        "approved",
    ],
    "attendance": [
        "register_number",
        "course_code",
        "attempt",
        "attended",
        "conducted",
        "status",
        "repeat_approved",
    ],
    "results": ["register_number", "course_code", "attempt", "outcome", "verified"],
    "faculty": [
        "staff_number",
        "name",
        "department",
        "qualification",
        "specialisation",
        "lab_competent",
        "eligible_course_ids",
    ],
    "workload": ["staff_number", "maximum_hours", "other_hours"],
    "offerings": [
        "regulation",
        "programme",
        "course_code",
        "staff_number",
        "section",
        "capacity",
        "minimum_seats",
        "reserved_seats",
        "room",
        "weekday",
        "start_time",
        "end_time",
        "kind",
        "window",
        "recovery_allowed",
    ],
}
LABELS = {
    "students": "Student master",
    "curriculum": "Regulation curriculum",
    "prerequisites": "Course prerequisites",
    "equivalences": "Course equivalences",
    "attendance": "Attendance records",
    "results": "Examination results",
    "faculty": "Faculty master",
    "workload": "Faculty workload",
    "offerings": "Offerings & timetable",
}
OPTIONAL = {
    "completed_courses",
    "failed_courses",
    "debarred_courses",
    "registered_courses",
    "prerequisites",
    "eligible_course_ids",
    "status",
}


def allow(u, k):
    if k not in FORMATS:
        fail("Unknown import profile.", 404)
    if k == "attendance":
        permit(u, "attendance", "manage")
    elif k == "results":
        permit(u, "results", "manage")
    else:
        permit(u, "manage")


@router.get("/imports")
def profiles(u=Depends(user)):
    items = []
    for k, f in FORMATS.items():
        try:
            allow(u, k)
            items.append({"key": k, "label": LABELS[k], "fields": f})
        except HTTPException:
            pass
    return {"items": items}


@router.get("/templates/{kind}")
def template(kind: str, u=Depends(user)):
    allow(u, kind)
    wb = Workbook()
    ws = wb.active
    ws.title = "Import"
    ws.append(FORMATS[kind])
    ws.freeze_panes = "A2"
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="18294A")
        ws.column_dimensions[c.column_letter].width = 24
    note = wb.create_sheet("Instructions")
    note.append(["DSU-CFCS · " + LABELS[kind]])
    note.append(
        ["Fill the Import sheet only. Use configured master codes, not display names."]
    )
    note.append(
        ["Boolean values: true / false. Separate course codes with semicolons."]
    )
    note.append(["Dates: YYYY-MM-DD; times: HH:MM; weekday: 1 Monday to 7 Sunday."])
    note.append(
        ["Preview validates every row. Confirmation commits all rows atomically."]
    )
    note.append(
        [
            "Blank attendance status requires a configured threshold; never implies debarment."
        ]
    )
    note.append(
        [
            "Prerequisites in curriculum: semicolon-separated mandatory course codes. Use the prerequisite profile for OR/conditional/recommended groups."
        ]
    )
    note.column_dimensions["A"].width = 115
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="DSU-{kind}-template.xlsx"'
        },
    )


def boolean(v):
    v = str(v).lower().strip()
    if v not in ["true", "false", "yes", "no", "1", "0"]:
        fail("Expected a true or false value.")
    return v in ["true", "yes", "1"]


def tparse(s):
    hour, minute = [int(x) for x in str(s).split(":")[:2]]
    if not 0 <= hour <= 23 or not 0 <= minute < 60:
        fail("Invalid time; use HH:MM.")
    return hour * 60 + minute


def parse_file(file):
    b = file.file.read(UPLOAD_LIMIT_MIB * 1024 * 1024 + 1)
    name = file.filename or ""
    ext = name.lower().rsplit(".", 1)[-1]
    if len(b) > UPLOAD_LIMIT_MIB * 1024 * 1024:
        fail(f"File exceeds the {UPLOAD_LIMIT_MIB} MiB upload limit.")
    if ext not in ["xlsx", "csv"]:
        fail("Upload an XLSX workbook or UTF-8 CSV file.")
    if ext == "xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                if (
                    len(z.infolist()) > 300
                    or sum(x.file_size for x in z.infolist()) > 24 * 1024 * 1024
                ):
                    fail("Workbook expansion exceeds safe bounds.")
                if any("vbaProject" in x.filename for x in z.infolist()):
                    fail("Macro-enabled workbooks are not accepted.")
            wb = load_workbook(
                io.BytesIO(b), read_only=True, data_only=False, keep_links=False
            )
            ws = wb.worksheets[0]
            if (ws.max_row or 0) > 2001 or (ws.max_column or 0) > 64:
                fail("Maximum 2,000 rows and 64 columns per batch.")
            values = []
            for row in ws.iter_rows():
                if any(c.data_type == "f" for c in row):
                    fail("Formulas are not accepted. Paste values only.")
                values.append(
                    [str(c.value).strip() if c.value is not None else "" for c in row]
                )
                if len(values) > 2001:
                    fail("Maximum 2,000 rows per batch.")
            wb.close()
        except HTTPException:
            raise
        except Exception:
            fail("This is not a readable XLSX workbook.")
    else:
        try:
            values = list(csv.reader(io.StringIO(b.decode("utf-8-sig"))))
        except (ValueError, UnicodeError, csv.Error):
            fail("Invalid UTF-8 CSV file.")
    if not values or len(values) < 2:
        fail("No data rows found. Add records below the header.")
    if len(values) > 2001:
        fail("Maximum 2,000 rows per batch.")
    headers = [h.strip().lower() for h in values.pop(0)]
    if len(headers) != len(set(headers)) or len(headers) > 64:
        fail("Duplicate or excessive column headers.")
    for i, row in enumerate(values):
        if len(row) > len(headers) or any(len(str(v)) > 2000 for v in row):
            fail(f"Row {i + 2} has extra columns or an oversized field.")
    return headers, [
        dict(zip(headers, row + [""] * max(0, len(headers) - len(row))))
        for row in values
    ]


def ref(db, table, col, value, extra="", args=()):
    id = db.val(
        f"SELECT id FROM {table} WHERE {col}=%s " + extra, (value,) + tuple(args)
    )
    if not id:
        fail(f"Unmapped {table.replace('_', ' ')}: {value}.")
    return id


def split(s):
    return [x.strip() for x in str(s or "").split(";") if x.strip()]


def close_attempt(db, u, sid, cid, attempt, outcome):
    from .engine import promote

    for e in db.all(
        "SELECT * FROM course_enrolments WHERE student_id=%s AND course_id=%s AND attempt=%s AND status='Registered' FOR UPDATE",
        (sid, cid, attempt),
    ):
        state = "Completed" if outcome == "Passed" else outcome
        db.run(
            "UPDATE course_enrolments SET status=%s,changed_at=now() WHERE id=%s",
            (state, e["id"]),
        )
        db.audit(
            u,
            "Finalised course attempt",
            "course_enrolments",
            e["id"],
            {"attempt": attempt, "outcome": outcome},
        )
        promote(db, u, e["section_id"])


def apply_row(db, u, k, r, overwrite=False):
    missing = [x for x in FORMATS[k] if x not in OPTIONAL and not r.get(x, "").strip()]
    if missing:
        fail("Missing value: " + ", ".join(missing) + ".")
    y = year_id(db)
    rid = pr = cid = None
    if "regulation" in r:
        rid = ref(db, "regulations", "code", r["regulation"], "AND deleted_at IS NULL")
        pr = ref(db, "programmes", "code", r["programme"], "AND deleted_at IS NULL")

    def course(code, reg=None, prog=None):
        return ref(
            db,
            "courses",
            "code",
            code,
            "AND regulation_id=%s AND programme_id=%s AND deleted_at IS NULL",
            (reg or rid, prog or pr),
        )

    def put(table, keys, vals):
        old = db.one(
            "SELECT * FROM "
            + table
            + " WHERE "
            + " AND ".join(x + "=%s" for x in keys),
            list(keys.values()),
        )
        if old:
            if not overwrite:
                fail(
                    "Duplicate existing "
                    + table.replace("_", " ")
                    + " record. Enable reviewed updates to amend it.",
                    409,
                )
            if table == "students" and (
                old["regulation_id"] != vals.get("regulation_id")
                or old["programme_id"] != vals.get("programme_id")
            ):
                fail(
                    "Conflicting regulation/programme entry. Approved migration is required."
                )
            if (
                table == "courses"
                and (
                    Decimal(str(vals["credits"])) != old["credits"]
                    or int(vals["assessment_id"]) != old["assessment_id"]
                )
                and db.val(
                    "SELECT EXISTS(SELECT 1 FROM course_enrolments WHERE course_id=%s UNION ALL SELECT 1 FROM student_course_results WHERE course_id=%s)",
                    (old["id"], old["id"]),
                )
            ):
                fail(
                    "Historic credit or assessment values cannot be overwritten; version the curriculum."
                )
            db.run(
                "UPDATE "
                + table
                + " SET "
                + ",".join(x + "=%s" for x in vals)
                + " WHERE id=%s",
                list(vals.values()) + [old["id"]],
            )
            db.audit(
                u,
                "Imported correction",
                table,
                old["id"],
                {"before": old, "after": vals},
            )
            return old["id"]
        return db.insert(table, **keys, **vals)

    if k == "curriculum":
        sem = ref(db, "curriculum_semesters", "number", int(r["semester"]))
        cat = ref(db, "course_categories", "code", r["category"])
        ass = ref(
            db,
            "assessment_schemes",
            "name",
            r["assessment"],
            "AND regulation_id=%s",
            (rid,),
        )
        credits = Decimal(r["credits"])
        if not credits.is_finite() or credits <= 0:
            fail("Invalid credit value: credits must be positive.")
        id = put(
            "courses",
            dict(code=r["course_code"], regulation_id=rid, programme_id=pr),
            dict(
                title=r["title"],
                semester_id=sem,
                category_id=cat,
                credits=credits,
                lecture=int(r["lecture"]),
                tutorial=int(r["tutorial"]),
                practical=int(r["practical"]),
                assessment_id=ass,
                approved=boolean(r["approved"]),
            ),
        )
        for code in split(r["prerequisites"]):
            pre = course(code)
            if pre == id:
                fail("A course cannot be its own prerequisite.")
            group = db.val(
                "INSERT INTO course_prerequisite_groups(course_id,name,operator,kind) VALUES(%s,'Required foundations','AND','Mandatory') ON CONFLICT(course_id,name) DO UPDATE SET operator='AND' RETURNING id",
                (id,),
            )
            db.run(
                "INSERT INTO course_prerequisites(group_id,prerequisite_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (group, pre),
            )
            if db.val(
                "WITH RECURSIVE chain(id) AS (SELECT %s::bigint UNION SELECT p.prerequisite_id FROM chain x JOIN course_prerequisite_groups g ON g.course_id=x.id JOIN course_prerequisites p ON p.group_id=g.id) SELECT EXISTS(SELECT 1 FROM chain WHERE id=%s)",
                (pre, id),
            ):
                fail("Cyclic prerequisite dependency.")
    elif k == "students":
        if (
            db.val(
                "SELECT d.code FROM programmes p JOIN departments d ON d.id=p.department_id WHERE p.id=%s",
                (pr,),
            )
            != r["department"]
        ):
            fail("Programme and department do not match.")
        sem = ref(db, "curriculum_semesters", "number", int(r["semester"]))
        batch = ref(db, "admission_batches", "year", int(r["admission_year"]))
        status = r["status"] or "Active"
        if int(r["semester"]) > db.val(
            "SELECT semesters FROM programmes WHERE id=%s", (pr,)
        ):
            fail("Student semester exceeds programme duration.")
        sid = put(
            "students",
            {"register_number": r["register_number"]},
            dict(
                name=r["name"],
                programme_id=pr,
                regulation_id=rid,
                semester_id=sem,
                batch_id=batch,
                status=status,
            ),
        )
        db.run(
            "INSERT INTO student_academic_records(student_id,cgpa,verified_at) VALUES(%s,%s,now()) ON CONFLICT(student_id) DO UPDATE SET cgpa=EXCLUDED.cgpa,verified_at=now()",
            (sid, Decimal(r["cgpa"])),
        )
        touched = set()
        for key, state in [
            ("completed_courses", "Passed"),
            ("failed_courses", "Failed"),
            ("debarred_courses", "Debarred"),
        ]:
            for code in split(r[key]):
                cid = course(code)
                if cid in touched:
                    fail("Conflicting course outcomes: " + code)
                touched.add(cid)
                put(
                    "student_course_results",
                    dict(student_id=sid, course_id=cid, attempt=1),
                    dict(outcome=state, verified=True),
                )
        from .engine import credit_audit

        s = get_student(db, u, sid)
        if credit_audit(db, s)["earned"] != Decimal(r["earned_credits"]):
            fail("Earned credits do not reconcile with verified course results.")
        for code in split(r["registered_courses"]):
            submit(db, u, s, course(code), None, "Auto", True)
    elif k == "prerequisites":
        cid = course(r["course_code"])
        pre = course(r["prerequisite_code"])
        if pre == cid:
            fail("Self-prerequisite is not allowed.")
        if r["operator"] not in ["AND", "OR"] or r["kind"] not in [
            "Mandatory",
            "Conditional",
            "Recommended",
        ]:
            fail("Invalid prerequisite kind or group operator.")
        g = db.one(
            "SELECT * FROM course_prerequisite_groups WHERE course_id=%s AND name=%s",
            (cid, r["group"]),
        )
        if g and (g["kind"] != r["kind"] or g["operator"] != r["operator"]):
            fail("Conflicting prerequisite group definition.")
        gid = (
            g["id"]
            if g
            else db.insert(
                "course_prerequisite_groups",
                course_id=cid,
                name=r["group"],
                kind=r["kind"],
                operator=r["operator"],
            )
        )
        db.insert("course_prerequisites", group_id=gid, prerequisite_id=pre)
        if db.val(
            """WITH RECURSIVE chain(id) AS (SELECT %s::bigint UNION SELECT p.prerequisite_id FROM chain x JOIN course_prerequisite_groups g ON g.course_id=x.id JOIN course_prerequisites p ON p.group_id=g.id) SELECT EXISTS(SELECT 1 FROM chain WHERE id=%s)""",
            (pre, cid),
        ):
            fail("Cyclic prerequisite dependency.")
    elif k == "equivalences":
        a = course(r["course_code"])
        b = course(
            r["target_code"],
            ref(db, "regulations", "code", r["target_regulation"]),
            ref(db, "programmes", "code", r["target_programme"]),
        )
        a, b = sorted([a, b])
        put(
            "course_equivalences",
            dict(course_a=a, course_b=b),
            dict(
                valid_from=date.fromisoformat(r["valid_from"][:10]),
                valid_until=date.fromisoformat(r["valid_until"][:10]),
                approved=boolean(r["approved"]),
                recognise_credit=boolean(r["recognise_credit"]),
                approved_by=u["id"],
            ),
        )
    elif k in ["results", "attendance"]:
        sid = ref(db, "students", "register_number", r["register_number"])
        s = get_student(db, u, sid)
        rid = s["regulation_id"]
        pr = s["programme_id"]
        cid = course(r["course_code"])
        if k == "results":
            if r["outcome"] not in ["Passed", "Failed", "Debarred"]:
                fail("Unknown examination outcome.")
            if r["outcome"] == "Passed" and db.val(
                "SELECT EXISTS(SELECT 1 FROM attendance_records WHERE student_id=%s AND course_id=%s AND status='Debarred' AND attempt=%s)",
                (sid, cid, int(r["attempt"])),
            ):
                fail(
                    "Credits cannot be awarded to an attendance-debarred attempt. Resolve the verified repeat attempt first."
                )
            put(
                "student_course_results",
                dict(student_id=sid, course_id=cid, attempt=int(r["attempt"])),
                dict(outcome=r["outcome"], verified=boolean(r["verified"])),
            )
            if boolean(r["verified"]):
                close_attempt(db, u, sid, cid, int(r["attempt"]), r["outcome"])
        else:
            if u["role"] == "Faculty" and not db.val(
                "SELECT count(*) FROM course_enrolments e JOIN course_sections z ON z.id=e.section_id WHERE student_id=%s AND course_id=%s AND faculty_id=%s AND status='Registered'",
                (sid, cid, u["faculty_id"]),
            ):
                fail("Faculty may record only their allocated course attendance.", 403)
            attended = int(r["attended"])
            conducted = int(r["conducted"])
            status = r["status"]
            if conducted <= 0 or not 0 <= attended <= conducted:
                fail("Invalid attended/conducted counts.")
            threshold = db.val(
                "SELECT attendance_threshold FROM regulations WHERE id=%s", (rid,)
            )
            if (
                status == "Eligible"
                and threshold is not None
                and Decimal(100 * attended) / conducted < threshold
            ):
                fail(
                    "Attendance is below the configured regulation threshold. An authorised exception is required; ordinary eligibility cannot relax the threshold."
                )
            if not status:
                threshold = db.val(
                    "SELECT attendance_threshold FROM regulations WHERE id=%s", (rid,)
                )
                if threshold is None:
                    fail(
                        "No attendance threshold configured. Supply an authorised course-wise status."
                    )
                status = (
                    "Eligible"
                    if Decimal(100 * attended) / conducted >= threshold
                    else "Shortage"
                )
            if boolean(r["repeat_approved"]) and u["role"] not in [
                "Examination Cell",
                "Super Administrator",
            ]:
                fail("Repeat approval requires Examination Cell authority.", 403)
            if status in ["Debarred", "Approved exception"] and u["role"] not in [
                "Super Administrator",
                "Examination Cell",
            ]:
                fail(
                    "Debarment and exceptions require Examination Cell authority.", 403
                )
            if status == "Approved exception" and not db.val(
                "SELECT id FROM academic_exceptions WHERE student_id=%s AND course_id=%s AND kind='Attendance exception' AND status='Approved' AND academic_year_id=%s",
                (sid, cid, y),
            ):
                fail("An approved attendance exception is required.")
            attempt = int(r.get("attempt") or 1)
            put(
                "attendance_records",
                dict(
                    student_id=sid, course_id=cid, academic_year_id=y, attempt=attempt
                ),
                dict(
                    attended=attended,
                    conducted=conducted,
                    status=status,
                    repeat_approved=boolean(r["repeat_approved"]),
                ),
            )
            if status == "Debarred":
                old = db.one(
                    "SELECT * FROM student_course_results WHERE student_id=%s AND course_id=%s AND attempt=%s",
                    (sid, cid, attempt),
                )
                if old and old["outcome"] != "Debarred":
                    if not overwrite:
                        fail(
                            "Debarment changes an existing result. Enable reviewed updates and confirm the correction."
                        )
                    db.run(
                        "UPDATE student_course_results SET outcome='Debarred',verified=true WHERE id=%s",
                        (old["id"],),
                    )
                    db.audit(
                        u,
                        "Corrected debarred attempt",
                        "student_course_results",
                        old["id"],
                        {"prior_outcome": old["outcome"], "attempt": attempt},
                    )
                elif not old:
                    db.insert(
                        "student_course_results",
                        student_id=sid,
                        course_id=cid,
                        attempt=attempt,
                        outcome="Debarred",
                        verified=True,
                    )
                close_attempt(db, u, sid, cid, attempt, "Debarred")
    elif k == "faculty":
        d = ref(db, "departments", "code", r["department"])
        f = put(
            "faculty",
            dict(staff_number=r["staff_number"]),
            dict(
                name=r["name"],
                department_id=d,
                specialisation=r["specialisation"],
                lab_competent=boolean(r["lab_competent"]),
            ),
        )
        db.run(
            "INSERT INTO faculty_qualifications(faculty_id,qualification) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (f, r["qualification"]),
        )
        for cid in split(r["eligible_course_ids"]):
            db.run(
                "INSERT INTO faculty_course_competencies VALUES(%s,%s,true) ON CONFLICT DO NOTHING",
                (f, int(cid)),
            )
    elif k == "workload":
        put(
            "faculty_workloads",
            dict(
                faculty_id=ref(db, "faculty", "staff_number", r["staff_number"]),
                academic_year_id=y,
            ),
            dict(
                maximum_hours=Decimal(r["maximum_hours"]),
                other_hours=Decimal(r["other_hours"]),
            ),
        )
    elif k == "offerings":
        from .api import Offer, Slot, create_offer

        c = course(r["course_code"])
        f = ref(db, "faculty", "staff_number", r["staff_number"])
        room = ref(db, "classrooms", "code", r["room"])
        w = ref(db, "registration_windows", "name", r["window"])
        slot = Slot(
            room_id=room,
            weekday=int(r["weekday"]),
            starts=tparse(r["start_time"]),
            ends=tparse(r["end_time"]),
            kind=r["kind"],
        )
        create_offer(
            Offer(
                course_id=c,
                faculty_id=f,
                section=r["section"],
                capacity=int(r["capacity"]),
                minimum_seats=int(r["minimum_seats"]),
                reserved_seats=int(r["reserved_seats"]),
                window_id=w,
                recovery_allowed=boolean(r["recovery_allowed"]),
                slots=[slot],
                confirm=True,
            ),
            db,
            u,
        )


class RollbackPreview(Exception):
    pass


def validate_rows(db, u, k, rows, overwrite=False, commit=False):
    errors = []
    seen = set()
    duplicate = 0
    try:
        with db.c.transaction():
            for i, r in enumerate(rows):
                key = tuple(sorted(r.items()))
                if key in seen:
                    errors.append(
                        {"row": i + 2, "message": "Duplicate row within this file."}
                    )
                    duplicate += 1
                    continue
                seen.add(key)
                try:
                    with db.c.transaction():
                        apply_row(db, u, k, r, overwrite)
                except (
                    HTTPException,
                    ValueError,
                    ArithmeticError,
                    psycopg.Error,
                    KeyError,
                ) as e:
                    msg = (
                        e.detail
                        if isinstance(e, HTTPException)
                        else (
                            "Duplicate record."
                            if isinstance(e, psycopg.errors.UniqueViolation)
                            else (
                                "A foreign key or academic constraint failed."
                                if isinstance(e, psycopg.Error)
                                else "Invalid field value: " + str(e)[:160]
                            )
                        )
                    )
                    duplicate += int("duplicate" in msg.lower())
                    errors.append({"row": i + 2, "message": msg})
            if not commit or errors:
                raise RollbackPreview()
    except RollbackPreview:
        pass
    return {
        "total": len(rows),
        "valid": len(rows) - len(errors),
        "failed": len(errors),
        "duplicates": duplicate,
        "missing": sum("Missing value" in e["message"] for e in errors),
        "errors": errors,
    }


@router.post("/imports/{kind}/preview")
def preview(
    kind: str,
    file: UploadFile = File(...),
    overwrite: bool = False,
    db=Depends(get_db),
    u=Depends(user),
):
    allow(u, kind)
    headers, rows = parse_file(file)
    missing = set(FORMATS[kind]) - set(headers)
    if kind == "attendance" and missing == {"attempt"}:
        for r in rows:
            r["attempt"] = "1"
        missing = set()  # v1 attendance sheets refer to the first attempt
    extra = set(headers) - set(FORMATS[kind])
    if missing or extra:
        fail(
            "Header mismatch. Missing: "
            + (", ".join(sorted(missing)) or "none")
            + "; unexpected: "
            + (", ".join(sorted(extra)) or "none")
            + ". Download the current template."
        )
    if not rows:
        fail("No data rows found.")
    summary = validate_rows(db, u, kind, rows, overwrite)
    id = db.insert(
        "import_batches",
        user_id=u["id"],
        kind=kind,
        filename=Path(file.filename or "upload").name[:180],
        payload=Jsonb({"rows": rows, "overwrite": overwrite}),
        summary=Jsonb(summary),
    )
    return {"batch_id": id, **summary, "committed": False}


@router.post("/students/import")
def students_import(file: UploadFile = File(...), db=Depends(get_db), u=Depends(user)):
    return preview("students", file, False, db, u)


@router.post("/curriculum/import")
def curriculum_import(
    file: UploadFile = File(...), db=Depends(get_db), u=Depends(user)
):
    return preview("curriculum", file, False, db, u)


@router.post("/attendance/import")
def attendance_import(
    file: UploadFile = File(...), db=Depends(get_db), u=Depends(user)
):
    return preview("attendance", file, False, db, u)


class Commit(Input):
    confirm: bool


@router.post("/imports/{id}/commit")
def commit_batch(id: int, b: Commit, db=Depends(get_db), u=Depends(user)):
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    batch = db.one(
        "SELECT * FROM import_batches WHERE id=%s AND user_id=%s AND created_at>now()-interval '2 hours' FOR UPDATE",
        (id, u["id"]),
    )
    if not batch:
        fail("Import preview not found, expired or outside your scope.", 404)
    allow(u, batch["kind"])
    if batch["committed"]:
        fail("This batch has already been committed.", 409)
    if batch["summary"]["failed"]:
        fail("Correct invalid rows and preview the file again.", 409)
    result = validate_rows(
        db,
        u,
        batch["kind"],
        batch["payload"]["rows"],
        batch["payload"]["overwrite"],
        True,
    )
    if result["failed"]:
        fail("Revalidation failed. " + result["errors"][0]["message"], 409)
    db.run("UPDATE import_batches SET committed=true WHERE id=%s", (id,))
    db.audit(
        u,
        "Committed verified import",
        "import_batches",
        id,
        {
            "kind": batch["kind"],
            "rows": result["valid"],
            "overwrite": batch["payload"]["overwrite"],
        },
    )
    return {"batch_id": id, **result, "committed": True}
