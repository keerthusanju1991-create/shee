"""Explicit SYNTHETIC fixtures only. Never run this against an institutional database."""

import random, itertools, os, sys
from datetime import date, datetime, timedelta, timezone
from .core import *

NAMES = [
    "Aarav Srinivas",
    "Ananya Krishnan",
    "Aditya Narayanan",
    "Meera Venkatesh",
    "Kavin Rajan",
    "Nila Raman",
    "Ishaan Kumar",
    "Diya Suresh",
    "Varun Ramesh",
    "Keerthana Mani",
    "Sai Arjun",
    "Harini S",
    "Vishal Mohan",
    "Aishwarya Ravi",
    "Pranav Iyer",
    "Sahana Devi",
    "Arun Prakash",
    "Janani Bharath",
    "Rohan Kumar",
    "Divya Nair",
    "Karthik Selvan",
    "Anika Saran",
    "Gautham N",
    "Priya Anand",
]
DEPTS = [
    ("CSE", "Computer Science & Engineering", 324),
    ("ECE", "Electronics & Communication", 258),
    ("MECH", "Mechanical Engineering", 216),
    ("EEE", "Electrical & Electronics", 196),
    ("CIVIL", "Civil Engineering", 154),
    ("AIDS", "Artificial Intelligence & Data Science", 100),
]
TITLES = [
    [
        "Computing Fundamentals",
        "Discrete Mathematics",
        "Data Structures",
        "Database Systems",
        "Machine Learning",
        "Computer Networks",
        "Distributed Systems",
        "Computing Capstone",
    ],
    [
        "Circuit Theory",
        "Digital Electronics",
        "Signals & Systems",
        "Communication Systems",
        "Embedded Systems",
        "Wireless Networks",
        "VLSI Design",
        "Electronics Capstone",
    ],
    [
        "Engineering Mechanics",
        "Materials Science",
        "Thermodynamics",
        "Fluid Mechanics",
        "Machine Design",
        "Heat Transfer",
        "Industrial Robotics",
        "Mechanical Capstone",
    ],
    [
        "Electrical Fundamentals",
        "Network Analysis",
        "Electrical Machines",
        "Power Electronics",
        "Control Systems",
        "Power Systems",
        "Smart Grids",
        "Electrical Capstone",
    ],
    [
        "Engineering Surveying",
        "Construction Materials",
        "Structural Mechanics",
        "Soil Mechanics",
        "Structural Design",
        "Transportation Systems",
        "Sustainable Construction",
        "Civil Capstone",
    ],
    [
        "Programming Foundations",
        "Applied Statistics",
        "Data Analytics",
        "Data Engineering",
        "Machine Learning",
        "Deep Learning",
        "Natural Language Processing",
        "AI Capstone",
    ],
]


def seed(db):
    if not DEMO:
        raise RuntimeError("Synthetic seed requires APP_MODE=synthetic.")
    if db.val("SELECT count(*) FROM schools"):
        raise RuntimeError("Refusing to seed a non-empty installation.")
    rng = random.Random(426)
    now = datetime.now(timezone.utc)
    school = db.insert("schools", code="SET", name="School of Engineering & Technology")
    db.run(
        "INSERT INTO workspace_metadata VALUES('dataset','synthetic'),('institution','Dhanalakshmi Srinivasan University')"
    )
    ay = db.insert(
        "academic_years",
        label="2026–2027",
        starts_on=date(2026, 6, 1),
        ends_on=date(2027, 5, 31),
    )
    win = db.insert(
        "registration_windows",
        name="SET · Academic registration",
        academic_year_id=ay,
        opens_at=date(2026, 6, 1),
        closes_at=date(2027, 5, 31),
    )
    batches = {yr: db.insert("admission_batches", year=yr) for yr in range(2022, 2027)}
    sem = {n: db.insert("curriculum_semesters", number=n) for n in range(1, 9)}
    dept = []
    prog = []
    regs = []
    assess = {}
    fac = {}
    rooms = {}
    course = {}
    sections = {}
    offers = {}
    for code, name, _ in DEPTS:
        d = db.insert("departments", school_id=school, code=code, name=name)
        dept.append(d)
        prog.append(
            db.insert(
                "programmes",
                department_id=d,
                code="BT-" + code,
                name="B.Tech. " + name,
                semesters=8,
            )
        )
    cats = [
        db.insert(
            "course_categories",
            code="PC",
            name="Programme core",
            elective=False,
            mandatory_for_degree=True,
        ),
        db.insert(
            "course_categories", code="PE", name="Professional elective", elective=True
        ),
    ]
    for yr in [2022, 2024, 2025, 2026]:
        r = db.insert(
            "regulations",
            code=f"R{yr}",
            name=f"Regulation {yr} · illustrative",
            active=True,
            floating_window=1,
            conditional_allowed=False,
            attendance_threshold=None,
        )
        regs.append(r)
        assess[r] = db.insert(
            "assessment_schemes",
            name="Illustrative assessment",
            regulation_id=r,
            description="Synthetic independent assessment mapping; not an official assessment scheme.",
        )
        for pr in prog:
            db.insert(
                "credit_limits",
                regulation_id=r,
                programme_id=pr,
                academic_year_id=ay,
                minimum_credits=6,
                maximum_credits=24,
            )
            db.insert(
                "graduation_requirements",
                regulation_id=r,
                programme_id=pr,
                total_credits=48,
                mandatory_credits=32,
                elective_credits=16,
            )
    for j, (code, name, _) in enumerate(DEPTS):
        for k in range(8):
            f = db.insert(
                "faculty",
                staff_number=f"SYN-F{j + 1}{k + 1:02}",
                name="Dr. " + NAMES[(j * 8 + k + 3) % len(NAMES)],
                department_id=dept[j],
                specialisation=TITLES[j][k],
                lab_competent=True,
            )
            fac[j, k] = f
            db.insert(
                "faculty_qualifications", faculty_id=f, qualification="Ph.D. · " + name
            )
            db.insert(
                "faculty_workloads",
                faculty_id=f,
                academic_year_id=ay,
                maximum_hours=10,
                other_hours=3 + (k % 3),
            )
            db.c.cursor().executemany(
                "INSERT INTO faculty_availability(faculty_id,weekday,starts,ends) VALUES(%s,%s,480,1080)",
                [(f, x) for x in range(1, 7)],
            )
        for n in range(1, 9):
            rooms[j, n] = db.insert(
                "classrooms", code=f"{code}-{100 + n}", capacity=180
            )
            for e in [0, 1]:
                for ir, r in enumerate(regs):
                    c = db.insert(
                        "courses",
                        code=f"{code}{n}0{e + 1}",
                        title=TITLES[j][n - 1]
                        if not e
                        else [
                            "Design Thinking",
                            "Technical Communication",
                            "Applied Modelling",
                            "Innovation Studio",
                            "Research Methods",
                            "Systems Engineering",
                            "Industry Practice",
                            "Advanced Applications",
                        ][n - 1],
                        programme_id=prog[j],
                        regulation_id=r,
                        semester_id=sem[n],
                        category_id=cats[e],
                        credits=4 if not e else 2,
                        lecture=2,
                        tutorial=0,
                        practical=0,
                        assessment_id=assess[r],
                        approved=True,
                    )
                    course[j, n, e, r] = c
                    if n == 5 and not e:
                        g = db.insert(
                            "course_prerequisite_groups",
                            course_id=c,
                            name="Subject foundations",
                            operator="AND",
                            kind="Mandatory",
                        )
                        db.insert(
                            "course_prerequisites",
                            group_id=g,
                            prerequisite_id=course[j, 3, 0, r],
                        )
                    for previous in regs[:ir]:
                        db.insert(
                            "course_equivalences",
                            course_a=course[j, n, e, previous],
                            course_b=c,
                            approved=True,
                            recognise_credit=True,
                            valid_from=date(2026, 6, 1),
                            valid_until=date(2027, 5, 31),
                        )
                base = course[j, n, e, regs[-1]]
                faculty = fac[j, n - 1]
                o = db.insert(
                    "course_offerings",
                    course_id=base,
                    department_id=dept[j],
                    academic_year_id=ay,
                    window_id=win,
                    status="Pending approval"
                    if n == 8 and e == 1 and j < 4
                    else "Published",
                    recovery_allowed=True,
                )
                offers[j, n, e] = o
                sc = db.insert(
                    "course_sections",
                    offering_id=o,
                    label="A",
                    faculty_id=faculty,
                    maximum_seats=160,
                    minimum_seats=8,
                    reserved_seats=12,
                )
                sections[j, n, e] = sc
                db.insert(
                    "timetable_slots",
                    section_id=sc,
                    room_id=rooms[j, n],
                    weekday=(n - 1) % 5 + 1,
                    starts=540 + e * 180,
                    ends=660 + e * 180,
                    kind="Theory",
                )
                for r in regs:
                    db.run(
                        "INSERT INTO offering_mappings VALUES(%s,%s)",
                        (o, course[j, n, e, r]),
                    )
                    db.run(
                        "INSERT INTO faculty_course_competencies VALUES(%s,%s,true)",
                        (faculty, course[j, n, e, r]),
                    )
    users = {}
    for role in ROLES:
        u = db.insert(
            "users",
            username="demo." + role.lower().replace(" ", "-"),
            name={
                "Dean – SET": "Dr. A. Raman",
                "Super Administrator": "Workspace Administrator",
                "Student": NAMES[0],
                "Faculty": "Dr. " + NAMES[3],
                "HoD": "Dr. K. Iyer",
                "Registrar": "University Registrar",
                "Faculty Advisor": "Dr. " + NAMES[4],
                "Examination Cell": "Examination Officer",
            }[role],
            role_id=db.val("SELECT id FROM roles WHERE name=%s", (role,)),
            department_id=dept[0]
            if role in ["HoD", "Faculty", "Faculty Advisor", "Student"]
            else None,
            faculty_id=fac[0, 0] if role in ["Faculty", "Faculty Advisor"] else None,
            demo=True,
        )
        users[role] = u
    db.run("UPDATE users SET school_id=%s WHERE id=%s", (school, users["Dean – SET"]))
    for rid in regs:
        for kind, authority in [
            ("Conditional prerequisite", "HoD"),
            ("Credit overload", "Registrar"),
            ("Course equivalence", "Registrar"),
            ("Special offering", "Dean – SET"),
            ("Timetable", "Dean – SET"),
            ("Academic progression", "Registrar"),
            ("Attendance exception", "Registrar"),
        ]:
            db.insert(
                "exception_policies",
                regulation_id=rid,
                kind=kind,
                authority=authority,
                active=True,
            )
    db.run(
        "UPDATE regulations SET approved_by=%s,approved_at=now()", (users["Registrar"],)
    )
    db.run("UPDATE course_equivalences SET approved_by=%s", (users["Registrar"],))
    db.run(
        "UPDATE course_offerings SET approved_by=%s WHERE status='Published'",
        (users["Dean – SET"],),
    )
    rrows = []
    arows = []
    enroll = []
    prefs = []
    sid0 = None
    allstudents = []
    for j, (code, _, count) in enumerate(DEPTS):
        for i in range(count):
            n = [3, 1, 5, 7][i % 4]
            r = regs[{1: 3, 3: 2, 5: 1, 7: 0}[n]]
            b = 2026 - (n - 1) // 2
            name = NAMES[(i + j * 7) % len(NAMES)] + (
                " " + chr(65 + ((i // len(NAMES)) % 26)) + "."
                if i >= len(NAMES)
                else ""
            )
            sid = db.insert(
                "students",
                register_number=f"SYN-{b % 100}{code}{i + 1:04}",
                name=name,
                programme_id=prog[j],
                regulation_id=r,
                batch_id=batches[b],
                semester_id=sem[n],
                status="Active" if i % 89 or i == 0 else "Restricted",
                advisor_id=fac[j, i % 8],
            )
            allstudents.append(sid)
            sid0 = sid0 or sid
            db.run(
                "INSERT INTO student_academic_records(student_id,cgpa,verified_at) VALUES(%s,%s,now())",
                (sid, round(rng.uniform(6.1, 9.7), 2)),
            )
            recovery = None
            for prev in range(1, n):
                for e in [0, 1]:
                    outcome = "Passed"
                    if prev == n - 1 and e == 0 and i % 7 == 0 and i > 0:
                        outcome = "Failed"
                        recovery = (prev, "Backlog")
                    elif prev == n - 1 and e == 0 and i % 13 == 0 and i > 0:
                        outcome = "Debarred"
                        recovery = (prev, "Repeat")
                        arows.append(
                            (sid, course[j, prev, e, r], ay, 15, 30, "Debarred", True)
                        )
                    rrows.append((sid, course[j, prev, e, r], 1, outcome, True))
            if i == 0 or (i % 89 != 0 and (i * 73) % 100 < [91, 86, 79, 88, 83, 76][j]):
                choices = [(n, 0, "Regular"), (n, 1, "Regular")]
                if i % 3 != 0 or i == 0:
                    choices.append((n + 1, 0, "Floating"))
                if recovery:
                    choices.append((recovery[0], 0, recovery[1]))
                if i == 0:
                    choices = [(n, 0, "Regular")]
                for sn, e, basket in choices:
                    days = rng.choices(
                        list(range(14)),
                        weights=[12, 11, 9, 10, 8, 5, 7, 6, 7, 5, 5, 4, 3, 3],
                    )[0]
                    when = (now - timedelta(days=days)).replace(
                        hour=rng.randrange(6, 12), minute=rng.randrange(60)
                    )
                    enroll.append(
                        (
                            sid,
                            sections[j, sn, e],
                            course[j, sn, e, r],
                            ay,
                            basket,
                            "P2"
                            if basket in ["Backlog", "Repeat"]
                            else (
                                "P5" if basket == "Floating" else ("P4" if e else "P3")
                            ),
                            "Registered",
                            when,
                        )
                    )
                if not i % 17:
                    arows.append(
                        (sid, course[j, n, 0, r], ay, 17, 25, "Shortage", False)
                    )
                else:
                    arows.append(
                        (sid, course[j, n, 0, r], ay, 23, 25, "Eligible", False)
                    )
            prefs.append((sid, course[j, min(n + 1, 8), 1, r], ay, 1, "Floating"))
            if i == 0 and j < 6:
                db.insert(
                    "academic_exceptions",
                    student_id=sid,
                    course_id=course[j, n + 1, 0, r],
                    kind="Conditional prerequisite"
                    if j % 2 == 0
                    else "Credit overload",
                    reason="Advisor review requested for the upcoming registration window.",
                    evidence="Synthetic advisor recommendation · SET/PILOT/"
                    + str(j + 1),
                    requested_by=users["Student"],
                    authority="Dean – SET" if j % 2 == 0 else "Registrar",
                    status="Pending",
                    extra_credits=2 if j % 2 else 0,
                    academic_year_id=ay,
                )
    cur = db.c.cursor()
    cur.executemany(
        "INSERT INTO student_course_results(student_id,course_id,attempt,outcome,verified) VALUES(%s,%s,%s,%s,%s)",
        rrows,
    )
    cur.executemany(
        "INSERT INTO attendance_records(student_id,course_id,academic_year_id,attended,conducted,status,repeat_approved) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        arows,
    )
    cur.executemany(
        "INSERT INTO course_enrolments(student_id,section_id,course_id,academic_year_id,basket,priority,status,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        enroll,
    )
    cur.executemany(
        "INSERT INTO course_preferences(student_id,course_id,academic_year_id,rank,interest) VALUES(%s,%s,%s,%s,%s)",
        prefs,
    )
    db.run("UPDATE course_enrolments SET attempt=2 WHERE basket IN('Backlog','Repeat')")
    db.run("UPDATE users SET student_id=%s WHERE id=%s", (sid0, users["Student"]))
    db.audit(
        None,
        "Initialised synthetic SET workspace",
        "workspace",
        None,
        {
            "students": len(allstudents),
            "source": "Clearly labelled synthetic fixtures",
            "official_policy": False,
        },
    )
    print(
        f"Synthetic workspace ready: {len(allstudents)} students, {len(enroll)} registrations, {len(sections)} sections."
    )


if __name__ == "__main__":
    with connect() as c:
        db = DB(c)
        for m in sorted((ROOT / "migrations").glob("*.sql")):
            if not db.val("SELECT to_regclass('schema_migrations')") or not db.val(
                "SELECT version FROM schema_migrations WHERE version=%s", (m.stem,)
            ):
                db.run(m.read_text())
        setup_roles(db)
        if "--demo" in sys.argv:
            seed(db)
