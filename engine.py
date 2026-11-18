"""One decision function is used by validation, registration, promotion and reports."""

from .core import *
from datetime import datetime, timezone


def recognised(db, cid, credit=True):
    rows = db.all(
        """SELECT CASE WHEN course_a=%s THEN course_b ELSE course_a END AS id FROM course_equivalences WHERE (course_a=%s OR course_b=%s) AND approved AND current_date BETWEEN valid_from AND valid_until"""
        + (" AND recognise_credit" if credit else ""),
        (cid, cid, cid),
    )
    return {cid} | {r["id"] for r in rows}


def credit_limit(db, s, year):
    return db.one(
        """SELECT * FROM credit_limits WHERE regulation_id=%s AND programme_id=%s AND academic_year_id=%s AND (semester_id IS NULL OR semester_id=%s) AND (category IS NULL OR category=%s) ORDER BY (semester_id IS NOT NULL) DESC,(category IS NOT NULL) DESC LIMIT 1""",
        (s["regulation_id"], s["programme_id"], year, s["semester_id"], s["category"]),
    )


def has_exception(db, s, cid, kind, year):
    return db.one(
        "SELECT * FROM academic_exceptions WHERE student_id=%s AND (course_id=%s OR course_id IS NULL) AND kind=%s AND status='Approved' AND academic_year_id=%s ORDER BY decided_at DESC LIMIT 1",
        (s["id"], cid, kind, year),
    )


def staff_check(db, sec, off, faculty_id=None):
    fid = faculty_id or sec["faculty_id"]
    year = off["academic_year_id"]
    cid = off["course_id"]
    if not fid:
        return "A qualified faculty member has not been assigned."
    if not db.val(
        "SELECT EXISTS(SELECT 1 FROM faculty_course_competencies x JOIN faculty f ON f.id=x.faculty_id WHERE f.deleted_at IS NULL AND x.faculty_id=%s AND x.course_id=ANY(%s) AND x.approved)",
        (fid, list(recognised(db, cid, False))),
    ):
        return "Faculty course competency has not been approved."
    workload = db.one(
        "SELECT * FROM faculty_workloads WHERE faculty_id=%s AND academic_year_id=%s",
        (fid, year),
    )
    if not workload:
        return "An approved faculty workload norm is missing."
    slots = db.all(
        "SELECT t.*,r.capacity,l.capacity AS lab_capacity FROM timetable_slots t JOIN classrooms r ON r.id=t.room_id LEFT JOIN laboratories l ON l.classroom_id=r.id WHERE section_id=%s",
        (sec["id"],),
    )
    if not slots:
        return "A room and weekly teaching schedule are required."
    structure = db.one(
        "SELECT lecture,tutorial,practical FROM courses WHERE id=%s", (cid,)
    )
    for kind, key in [
        ("Theory", "lecture"),
        ("Tutorial", "tutorial"),
        ("Practical", "practical"),
    ]:
        scheduled = sum(t["ends"] - t["starts"] for t in slots if t["kind"] == kind)
        if structure and scheduled < 60 * structure[key]:
            return f"The timetable does not cover the approved {kind.lower()} contact hours."
    if any(
        a["weekday"] == b["weekday"]
        and a["starts"] < b["ends"]
        and a["ends"] > b["starts"]
        for i, a in enumerate(slots)
        for b in slots[i + 1 :]
    ):
        return "Overlapping slots within this teaching section."
    assigned = db.val(
        "SELECT COALESCE(sum(t.ends-t.starts),0)/60.0 FROM timetable_slots t JOIN course_sections x ON x.id=t.section_id JOIN course_offerings o ON o.id=x.offering_id WHERE x.faculty_id=%s AND x.id<>%s AND o.academic_year_id=%s AND (o.status='Published' OR o.id=%s)",
        (fid, sec["id"], year, sec["offering_id"]),
    )
    if (
        assigned
        + workload["other_hours"]
        + Decimal(sum(t["ends"] - t["starts"] for t in slots)) / 60
        > workload["maximum_hours"]
    ):
        return "Assignment exceeds the approved weekly faculty workload."
    for t in slots:
        if sec["maximum_seats"] > t["capacity"]:
            return "Section capacity exceeds the classroom capacity."
        if t["kind"] == "Practical" and (
            not t["lab_capacity"]
            or t["lab_capacity"] < sec["maximum_seats"]
            or not db.val("SELECT lab_competent FROM faculty WHERE id=%s", (fid,))
        ):
            return "Practical slot requires a competent faculty member and sufficient laboratory capacity."
        if not db.val(
            "SELECT EXISTS(SELECT 1 FROM faculty_availability WHERE faculty_id=%s AND weekday=%s AND starts<=%s AND ends>=%s)",
            (fid, t["weekday"], t["starts"], t["ends"]),
        ):
            return "Scheduled teaching is outside the approved faculty availability."
        collision = db.one(
            """SELECT f.name,r.code,x.faculty_id,t.room_id FROM timetable_slots t JOIN course_sections x ON x.id=t.section_id JOIN course_offerings o ON o.id=x.offering_id JOIN faculty f ON f.id=x.faculty_id JOIN classrooms r ON r.id=t.room_id WHERE x.id<>%s AND o.academic_year_id=%s AND (o.status='Published' OR o.id=%s) AND t.weekday=%s AND t.starts<%s AND t.ends>%s AND (x.faculty_id=%s OR t.room_id=%s) LIMIT 1""",
            (
                sec["id"],
                year,
                sec["offering_id"],
                t["weekday"],
                t["ends"],
                t["starts"],
                fid,
                t["room_id"],
            ),
        )
        if collision:
            return (
                "Faculty timetable conflict: overlapping teaching assignment."
                if collision["faculty_id"] == fid
                else "Classroom timetable conflict: "
                + collision["code"]
                + " is already in use."
            )
    return None


def evaluate(db, s, cid, section_id=None, basket="Auto", ignore_id=0, check_staff=True):
    year = year_id(db)
    sid = s["id"]
    c = db.one(
        "SELECT c.*,cs.number AS semester,cc.elective,cc.name AS category FROM courses c JOIN curriculum_semesters cs ON cs.id=c.semester_id JOIN course_categories cc ON cc.id=c.category_id WHERE c.id=%s AND c.deleted_at IS NULL",
        (cid,),
    )
    if not c:
        fail("Course not found.", 404)
    out = {
        "course_id": cid,
        "code": c["code"],
        "title": c["title"],
        "credits": c["credits"],
        "semester": c["semester"],
        "section_id": section_id,
        "basket": "Regular",
        "priority": "P3",
        "category": c["category"],
    }

    def finish(status, reason):
        return {**out, "status": status, "reason": reason}

    if (
        s["regulation_id"] != c["regulation_id"]
        or s["programme_id"] != c["programme_id"]
    ):
        return finish(
            "Blocked",
            "Course is outside the student’s approved regulation and programme.",
        )
    if not s["regulation_active"] or not c["approved"]:
        return finish(
            "Blocked", "The regulation or curriculum is not active and approved."
        )
    eq = list(recognised(db, cid))
    passed = {
        r["course_id"]
        for r in db.all(
            "SELECT DISTINCT course_id FROM student_course_results WHERE student_id=%s AND verified AND outcome='Passed'",
            (sid,),
        )
    }
    if passed.intersection(eq):
        return finish(
            "Already completed",
            "Verified results show completion of this course or an approved credit-equivalent course.",
        )
    enrol = db.one(
        "SELECT e.id,e.status,e.basket FROM course_enrolments e WHERE student_id=%s AND course_id=ANY(%s) AND academic_year_id=%s AND status IN('Registered','Waitlisted','Pending approval') AND id<>%s LIMIT 1",
        (sid, eq, year, ignore_id),
    )
    if enrol:
        return {
            **finish(
                "Blocked",
                "Duplicate registration: this course or an approved equivalent is already "
                + enrol["status"].lower()
                + ".",
            ),
            "enrolment_id": enrol["id"],
            "existing_status": enrol["status"],
            "basket": enrol["basket"],
        }
    if s["status"] != "Active" and not has_exception(
        db, s, cid, "Academic progression", year
    ):
        return finish(
            "Blocked",
            "Academic status is "
            + s["status"].lower()
            + ". An authorised progression decision is required.",
        )
    att = db.one(
        "SELECT * FROM attendance_records WHERE student_id=%s AND course_id=%s ORDER BY academic_year_id DESC,attempt DESC LIMIT 1",
        (sid, cid),
    )
    results = db.one(
        "SELECT outcome FROM student_course_results WHERE student_id=%s AND course_id=%s AND verified ORDER BY attempt DESC LIMIT 1",
        (sid, cid),
    )
    outcome = results["outcome"] if results else ""
    repeat = outcome == "Debarred" or bool(att and att["status"] == "Debarred")
    backlog = outcome == "Failed"
    rec = repeat or backlog or bool(att and att["repeat_approved"])
    if backlog and basket == "Normal":
        return finish(
            "Blocked",
            "A verified failed attempt must use the approved backlog recovery basket.",
        )
    if repeat and basket == "Normal":
        return finish(
            "Blocked",
            "Attendance Repeat Required. Use the recovery basket for an approved repeat offering.",
        )
    if basket == "Recovery" and not rec:
        return finish(
            "Blocked", "No verified backlog or approved repeat exists for this course."
        )
    if rec and basket != "Normal":
        out["basket"] = (
            "Repeat" if repeat or (att and att["repeat_approved"]) else "Backlog"
        )
        out["priority"] = "P2"
    elif c["semester"] == s["semester"]:
        out["priority"] = "P4" if c["elective"] else "P3"
    elif (
        s["semester"] < s["semesters"]
        and c["semester"] == s["semester"] + 1
        and s["floating_window"] == 1
    ):
        out["basket"] = "Floating"
        out["priority"] = "P5"
    elif not (
        s["semester"] == s["semesters"]
        and db.val(
            "SELECT approved FROM pending_requirements WHERE student_id=%s AND course_id=%s",
            (sid, cid),
        )
    ):
        return finish(
            "Blocked",
            "Outside the current + next-semester window; no approved academic recovery or graduation requirement applies.",
        )
    if s["semester"] >= s["semesters"] - 1 and db.val(
        "SELECT approved FROM pending_requirements WHERE student_id=%s AND course_id=%s",
        (sid, cid),
    ):
        out["priority"] = "P1"
    if (
        att
        and att["status"] == "Shortage"
        and s["block_on_shortage"]
        and not has_exception(db, s, cid, "Attendance exception", year)
    ):
        return finish(
            "Blocked",
            "Course-wise attendance shortage under the applicable regulation.",
        )
    if s["block_unrelated"] and db.val(
        "SELECT EXISTS(SELECT 1 FROM attendance_records WHERE student_id=%s AND academic_year_id=%s AND status='Debarred')",
        (sid, year),
    ):
        return finish(
            "Blocked",
            "This regulation explicitly restricts progression during attendance debarment.",
        )
    groups = db.all(
        """SELECT g.id,g.operator,g.kind,array_agg(cp.prerequisite_id) AS ids,array_agg(c.title) AS titles FROM course_prerequisite_groups g JOIN course_prerequisites cp ON cp.group_id=g.id JOIN courses c ON c.id=cp.prerequisite_id WHERE g.course_id=%s GROUP BY g.id""",
        (cid,),
    )
    notes = []
    pending = None
    for g in groups:
        meets = [bool(passed & recognised(db, id)) for id in g["ids"]]
        ok = all(meets) if g["operator"] == "AND" else any(meets)
        if not ok:
            detail = (
                c["title"]
                + " requires successful completion of "
                + (" " + g["operator"].lower() + " ").join(g["titles"])
                + "."
            )
            if g["kind"] == "Mandatory":
                return finish("Blocked", detail)
            if (
                g["kind"] == "Conditional"
                and not s["conditional_allowed"]
                and not has_exception(db, s, cid, "Conditional prerequisite", year)
            ):
                pending = "Conditional prerequisite approval required. " + detail
            notes.append(
                "Recommended preparation: " + ", ".join(g["titles"])
                if g["kind"] == "Recommended"
                else "Conditional registration authorised."
            )
    sec = db.one(
        """SELECT z.*,o.id AS off_id,o.course_id AS base_course,o.status AS offering_status,o.recovery_allowed,o.academic_year_id,w.opens_at,w.closes_at FROM offering_mappings m JOIN course_offerings o ON o.id=m.offering_id JOIN course_sections z ON z.offering_id=o.id JOIN registration_windows w ON w.id=o.window_id WHERE m.course_id=%s AND o.academic_year_id=%s AND o.deleted_at IS NULL"""
        + (" AND z.id=%s" if section_id else "")
        + " ORDER BY (o.status='Published') DESC,z.id LIMIT 1",
        (cid, year, section_id) if section_id else (cid, year),
    )
    if not sec or sec["offering_status"] != "Published":
        return finish(
            "Blocked", "No approved, published offering is available for this course."
        )
    out["section_id"] = sec["id"]
    out["section"] = sec["label"]
    if sec["base_course"] not in recognised(db, cid, False):
        return finish(
            "Blocked",
            "A current approved equivalence is required for this shared teaching section.",
        )
    if out["basket"] in ["Repeat", "Backlog"] and not sec["recovery_allowed"]:
        return finish(
            "Blocked", "This offering is not approved for recovery registration."
        )
    if not sec["opens_at"] <= datetime.now(timezone.utc) <= sec["closes_at"]:
        return finish("Blocked", "The registration window is closed.")
    limit = credit_limit(db, s, year)
    active = db.val(
        "SELECT COALESCE(SUM(c.credits),0) FROM course_enrolments e JOIN courses c ON c.id=e.course_id WHERE e.student_id=%s AND e.academic_year_id=%s AND e.status='Registered' AND e.id<>%s",
        (sid, year, ignore_id),
    )
    if not limit:
        return finish(
            "Blocked", "No approved credit limit is configured for this student."
        )
    extra = has_exception(db, s, cid, "Credit overload", year)
    maximum = limit["maximum_credits"] + (extra["extra_credits"] if extra else 0)
    if active + c["credits"] > maximum:
        return finish(
            "Blocked",
            f"Credit limit exceeded: {active:g} active + {c['credits']:g} requested exceeds {maximum:g} approved credits.",
        )
    if check_staff:
        error = staff_check(
            db, sec, {"academic_year_id": year, "course_id": sec["base_course"]}
        )
        if error:
            return finish("Blocked", error)
    clash = db.one(
        """SELECT c.title FROM timetable_slots a JOIN timetable_slots b ON a.weekday=b.weekday AND a.starts<b.ends AND a.ends>b.starts JOIN course_enrolments e ON e.section_id=b.section_id JOIN courses c ON c.id=e.course_id WHERE a.section_id=%s AND e.student_id=%s AND e.status='Registered' AND e.academic_year_id=%s AND e.id<>%s LIMIT 1""",
        (sec["id"], sid, year, ignore_id),
    )
    if clash:
        return finish(
            "Blocked", "Student timetable conflict with " + clash["title"] + "."
        )
    seats = db.one(
        "SELECT count(*) AS used,count(*) FILTER(WHERE priority IN('P1','P2','P3')) AS protected FROM course_enrolments WHERE section_id=%s AND status='Registered' AND id<>%s",
        (sec["id"], ignore_id),
    )
    remain = sec["maximum_seats"] - seats["used"]
    out["seats_left"] = remain
    if pending:
        return finish("Pending approval", pending)
    if remain <= 0:
        return finish(
            "Waitlisted",
            "Section is full. Places are allocated by approved priority, then request time.",
        )
    if out["priority"] in ["P4", "P5"] and remain <= max(
        0, sec["reserved_seats"] - seats["protected"]
    ):
        return finish(
            "Waitlisted",
            "Remaining seats are reserved for compulsory and recovery registrations.",
        )
    return finish(
        "Eligible",
        "All registration checks passed." + (" " + " ".join(notes) if notes else ""),
    )


def submit(db, u, s, cid, section_id, basket, confirm):
    confirmed(confirm)
    db.run(
        "SELECT pg_advisory_xact_lock(740126)"
    )  # shared allocation mutex; bounded SET pilot
    db.run("SELECT id FROM students WHERE id=%s FOR UPDATE", (s["id"],))
    if section_id:
        db.run("SELECT id FROM course_sections WHERE id=%s FOR UPDATE", (section_id,))
    s = get_student(db, u, s["id"])
    res = evaluate(db, s, cid, section_id, basket)
    if res["status"] not in ["Eligible", "Waitlisted", "Pending approval"]:
        fail(res["reason"], 409)
    status = "Registered" if res["status"] == "Eligible" else res["status"]
    if status == "Pending approval":
        pol = db.one(
            "SELECT authority FROM exception_policies WHERE regulation_id=%s AND kind='Conditional prerequisite' AND active",
            (s["regulation_id"],),
        )
        if not pol:
            fail(
                "No authorised conditional exception process has been configured.", 409
            )
        db.insert(
            "academic_exceptions",
            student_id=s["id"],
            course_id=cid,
            kind="Conditional prerequisite",
            reason=res["reason"],
            evidence="Pending course registration; verified evidence must be reviewed before approval.",
            requested_by=u["id"],
            authority=pol["authority"],
            status="Pending",
            academic_year_id=year_id(db),
        )
    attempt = db.val(
        "SELECT COALESCE(max(attempt),0)+1 FROM student_course_results WHERE student_id=%s AND course_id=%s AND verified",
        (s["id"], cid),
    )
    eid = db.insert(
        "course_enrolments",
        attempt=attempt,
        student_id=s["id"],
        section_id=res["section_id"],
        course_id=cid,
        academic_year_id=year_id(db),
        basket=res["basket"],
        priority=res["priority"],
        status=status,
    )
    db.audit(
        u,
        status,
        "course_enrolments",
        eid,
        {
            "student_id": s["id"],
            "course_id": cid,
            "priority": res["priority"],
            "basket": res["basket"],
        },
    )
    return {**res, "id": eid, "status": status}


def promote(db, u, section_id):
    queue = db.all(
        """SELECT e.* FROM course_enrolments e JOIN students s ON s.id=e.student_id JOIN regulations r ON r.id=s.regulation_id WHERE e.section_id=%s AND e.status='Waitlisted' ORDER BY array_position(r.priority_order,e.priority),e.created_at,e.id FOR UPDATE OF e""",
        (section_id,),
    )
    for row in queue:
        s = get_student(db, {"role": "System"}, row["student_id"])
        res = evaluate(db, s, row["course_id"], section_id, "Auto", row["id"])
        if res["status"] == "Eligible":
            db.run(
                "UPDATE course_enrolments SET status='Registered',changed_at=now() WHERE id=%s",
                (row["id"],),
            )
            db.audit(
                u,
                "Promoted from waitlist",
                "course_enrolments",
                row["id"],
                {"priority": res["priority"]},
            )


def credit_audit(db, s):
    y = year_id(db)
    rows = db.all(
        "SELECT course_id AS id,code,title,credits,elective FROM verified_credit_recognition WHERE student_id=%s ORDER BY course_id",
        (s["id"],),
    )
    seen = set()
    earned = Decimal(0)
    elect = Decimal(0)
    for r in rows:
        eq = recognised(db, r["id"])
        if seen & eq:
            continue
        seen.update(eq)
        earned += r["credits"]
        elect += r["credits"] if r["elective"] else 0
    b = db.all(
        "SELECT e.basket,sum(c.credits) AS credits FROM course_enrolments e JOIN courses c ON c.id=e.course_id WHERE student_id=%s AND academic_year_id=%s AND status='Registered' GROUP BY e.basket",
        (s["id"], y),
    )
    buckets = {x["basket"]: x["credits"] for x in b}
    registered = sum(buckets.values())
    g = db.one(
        "SELECT * FROM graduation_requirements WHERE regulation_id=%s AND programme_id=%s",
        (s["regulation_id"], s["programme_id"]),
    )
    limit = credit_limit(db, s, y)
    pending = max(0, g["total_credits"] - earned) if g else None
    missing = db.all(
        "SELECT c.id,c.title,c.credits FROM courses c WHERE c.programme_id=%s AND c.regulation_id=%s AND c.approved AND c.deleted_at IS NULL AND NOT(c.id=ANY(%s))",
        (s["programme_id"], s["regulation_id"], list(seen)),
    )
    offered = db.val(
        "SELECT count(DISTINCT m.course_id) FROM offering_mappings m JOIN course_offerings o ON o.id=m.offering_id WHERE m.course_id=ANY(%s) AND o.status='Published' AND o.academic_year_id=%s",
        ([r["id"] for r in missing], y),
    )
    eligible = bool(
        g
        and pending == 0
        and earned - elect >= g["mandatory_credits"]
        and elect >= g["elective_credits"]
        and not db.val(
            "SELECT count(*) FROM pending_requirements WHERE student_id=%s AND approved AND NOT(course_id=ANY(%s))",
            (s["id"], list(seen)),
        )
    )
    required_left = db.val(
        "SELECT count(*) FROM courses c JOIN course_categories cat ON cat.id=c.category_id WHERE c.programme_id=%s AND c.regulation_id=%s AND c.approved AND c.deleted_at IS NULL AND cat.mandatory_for_degree AND NOT(c.id=ANY(%s))",
        (s["programme_id"], s["regulation_id"], list(seen)),
    )
    eligible = eligible and required_left == 0
    periods = (
        math.ceil(pending / limit["maximum_credits"])
        if pending is not None and limit and limit["maximum_credits"] > 0
        else None
    )
    elective_registered = db.val(
        "SELECT COALESCE(sum(c.credits),0) FROM course_enrolments e JOIN courses c ON c.id=e.course_id JOIN course_categories cc ON cc.id=c.category_id WHERE e.student_id=%s AND e.academic_year_id=%s AND e.status='Registered' AND cc.elective",
        (s["id"], y),
    )
    return {
        "minimum_load_periods": periods,
        "mandatory_remaining": required_left,
        "elective_registered": elective_registered,
        "earned": earned,
        "registered": registered,
        "pending": pending,
        "target": g["total_credits"] if g else None,
        "mandatory_earned": earned - elect,
        "elective_earned": elect,
        "baskets": buckets,
        "minimum": limit["minimum_credits"] if limit else None,
        "maximum": limit["maximum_credits"] if limit else None,
        "graduation_eligible": eligible,
        "missing_courses": len(missing),
        "available_courses": offered,
        "forecast": "Degree credit and category requirements met."
        if eligible
        else f"{offered} of {len(missing)} remaining curriculum courses have published offerings. Credit-load projection: at least {periods if periods is not None else 'an unconfigured number of'} registration period(s) at the approved maximum. Availability, prerequisites and assessment outcomes may extend completion.",
    }
