from .core import *

STUDENTS = """SELECT s.id,s.register_number,s.name,s.status,p.code AS programme,d.code AS department,d.name AS department_name,d.id AS department_id,r.code AS regulation,cs.number AS semester,b.year AS admission_year,ar.cgpa FROM students s JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN regulations r ON r.id=s.regulation_id JOIN curriculum_semesters cs ON cs.id=s.semester_id JOIN admission_batches b ON b.id=s.batch_id LEFT JOIN student_academic_records ar ON ar.student_id=s.id WHERE s.deleted_at IS NULL"""
COURSES = """SELECT c.id,c.code,c.title,c.credits,c.lecture,c.tutorial,c.practical,c.approved,cs.number AS semester,r.code AS regulation,cc.name AS category,cc.elective,d.code AS department,p.code AS programme,sc.name AS assessment FROM courses c JOIN programmes p ON p.id=c.programme_id JOIN departments d ON d.id=p.department_id JOIN regulations r ON r.id=c.regulation_id JOIN curriculum_semesters cs ON cs.id=c.semester_id JOIN course_categories cc ON cc.id=c.category_id JOIN assessment_schemes sc ON sc.id=c.assessment_id WHERE c.deleted_at IS NULL"""
OFFERINGS = """SELECT o.id,z.id AS section_id,c.code,c.title,r.code AS regulation,d.code AS department,d.id AS department_id,o.status,z.label AS section,z.faculty_id,f.name AS faculty,z.maximum_seats AS capacity,z.reserved_seats,(SELECT count(*) FROM course_enrolments e WHERE e.section_id=z.id AND status='Registered') AS enrolled,(SELECT count(*) FROM course_enrolments e WHERE e.section_id=z.id AND status='Waitlisted') AS waitlisted,(SELECT string_agg(DISTINCT rr.code,', ' ORDER BY rr.code) FROM offering_mappings om JOIN courses cm ON cm.id=om.course_id JOIN regulations rr ON rr.id=cm.regulation_id WHERE om.offering_id=o.id) AS mappings,o.recovery_allowed FROM course_offerings o JOIN course_sections z ON z.offering_id=o.id JOIN courses c ON c.id=o.course_id JOIN regulations r ON r.id=c.regulation_id JOIN departments d ON d.id=o.department_id LEFT JOIN faculty f ON f.id=z.faculty_id WHERE o.deleted_at IS NULL"""
FACULTY = """SELECT f.id,f.staff_number,f.name,f.specialisation,f.lab_competent,d.code AS department,d.id AS department_id,(SELECT string_agg(qualification,', ') FROM faculty_qualifications WHERE faculty_id=f.id) AS qualification,w.maximum_hours,w.other_hours,COALESCE((SELECT sum(t.ends-t.starts)/60.0 FROM course_sections z JOIN timetable_slots t ON t.section_id=z.id JOIN course_offerings o ON o.id=z.offering_id WHERE z.faculty_id=f.id AND o.status='Published' AND o.academic_year_id=w.academic_year_id),0)+w.other_hours AS allocated,(SELECT count(*) FROM course_sections z JOIN course_offerings o ON o.id=z.offering_id WHERE z.faculty_id=f.id AND o.status='Published') AS sections FROM faculty f JOIN departments d ON d.id=f.department_id LEFT JOIN faculty_workloads w ON w.faculty_id=f.id AND w.academic_year_id=(SELECT id FROM academic_years WHERE current_date BETWEEN starts_on AND ends_on ORDER BY starts_on DESC LIMIT 1) WHERE f.deleted_at IS NULL"""
ENROL = """SELECT e.id,e.student_id,s.register_number,s.name AS student,c.code,c.title,c.credits,e.basket,e.priority,e.status,e.created_at,d.code AS department,r.code AS regulation,z.label AS section,e.section_id FROM course_enrolments e JOIN students s ON s.id=e.student_id JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN regulations r ON r.id=s.regulation_id JOIN courses c ON c.id=e.course_id JOIN course_sections z ON z.id=e.section_id WHERE e.academic_year_id=(SELECT id FROM academic_years WHERE current_date BETWEEN starts_on AND ends_on ORDER BY starts_on DESC LIMIT 1)"""
EXCEPT = """SELECT x.*,s.name AS student,s.register_number,c.code AS course_code,c.title AS course,d.code AS department,u.name AS decision_maker FROM academic_exceptions x JOIN students s ON s.id=x.student_id JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id LEFT JOIN courses c ON c.id=x.course_id LEFT JOIN users u ON u.id=x.decided_by WHERE true"""


def dept_overview(db, u):
    where, p = dept_scope(u, "d.id")
    return db.all(
        """SELECT d.id,d.code AS code,d.name AS department,(SELECT count(*) FROM students s JOIN programmes p ON p.id=s.programme_id WHERE p.department_id=d.id AND s.deleted_at IS NULL) AS students,(SELECT count(*) FROM course_offerings o WHERE o.department_id=d.id AND o.deleted_at IS NULL) AS offerings,(SELECT count(*) FROM course_enrolments e JOIN students s ON s.id=e.student_id JOIN programmes p ON p.id=s.programme_id WHERE p.department_id=d.id AND e.status='Registered' AND e.basket='Floating') AS floating,(SELECT count(DISTINCT e.student_id) FROM course_enrolments e JOIN students s ON s.id=e.student_id JOIN programmes p ON p.id=s.programme_id WHERE p.department_id=d.id AND e.status='Registered') AS registered,(SELECT count(*) FROM faculty f WHERE f.department_id=d.id AND f.deleted_at IS NULL) AS faculty FROM departments d WHERE d.deleted_at IS NULL AND """
        + where
        + " ORDER BY d.id",
        p,
    )


def dashboard(db, u, days):
    days = days if days in [7, 14, 30] else 7
    scope, p = student_scope(u)
    ds, dp = dept_scope(u, "d.id")
    sids = "SELECT s.id FROM students s WHERE s.deleted_at IS NULL AND " + scope
    stats = db.one(
        "SELECT count(*) AS students,count(*) FILTER(WHERE status='Active') AS active_students FROM students s WHERE s.deleted_at IS NULL AND "
        + scope,
        p,
    )
    faculty = db.all(FACULTY + " AND " + ds, dp)
    usage = sum(float(x["allocated"] or 0) for x in faculty)
    cap = sum(float(x["maximum_hours"] or 0) for x in faculty)
    stats.update(
        faculty_utilisation=round(100 * usage / cap, 1) if cap else 0,
        faculty=len(faculty),
        regulations=db.val(
            "SELECT count(*) FROM regulations WHERE active AND deleted_at IS NULL"
        ),
        departments=len(dept_overview(db, u)),
    )
    offering = db.one(
        "SELECT count(*) AS offerings,count(*) FILTER(WHERE o.status='Published') AS published,count(*) FILTER(WHERE o.status='Pending approval') AS pending_offers,count(*) FILTER(WHERE NOT EXISTS(SELECT 1 FROM course_sections z WHERE z.offering_id=o.id AND faculty_id IS NOT NULL)) AS faculty_shortages FROM course_offerings o JOIN departments d ON d.id=o.department_id WHERE o.deleted_at IS NULL AND "
        + ds,
        dp,
    )
    stats.update(offering)
    stats["pending_exceptions"] = db.val(
        "SELECT count(*) FROM academic_exceptions WHERE status='Pending' AND student_id IN("
        + sids
        + ")",
        p,
    )
    stats["pending"] = stats["pending_exceptions"] + stats["pending_offers"]
    stats["shortage"] = db.val(
        "SELECT count(DISTINCT student_id) FROM attendance_records WHERE status='Shortage' AND student_id IN("
        + sids
        + ")",
        p,
    )
    stats["backlogs"] = db.val(
        "SELECT count(DISTINCT a.student_id) FROM student_course_results a WHERE a.outcome='Failed' AND a.verified AND NOT EXISTS(SELECT 1 FROM student_course_results b WHERE b.student_id=a.student_id AND b.course_id=a.course_id AND b.verified AND b.outcome='Passed') AND a.student_id IN("
        + sids
        + ")",
        p,
    )
    mix = db.all(
        "SELECT basket,count(*) AS value FROM course_enrolments WHERE status='Registered' AND academic_year_id=%s AND student_id IN("
        + sids
        + ") GROUP BY basket ORDER BY value DESC",
        [year_id(db)] + p,
    )
    raw = db.all(
        "SELECT created_at::date AS day,basket,count(*) AS value FROM course_enrolments WHERE status='Registered' AND created_at::date >= current_date-(%s::integer-1) AND student_id IN("
        + sids
        + ") GROUP BY 1,2 ORDER BY 1",
        [days] + p,
    )
    from datetime import timedelta

    trend = []
    for i in range(days):
        dt = date.today() - timedelta(days=days - i - 1)
        rr = [r for r in raw if r["day"] == dt]
        trend.append(
            {
                "date": dt.isoformat(),
                "regular": sum(r["value"] for r in rr if r["basket"] == "Regular"),
                "floating": sum(r["value"] for r in rr if r["basket"] == "Floating"),
            }
        )
    snap = db.one(
        "SELECT count(*) FILTER(WHERE eligible_courses>0) AS eligible_students,count(*) FILTER(WHERE floating_courses>0) AS floating_eligible,max(evaluated_at) AS eligibility_as_of FROM eligibility_snapshots WHERE academic_year_id=%s AND student_id IN("
        + sids
        + ")",
        [year_id(db)] + p,
    )
    stats.update(snap)
    return {
        "stats": stats,
        "trend": trend,
        "mix": mix,
        "departments": dept_overview(db, u),
        "year": db.val("SELECT label FROM academic_years WHERE id=%s", (year_id(db),)),
        "synthetic": DEMO,
    }


REPORTS = {
    "registration": "Registration register",
    "department": "Department overview",
    "faculty-workload": "Faculty workload",
    "course-demand": "Course demand",
    "section-strength": "Section strength",
    "attendance": "Course-wise attendance",
    "attendance-debarred": "Attendance-debarred list",
    "backlogs": "Backlog register",
    "repeat-demand": "Attendance repeat demand",
    "floating": "Floating registrations",
    "credit-completion": "Credit completion",
    "graduation-risk": "Graduation risk",
    "regulation-distribution": "Regulation distribution",
    "faculty-shortage": "Faculty shortages",
    "course-utilisation": "Offering utilisation",
    "cross-department": "Cross-department enrolment",
    "eligible": "Eligible students",
    "ineligible": "Ineligible students",
    "prerequisite-blocked": "Prerequisite blocks",
    "academic-recovery": "Academic recovery",
    "student-progression": "Student progression",
    "academic-risk": "Academic risk",
}


def report_data(db, u, key, limit=None):
    sc, sp = student_scope(u)
    ds, dp = dept_scope(u, "d.id")
    tail = " LIMIT " + str(int(limit)) if limit else ""
    if key not in REPORTS:
        fail("Unknown report.", 404)
    if key in [
        "faculty-workload",
        "faculty-shortage",
        "department",
        "course-utilisation",
        "section-strength",
        "regulation-distribution",
        "course-demand",
    ] and u["role"] in ["Student", "Faculty Advisor", "Faculty"]:
        if not (key == "faculty-workload" and u["role"] == "Faculty"):
            fail("This report requires department reporting privileges.", 403)
    if u["role"] == "Faculty" and key not in [
        "registration",
        "floating",
        "cross-department",
        "faculty-workload",
        "attendance",
        "attendance-debarred",
    ]:
        fail("This report exceeds the faculty teaching scope.", 403)
    if u["role"] == "Faculty" and key in [
        "registration",
        "floating",
        "cross-department",
    ]:
        sc += " AND z.faculty_id=%s"
        sp.append(u["faculty_id"])
    if u["role"] == "Faculty" and key in ["attendance", "attendance-debarred"]:
        sc += " AND EXISTS(SELECT 1 FROM course_enrolments en JOIN course_sections sz ON sz.id=en.section_id WHERE en.student_id=s.id AND en.course_id=a.course_id AND en.status='Registered' AND sz.faculty_id=%s)"
        sp.append(u["faculty_id"])
    if key == "repeat-demand":
        return db.all(
            "SELECT d.code AS department,c.code,c.title,cs.number AS semester,count(DISTINCT a.student_id) AS repeat_demand FROM attendance_records a JOIN students s ON s.id=a.student_id JOIN courses c ON c.id=a.course_id JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN curriculum_semesters cs ON cs.id=c.semester_id WHERE a.status='Debarred' AND NOT EXISTS(SELECT 1 FROM verified_credit_recognition v WHERE v.student_id=s.id AND v.course_id=c.id) AND "
            + sc
            + " GROUP BY d.code,c.code,c.title,cs.number ORDER BY repeat_demand DESC"
            + tail,
            sp,
        )
    if key == "academic-recovery":
        return db.all(
            "SELECT s.register_number,s.name AS student,d.code AS department,cs.number AS semester,c.code,c.title,r.outcome,CASE WHEN r.outcome='Debarred' THEN 'Attendance repeat' ELSE 'Backlog' END AS recovery_basket FROM student_course_results r JOIN students s ON s.id=r.student_id JOIN courses c ON c.id=r.course_id JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN curriculum_semesters cs ON cs.id=c.semester_id WHERE r.verified AND r.outcome IN('Failed','Debarred') AND NOT EXISTS(SELECT 1 FROM verified_credit_recognition v WHERE v.student_id=s.id AND v.course_id=c.id) AND "
            + sc
            + " ORDER BY cs.number,d.code"
            + tail,
            sp,
        )
    if key == "department":
        return dept_overview(db, u)
    if key == "faculty-workload":
        return db.all(
            FACULTY
            + " AND "
            + ds
            + (" AND f.id=%s" if u["role"] == "Faculty" else "")
            + " ORDER BY d.code,f.name"
            + tail,
            dp + ([u["faculty_id"]] if u["role"] == "Faculty" else []),
        )
    if key in ["registration", "floating", "cross-department"]:
        return db.all(
            ENROL
            + " AND "
            + sc
            + (
                " AND e.basket='Floating' AND e.status='Registered'"
                if key == "floating"
                else (
                    " AND d.id<>(SELECT department_id FROM course_offerings o WHERE o.id=z.offering_id)"
                    if key == "cross-department"
                    else ""
                )
            )
            + " ORDER BY e.created_at DESC"
            + tail,
            sp,
        )
    if key in ["course-utilisation", "section-strength", "faculty-shortage"]:
        return db.all(
            OFFERINGS
            + " AND "
            + ds
            + (" AND z.faculty_id IS NULL" if key == "faculty-shortage" else "")
            + " ORDER BY d.code,c.code"
            + tail,
            dp,
        )
    if key in [
        "attendance",
        "attendance-debarred",
        "repeat-demand",
        "academic-recovery",
        "backlogs",
    ]:
        if key == "backlogs":
            return db.all(
                """SELECT s.register_number,s.name AS student,c.code,c.title,r.outcome AS status,cs.number AS semester FROM student_course_results r JOIN students s ON s.id=r.student_id JOIN courses c ON c.id=r.course_id JOIN curriculum_semesters cs ON cs.id=c.semester_id WHERE r.verified AND r.outcome='Failed' AND NOT EXISTS(SELECT 1 FROM student_course_results p WHERE p.student_id=s.id AND p.course_id=c.id AND p.verified AND p.outcome='Passed') AND """
                + sc
                + " ORDER BY s.id"
                + tail,
                sp,
            )
        return db.all(
            """SELECT s.register_number,s.name AS student,c.code,c.title,d.code AS department,cs.number AS semester,a.attended,a.conducted,round(100.0*a.attended/a.conducted,1) AS attendance_percent,a.status,a.repeat_approved FROM attendance_records a JOIN students s ON s.id=a.student_id JOIN courses c ON c.id=a.course_id JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN curriculum_semesters cs ON cs.id=c.semester_id WHERE """
            + sc
            + (" AND a.status='Debarred'" if key != "attendance" else "")
            + " ORDER BY d.code,s.id"
            + tail,
            sp,
        )
    if key == "regulation-distribution":
        return db.all(
            "SELECT r.code AS regulation,count(*) AS students FROM students s JOIN regulations r ON r.id=s.regulation_id WHERE s.deleted_at IS NULL AND "
            + sc
            + " GROUP BY r.code ORDER BY r.code",
            sp,
        )
    if key == "course-demand":
        return db.all(
            """SELECT c.code,c.title,r.code AS regulation,d.code AS department,cs.number AS semester,count(*) AS preferences,count(*) FILTER(WHERE x.rank=1) AS first_preference,count(*) FILTER(WHERE x.interest='Floating') AS floating_interest,ceil(count(*)/NULLIF((SELECT max(z.maximum_seats) FROM course_sections z JOIN offering_mappings m ON m.offering_id=z.offering_id WHERE m.course_id=c.id),0)::numeric) AS sections_required FROM course_preferences x JOIN courses c ON c.id=x.course_id JOIN regulations r ON r.id=c.regulation_id JOIN programmes p ON p.id=c.programme_id JOIN departments d ON d.id=p.department_id JOIN curriculum_semesters cs ON cs.id=c.semester_id WHERE """
            + ds
            + " GROUP BY c.id,r.code,d.code,cs.number ORDER BY preferences DESC"
            + tail,
            dp,
        )
    if key in [
        "credit-completion",
        "graduation-risk",
        "student-progression",
        "academic-risk",
    ]:
        return db.all(
            """WITH earned AS (SELECT student_id,sum(credits) AS credits FROM verified_credit_recognition GROUP BY student_id) SELECT s.register_number,s.name AS student,d.code AS department,r.code AS regulation,cs.number AS semester,COALESCE(e.credits,0) AS earned,g.total_credits AS target,GREATEST(g.total_credits-COALESCE(e.credits,0),0) AS pending,s.status FROM students s JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id JOIN regulations r ON r.id=s.regulation_id JOIN curriculum_semesters cs ON cs.id=s.semester_id LEFT JOIN earned e ON e.student_id=s.id LEFT JOIN graduation_requirements g ON g.programme_id=p.id AND g.regulation_id=r.id WHERE s.deleted_at IS NULL AND """
            + sc
            + (
                " AND cs.number>=p.semesters-1 AND COALESCE(e.credits,0)<g.total_credits"
                if key == "graduation-risk"
                else (
                    " AND (s.status<>'Active' OR EXISTS(SELECT 1 FROM attendance_records a WHERE a.student_id=s.id AND a.status IN('Debarred','Shortage')))"
                    if key == "academic-risk"
                    else ""
                )
            )
            + " ORDER BY s.id"
            + tail,
            sp,
        )
    from .analytics import refresh_snapshots

    refresh_snapshots(db, u)
    if key == "prerequisite-blocked":
        return db.all(
            "SELECT s.register_number,s.name AS student,b.value->>'course' AS course,b.value->>'reason' AS reason FROM eligibility_snapshots a JOIN students s ON s.id=a.student_id CROSS JOIN LATERAL jsonb_array_elements(a.prerequisite_blocks) b WHERE a.academic_year_id=%s AND "
            + sc
            + " ORDER BY s.id"
            + tail,
            [year_id(db)] + sp,
        )
    return db.all(
        "SELECT s.register_number,s.name AS student,d.code AS department,a.eligible_courses,a.floating_courses,a.summary AS outcome,a.evaluated_at FROM eligibility_snapshots a JOIN students s ON s.id=a.student_id JOIN programmes p ON p.id=s.programme_id JOIN departments d ON d.id=p.department_id WHERE a.academic_year_id=%s AND a.eligible_courses"
        + (">0" if key == "eligible" else "=0")
        + " AND "
        + sc
        + " ORDER BY s.id"
        + tail,
        [year_id(db)] + sp,
    )
