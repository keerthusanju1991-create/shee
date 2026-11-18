from fastapi import APIRouter, Depends
from pydantic import Field
from .core import *

router = APIRouter()
# Every identifier is allowlisted. No credentials or session storage is exposed here.
MASTERS = {
    "exception_policies": ["regulation_id", "kind", "authority", "active"],
    "schools": ["code", "name"],
    "departments": ["school_id", "code", "name"],
    "programmes": ["department_id", "code", "name", "semesters"],
    "academic_years": ["label", "starts_on", "ends_on"],
    "admission_batches": ["year"],
    "curriculum_semesters": ["number"],
    "regulations": [
        "code",
        "name",
        "active",
        "floating_window",
        "conditional_allowed",
        "attendance_threshold",
        "block_on_shortage",
        "block_unrelated",
        "priority_order",
    ],
    "graduation_requirements": [
        "regulation_id",
        "programme_id",
        "total_credits",
        "mandatory_credits",
        "elective_credits",
    ],
    "credit_limits": [
        "regulation_id",
        "programme_id",
        "academic_year_id",
        "semester_id",
        "category",
        "minimum_credits",
        "maximum_credits",
    ],
    "course_categories": ["code", "name", "elective", "mandatory_for_degree"],
    "assessment_schemes": ["regulation_id", "name", "description"],
    "courses": [
        "code",
        "title",
        "programme_id",
        "regulation_id",
        "semester_id",
        "category_id",
        "credits",
        "lecture",
        "tutorial",
        "practical",
        "assessment_id",
        "approved",
    ],
    "course_equivalences": [
        "course_a",
        "course_b",
        "approved",
        "recognise_credit",
        "valid_from",
        "valid_until",
    ],
    "classrooms": ["code", "capacity"],
    "laboratories": ["classroom_id", "speciality", "capacity"],
    "faculty": [
        "staff_number",
        "name",
        "department_id",
        "specialisation",
        "lab_competent",
    ],
    "faculty_workloads": [
        "faculty_id",
        "academic_year_id",
        "maximum_hours",
        "other_hours",
    ],
    "faculty_availability": ["faculty_id", "weekday", "starts", "ends"],
    "registration_windows": ["name", "opens_at", "closes_at", "academic_year_id"],
    "students": [
        "register_number",
        "name",
        "programme_id",
        "regulation_id",
        "batch_id",
        "semester_id",
        "status",
        "category",
        "advisor_id",
    ],
}
SOFT = {
    "schools",
    "departments",
    "programmes",
    "admission_batches",
    "curriculum_semesters",
    "regulations",
    "course_categories",
    "courses",
    "classrooms",
    "laboratories",
    "faculty",
    "students",
}
POLICY = {
    "exception_policies",
    "regulations",
    "credit_limits",
    "graduation_requirements",
    "course_equivalences",
    "assessment_schemes",
}
OPTIONAL = {
    "category",
    "semester_id",
    "attendance_threshold",
    "advisor_id",
    "priority_order",
}


class Master(Input):
    values: dict
    confirm: bool


@router.get("/admin/masters")
def master_types(u=Depends(user)):
    permit(u, "manage", "policy", "audit")
    return {
        "items": [
            {
                "key": k,
                "fields": v,
                "archivable": k in SOFT,
                "editable": "manage" in u["permissions"]
                or (k in POLICY and "policy" in u["permissions"]),
            }
            for k, v in MASTERS.items()
        ]
    }


@router.get("/admin/masters/{kind}")
def master_rows(
    kind: str, page: int = 1, size: int = 25, db=Depends(get_db), u=Depends(user)
):
    permit(u, "manage", "policy", "audit")
    if kind not in MASTERS:
        fail("Unknown master collection.", 404)
    where = " WHERE deleted_at IS NULL" if kind in SOFT else " WHERE true"
    params = []
    if u["role"] == "Dean – SET":
        if kind == "students":
            sc, params = student_scope(u, "students")
            where += " AND " + sc
        if kind in ["programmes", "faculty"]:
            sc, params = dept_scope(u, "department_id")
            where += " AND " + sc
        if kind == "departments":
            sc, params = dept_scope(u, "id")
            where += " AND " + sc
    return db.page(
        "SELECT id,"
        + ",".join(MASTERS[kind])
        + " FROM "
        + kind
        + where
        + " ORDER BY id",
        params,
        page,
        size,
    )


def save(db, u, kind, b, id=None):
    if kind not in MASTERS:
        fail("Unknown master collection.", 404)
    permit(u, "manage", "policy") if kind in POLICY else permit(u, "manage")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    v = b.values.copy()
    if not v or any(k not in MASTERS[kind] for k in v):
        fail("Unknown or empty master fields.")
    if any(isinstance(x, str) and len(x) > 2000 for x in v.values()):
        fail("A master value is too long.")
    old = None
    if id:
        old = db.one(f"SELECT * FROM {kind} WHERE id=%s FOR UPDATE", (id,))
        if not old:
            fail("Master record not found.", 404)
        if kind == "students" and any(
            k in v and v[k] != old[k]
            for k in ["regulation_id", "programme_id", "register_number"]
        ):
            fail(
                "Identity and curriculum cannot be remapped by an ordinary edit. Use approved migration.",
                409,
            )
        if (
            kind == "courses"
            and db.val(
                "SELECT EXISTS(SELECT 1 FROM course_enrolments WHERE course_id=%s UNION ALL SELECT 1 FROM student_course_results WHERE course_id=%s)",
                (id, id),
            )
            and any(
                k in v and str(v[k]) != str(old[k])
                for k in [
                    "regulation_id",
                    "credits",
                    "programme_id",
                    "semester_id",
                    "assessment_id",
                ]
            )
        ):
            fail(
                "Version the curriculum instead of changing historic academic recognition.",
                409,
            )
    final = {**(old or {}), **v}
    if kind in ["courses", "students"] and not db.val(
        "SELECT p.id FROM programmes p JOIN curriculum_semesters cs ON cs.id=%s WHERE p.id=%s AND cs.number<=p.semesters",
        (final.get("semester_id"), final.get("programme_id")),
    ):
        fail("Curriculum semester exceeds the programme duration.")
    if kind == "exception_policies" and final.get("authority") not in [
        "Super Administrator",
        "Registrar",
        "Dean – SET",
        "HoD",
        "Faculty Advisor",
        "Examination Cell",
    ]:
        fail("Select an authorised academic review role.")
    if kind == "regulations":
        if "priority_order" in v and (
            not isinstance(v["priority_order"], list)
            or sorted(v["priority_order"]) != ["P1", "P2", "P3", "P4", "P5"]
        ):
            fail("Priority order must include P1 through P5 exactly once.")
        v.update(approved_by=u["id"], approved_at=datetime.now().astimezone())
    if kind == "course_equivalences":
        v["approved_by"] = u["id"]
        if final.get("course_a", 0) > final.get("course_b", 0):
            v["course_a"], v["course_b"] = final["course_b"], final["course_a"]
    if kind == "courses" and not db.val(
        "SELECT id FROM assessment_schemes WHERE id=%s AND regulation_id=%s",
        (final.get("assessment_id"), final.get("regulation_id")),
    ):
        fail("Assessment scheme must belong to the same regulation.")
    if kind == "graduation_requirements" and float(
        final.get("mandatory_credits", 0)
    ) + float(final.get("elective_credits", 0)) > float(final.get("total_credits", 0)):
        fail("Category requirements exceed the degree total.")
    if id:
        db.run(
            f"UPDATE {kind} SET " + ",".join(k + "=%s" for k in v) + " WHERE id=%s",
            list(v.values()) + [id],
        )
    else:
        id = db.insert(kind, **v)
    db.audit(
        u,
        "Updated master" if old else "Created master",
        kind,
        id,
        {"before": old, "after": v},
    )
    return {"id": id, "status": "Saved"}


@router.post("/admin/masters/{kind}")
def create(kind: str, b: Master, db=Depends(get_db), u=Depends(user)):
    return save(db, u, kind, b)


@router.put("/admin/masters/{kind}/{id}")
def update(kind: str, id: int, b: Master, db=Depends(get_db), u=Depends(user)):
    return save(db, u, kind, b, id)


@router.post("/regulations")
def reg_create(b: Master, db=Depends(get_db), u=Depends(user)):
    return save(db, u, "regulations", b)


@router.post("/courses")
def course_create(b: Master, db=Depends(get_db), u=Depends(user)):
    return save(db, u, "courses", b)


class Archive(Input):
    confirm: bool
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/admin/masters/{kind}/{id}/archive")
def archive(kind: str, id: int, b: Archive, db=Depends(get_db), u=Depends(user)):
    permit(u, "manage")
    confirmed(b.confirm)
    if kind not in SOFT:
        fail("This collection does not support archival.")
    from psycopg import sql

    for fk in db.all(
        "SELECT c.conrelid::regclass::text AS child,a.attname AS col FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1] WHERE c.contype='f' AND c.confrelid=to_regclass(%s)",
        (kind,),
    ):
        if db.val(
            sql.SQL("SELECT EXISTS(SELECT 1 FROM {} WHERE {}=%s)").format(
                sql.Identifier(fk["child"].split(".")[-1]), sql.Identifier(fk["col"])
            ),
            (id,),
        ):
            fail(
                "This master record has dependent academic history. Deactivate the policy or create a new version instead.",
                409,
            )
    if not db.one(
        f"UPDATE {kind} SET deleted_at=now() WHERE id=%s AND deleted_at IS NULL RETURNING id",
        (id,),
    ):
        fail("Record not found.", 404)
    db.audit(u, "Archived master", kind, id, {"reason": b.reason})
    return {"status": "Archived"}


class Migration(Input):
    student_id: int
    regulation_id: int
    reason: str = Field(min_length=12, max_length=1500)
    confirm: bool


@router.post("/admin/migrations")
def migrate(b: Migration, db=Depends(get_db), u=Depends(user)):
    permit(u, "migrate")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    s = get_student(db, u, b.student_id)
    if s["regulation_id"] == b.regulation_id:
        fail("Already under this regulation.")
    if not db.val(
        "SELECT id FROM regulations WHERE id=%s AND active AND deleted_at IS NULL",
        (b.regulation_id,),
    ):
        fail("Target regulation is not active.")
    if db.val(
        "SELECT count(*) FROM course_enrolments WHERE student_id=%s AND status IN('Registered','Pending approval','Waitlisted')",
        (s["id"],),
    ):
        fail("Resolve active registrations before migration.", 409)
    from .engine import recognised

    for row in db.all(
        "SELECT DISTINCT course_id FROM student_course_results WHERE student_id=%s AND verified AND outcome='Passed'",
        (s["id"],),
    ):
        if not db.val(
            "SELECT id FROM courses WHERE id=ANY(%s) AND regulation_id=%s AND programme_id=%s AND approved LIMIT 1",
            (
                list(recognised(db, row["course_id"])),
                b.regulation_id,
                s["programme_id"],
            ),
        ):
            fail("Approved credit recognition is missing for a completed course.", 409)
    mid = db.insert(
        "regulation_migrations",
        student_id=s["id"],
        from_regulation=s["regulation_id"],
        to_regulation=b.regulation_id,
        reason=b.reason,
        approved_by=u["id"],
    )
    db.run(
        "UPDATE students SET regulation_id=%s WHERE id=%s", (b.regulation_id, s["id"])
    )
    db.audit(
        u,
        "Approved regulation migration",
        "regulation_migrations",
        mid,
        {
            "student_id": s["id"],
            "from": s["regulation_id"],
            "to": b.regulation_id,
            "reason": b.reason,
        },
    )
    return {"status": "Migration approved", "id": mid}


class Account(Input):
    username: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=256)
    role: str
    school_id: int | None = None
    department_id: int | None = None
    student_id: int | None = None
    faculty_id: int | None = None
    confirm: bool


@router.post("/admin/users")
def account(b: Account, db=Depends(get_db), u=Depends(user)):
    permit(u, "users")
    confirmed(b.confirm)
    rid = db.val("SELECT id FROM roles WHERE name=%s", (b.role,))
    if not rid:
        fail("Unknown role.")
    if b.role == "Dean – SET" and not b.school_id:
        fail("Select the school managed by this Dean.")
    if (
        b.role in ["HoD", "Student", "Faculty", "Faculty Advisor"]
        and not b.department_id
    ):
        fail("This role requires a department scope.")
    if b.role == "Student" and not db.val(
        "SELECT s.id FROM students s JOIN programmes p ON p.id=s.programme_id WHERE s.id=%s AND p.department_id=%s",
        (b.student_id, b.department_id),
    ):
        fail("Select a student in the assigned department.")
    if b.role in ["Faculty", "Faculty Advisor"] and not db.val(
        "SELECT id FROM faculty WHERE id=%s AND department_id=%s",
        (b.faculty_id, b.department_id),
    ):
        fail("Select a faculty member in the assigned department.")
    uid = db.insert(
        "users",
        **b.model_dump(exclude={"password", "confirm", "role"}),
        password_hash=PH.hash(b.password),
        role_id=rid,
    )
    db.audit(u, "Created user account", "users", uid, {"role": b.role})
    return {"id": uid, "status": "Created"}


class Grant(Input):
    role: str
    permission: str
    grant: bool
    confirm: bool


@router.post("/admin/permissions")
def grant(b: Grant, db=Depends(get_db), u=Depends(user)):
    permit(u, "users")
    confirmed(b.confirm)
    if (
        b.grant
        and b.permission in ["manage", "users", "policy", "migrate", "audit"]
        and b.role not in ["Super Administrator", "Registrar", "Dean – SET"]
    ):
        fail(
            "Institutional administration privileges cannot be delegated to individual or department-scoped accounts.",
            403,
        )
    if b.role == "Super Administrator":
        fail("The recovery administrator role is immutable.")
    rid = db.val("SELECT id FROM roles WHERE name=%s", (b.role,))
    pid = db.val("SELECT id FROM permissions WHERE code=%s", (b.permission,))
    if not rid or not pid:
        fail("Unknown role or permission.")
    if b.grant:
        db.run(
            "INSERT INTO role_permissions VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (rid, pid),
        )
    else:
        db.run(
            "DELETE FROM role_permissions WHERE role_id=%s AND permission_id=%s",
            (rid, pid),
        )
    db.audit(
        u,
        "Changed delegated permission",
        "roles",
        rid,
        b.model_dump(exclude={"confirm"}),
    )
    return {"status": "Permissions saved"}


@router.get("/admin/users")
def accounts(db=Depends(get_db), u=Depends(user)):
    permit(u, "users")
    return {
        "items": db.all(
            "SELECT u.id,u.name,u.username,r.name AS role,u.demo,u.active FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.id"
        )
    }


@router.get("/admin/permissions")
def permissions(db=Depends(get_db), u=Depends(user)):
    permit(u, "users")
    return {
        "roles": db.all("SELECT id,name FROM roles ORDER BY id"),
        "permissions": [
            r["code"] for r in db.all("SELECT code FROM permissions ORDER BY code")
        ],
        "grants": db.all(
            "SELECT r.name AS role,p.code AS permission FROM role_permissions x JOIN roles r ON r.id=x.role_id JOIN permissions p ON p.id=x.permission_id"
        ),
    }


class PendingRequirement(Input):
    course_id: int
    approved: bool
    reason: str = Field(min_length=8, max_length=1500)
    confirm: bool


@router.post("/students/{sid}/pending-requirements")
def pending_requirement(
    sid: int, b: PendingRequirement, db=Depends(get_db), u=Depends(user)
):
    permit(u, "policy")
    confirmed(b.confirm)
    db.run("SELECT pg_advisory_xact_lock(740126)")
    s = get_student(db, u, sid)
    if not db.val(
        "SELECT id FROM courses WHERE id=%s AND approved AND regulation_id=%s AND programme_id=%s",
        (b.course_id, s["regulation_id"], s["programme_id"]),
    ):
        fail(
            "Graduation requirements must belong to the student’s approved curriculum."
        )
    db.run(
        "INSERT INTO pending_requirements(student_id,course_id,approved) VALUES(%s,%s,%s) ON CONFLICT(student_id,course_id) DO UPDATE SET approved=EXCLUDED.approved",
        (sid, b.course_id, b.approved),
    )
    db.audit(
        u,
        "Reviewed graduation requirement",
        "students",
        sid,
        {"course_id": b.course_id, "approved": b.approved, "reason": b.reason},
    )
    return {"status": "Graduation requirement recorded"}
