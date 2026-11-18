"""Identity, scoped data access and transaction boundary. No policy thresholds in code."""

import os, hashlib, secrets, json, math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer

bearer = HTTPBearer(
    auto_error=False,
    description="Use the access_token returned by POST /api/auth/login.",
)
from argon2 import PasswordHasher
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("DATABASE_URL", "dbname=dsu host=/tmp/dsu-socket port=5436")
DEMO = os.getenv("APP_MODE", "production") == "synthetic"
SERVERLESS = os.getenv("DSU_RUNTIME") == "vercel" or os.getenv("VERCEL") == "1"
UPLOAD_LIMIT_MIB = 4 if SERVERLESS else 5
PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DB:
    def __init__(self, conn):
        self.c = conn
        self.read_cache = None

    def all(self, sql, params=()):
        if self.read_cache is None:
            return self.c.execute(sql, params).fetchall()
        key = ("all", sql, repr(params))
        if key not in self.read_cache:
            self.read_cache[key] = self.c.execute(sql, params).fetchall()
        return self.read_cache[key]

    def one(self, sql, params=()):
        if self.read_cache is None:
            return self.c.execute(sql, params).fetchone()
        key = ("one", sql, repr(params))
        if key not in self.read_cache:
            self.read_cache[key] = self.c.execute(sql, params).fetchone()
        return self.read_cache[key]

    def val(self, sql, params=()):
        r = self.one(sql, params)
        return next(iter(r.values())) if r else None

    def run(self, sql, params=()):
        return self.c.execute(sql, params)

    def insert(self, table, **values):
        # Table/column identifiers are drawn only from server-side allowlists.
        return self.val(
            f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join(['%s'] * len(values))}) RETURNING id",
            tuple(values.values()),
        )

    def audit(self, u, action, entity, eid=None, detail=None):
        self.run(
            "INSERT INTO academic_audit_logs(actor_id,action,entity,entity_id,detail) VALUES(%s,%s,%s,%s,%s)",
            (
                u["id"] if u else None,
                action,
                entity,
                eid,
                Jsonb(detail or {}, dumps=lambda v: json.dumps(v, default=str)),
            ),
        )

    def page(self, sql, params=(), page=1, size=25):
        size = max(1, min(int(size), 100))
        page = max(1, int(page))
        total = self.val("SELECT count(*) FROM (" + sql + ") z", params)
        return dict(
            items=self.all(
                sql + " LIMIT %s OFFSET %s", tuple(params) + (size, (page - 1) * size)
            ),
            page=page,
            size=size,
            total=total,
        )


def connect():
    if SERVERLESS and not os.getenv("DATABASE_URL"):
        raise RuntimeError("Configure the server-side DATABASE_URL for this deployment environment.")
    # Some transaction poolers do not carry named prepared statements between transactions.
    options = {"prepare_threshold": None} if SERVERLESS else {}
    return psycopg.connect(DSN, row_factory=dict_row, connect_timeout=10, **options)


def get_db():
    with connect() as c:
        yield DB(c)


def fail(msg, status=422):
    raise HTTPException(status, detail=msg)


def confirmed(yes):
    if yes is not True:
        fail("Explicit confirmation is required for this academic change.")


def request_session_digest(request: Request) -> str:
    """Resolve one opaque session; never infer identity from a role or a missing token.

    The browser uses an application-specific header because embedding gateways
    may consume the standard Authorization header for their own authentication.
    API clients retain the existing Authorization: Bearer contract.
    """
    token = request.headers.get("x-cfcs-session")
    if token is None:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        token = value if scheme.lower() == "bearer" else ""
    if not token or len(token) > 256:
        fail("Please sign in to your academic workspace.", 401)
    return hashlib.sha256(token.encode()).hexdigest()


def user(request: Request, db: DB = Depends(get_db), credentials=Depends(bearer)):
    token = request_session_digest(request)
    u = db.one(
        """SELECT u.id,u.name,u.username,u.role_id,r.name AS role,u.department_id,u.school_id,u.student_id,u.faculty_id,u.demo FROM sessions s JOIN users u ON u.id=s.user_id JOIN roles r ON r.id=u.role_id WHERE s.token_hash=%s AND s.expires_at>now() AND u.active""",
        (token,),
    )
    if not u or (u["demo"] and not DEMO):
        fail("Your session has expired. Sign in again.", 401)
    if u["role"] == "Dean – SET":
        u["department_scope"] = [
            r["id"]
            for r in db.all(
                "SELECT id FROM departments WHERE school_id=%s AND deleted_at IS NULL",
                (u["school_id"],),
            )
        ]
    u["permissions"] = [
        x["code"]
        for x in db.all(
            "SELECT code FROM permissions p JOIN role_permissions rp ON rp.permission_id=p.id WHERE rp.role_id=%s",
            (u["role_id"],),
        )
    ]
    return u


def permit(u, *permissions):
    if not any(p in u["permissions"] for p in permissions):
        fail("Your role is not authorised for this action.", 403)


def student_scope(u, alias="s"):
    if u["role"] == "Dean – SET":
        return (
            f"{alias}.programme_id IN(SELECT id FROM programmes WHERE department_id=ANY(%s))",
            [u.get("department_scope", [])],
        )
    if u["role"] == "Student":
        return f"{alias}.id=%s", [u["student_id"]]
    if u["role"] == "Faculty Advisor":
        return f"{alias}.advisor_id=%s", [u["faculty_id"]]
    if u["role"] == "Faculty":
        return (
            f"EXISTS(SELECT 1 FROM course_enrolments se JOIN course_sections z ON z.id=se.section_id WHERE se.student_id={alias}.id AND z.faculty_id=%s AND se.status='Registered')",
            [u["faculty_id"]],
        )
    if u["role"] == "HoD":
        return (
            f"{alias}.programme_id IN(SELECT id FROM programmes WHERE department_id=%s)",
            [u["department_id"]],
        )
    return "TRUE", []


def dept_scope(u, col):
    if u["role"] == "Dean – SET":
        return f"{col}=ANY(%s)", [u.get("department_scope", [])]
    if u["role"] in ["HoD", "Faculty Advisor", "Student", "Faculty"]:
        return f"{col}=%s", [u["department_id"]]
    return "TRUE", []


def check_dept(u, id):
    if u["role"] == "Dean – SET" and id not in u.get("department_scope", []):
        fail("Outside your school scope.", 403)
    if (
        u["role"] in ["HoD", "Faculty Advisor", "Faculty", "Student"]
        and u["department_id"] != id
    ):
        fail("Outside your department scope.", 403)


def get_student(db, u, sid):
    scope, p = student_scope(u)
    s = db.one(
        """SELECT s.*,p.department_id,p.name AS programme,p.code AS programme_code,p.semesters,d.name AS department,d.code AS dept_code,r.code AS regulation,r.active AS regulation_active,r.conditional_allowed,r.floating_window,r.block_on_shortage,r.block_unrelated,cs.number AS semester,b.year AS admission_year,ar.cgpa FROM students s JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN regulations r ON r.id=s.regulation_id JOIN curriculum_semesters cs ON cs.id=s.semester_id JOIN admission_batches b ON b.id=s.batch_id LEFT JOIN student_academic_records ar ON ar.student_id=s.id WHERE s.deleted_at IS NULL AND s.id=%s AND """
        + scope,
        [sid] + p,
    )
    if not s:
        fail("Student not found in your authorised scope.", 404)
    return s


def year_id(db):
    y = db.val(
        "SELECT id FROM academic_years WHERE current_date BETWEEN starts_on AND ends_on ORDER BY starts_on DESC LIMIT 1"
    )
    if not y:
        fail("No academic year is configured for the current date.")
    return y


def session(db, u):
    token = secrets.token_urlsafe(40)
    db.run(
        "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(%s,%s,now()+interval '4 hours')",
        (hashlib.sha256(token.encode()).hexdigest(), u),
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 14400}


PERM = {
    "Super Administrator": [
        "manage",
        "policy",
        "import",
        "records",
        "publish",
        "assign",
        "register",
        "review",
        "report",
        "attendance",
        "results",
        "audit",
        "preferences",
        "requests",
        "users",
        "migrate",
    ],
    "Registrar": [
        "policy",
        "records",
        "review",
        "report",
        "audit",
        "requests",
        "migrate",
    ],
    "Dean – SET": [
        "records",
        "publish",
        "assign",
        "review",
        "report",
        "audit",
        "requests",
    ],
    "HoD": [
        "records",
        "publish",
        "assign",
        "review",
        "report",
        "attendance",
        "requests",
    ],
    "Faculty Advisor": ["records", "review", "report", "requests"],
    "Faculty": ["attendance", "report"],
    "Student": ["register", "preferences", "requests", "report"],
    "Examination Cell": [
        "records",
        "results",
        "attendance",
        "import",
        "report",
        "review",
        "requests",
    ],
}
ROLES = list(PERM)


def setup_roles(db):
    for code in sorted(set(sum(PERM.values(), []))):
        db.run(
            "INSERT INTO permissions(code) VALUES(%s) ON CONFLICT DO NOTHING", (code,)
        )
    for r, pp in PERM.items():
        rid = db.val(
            "INSERT INTO roles(name) VALUES(%s) ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name RETURNING id",
            (r,),
        )
        for p in pp:
            db.run(
                "INSERT INTO role_permissions SELECT %s,id FROM permissions WHERE code=%s ON CONFLICT DO NOTHING",
                (rid, p),
            )
