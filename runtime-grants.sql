-- Operator only. Run against the dedicated DSU database, AFTER its four migrations.
-- A DBA must provision a NEW LOGIN named dsu_cfcs_runtime (not a schema/database owner,
-- not a member of the migration role, provider admin role or other privileged roles).
-- Set its password privately. This file never creates a password or stores a URL.
-- Use a dedicated public schema. Review permissions after each schema change.
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='dsu_cfcs_runtime' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls) THEN
    RAISE EXCEPTION 'Provision a separate, unprivileged login named dsu_cfcs_runtime first.';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.member WHERE r.rolname='dsu_cfcs_runtime') OR
     EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname=current_database() AND datdba='dsu_cfcs_runtime'::regrole) OR
     EXISTS (SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname='public' AND nspowner='dsu_cfcs_runtime'::regrole) OR
     EXISTS (SELECT 1 FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relowner='dsu_cfcs_runtime'::regrole) THEN
    RAISE EXCEPTION 'Runtime role must not own database objects or have role memberships. Use a fresh non-owner login.';
  END IF;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO dsu_cfcs_runtime;
-- This does not grant CREATE, grant options, TRUNCATE or ownership.
GRANT SELECT ON TABLE
  public.academic_audit_logs,
  public.academic_exceptions,
  public.academic_years,
  public.admission_batches,
  public.assessment_schemes,
  public.attendance_records,
  public.classrooms,
  public.course_categories,
  public.course_enrolments,
  public.course_equivalences,
  public.course_offerings,
  public.course_preferences,
  public.course_prerequisite_groups,
  public.course_prerequisites,
  public.course_sections,
  public.courses,
  public.credit_limits,
  public.curriculum_semesters,
  public.departments,
  public.eligibility_snapshots,
  public.exception_policies,
  public.faculty,
  public.faculty_availability,
  public.faculty_course_competencies,
  public.faculty_qualifications,
  public.faculty_workloads,
  public.graduation_requirements,
  public.import_batches,
  public.laboratories,
  public.login_attempts,
  public.offering_mappings,
  public.pending_requirements,
  public.permissions,
  public.programmes,
  public.registration_approvals,
  public.registration_windows,
  public.regulation_migrations,
  public.regulations,
  public.role_permissions,
  public.roles,
  public.schema_migrations,
  public.schools,
  public.sessions,
  public.student_academic_records,
  public.student_course_results,
  public.students,
  public.timetable_slots,
  public.users,
  public.verified_credit_recognition,
  public.workspace_metadata TO dsu_cfcs_runtime;
GRANT INSERT, UPDATE ON TABLE
  public.academic_exceptions,
  public.academic_years,
  public.admission_batches,
  public.assessment_schemes,
  public.attendance_records,
  public.classrooms,
  public.course_categories,
  public.course_enrolments,
  public.course_equivalences,
  public.course_offerings,
  public.course_preferences,
  public.course_prerequisite_groups,
  public.course_prerequisites,
  public.course_sections,
  public.courses,
  public.credit_limits,
  public.curriculum_semesters,
  public.departments,
  public.eligibility_snapshots,
  public.exception_policies,
  public.faculty,
  public.faculty_availability,
  public.faculty_course_competencies,
  public.faculty_qualifications,
  public.faculty_workloads,
  public.graduation_requirements,
  public.import_batches,
  public.laboratories,
  public.offering_mappings,
  public.pending_requirements,
  public.programmes,
  public.registration_approvals,
  public.registration_windows,
  public.regulations,
  public.schools,
  public.student_academic_records,
  public.student_course_results,
  public.students,
  public.timetable_slots,
  public.users TO dsu_cfcs_runtime;
GRANT INSERT ON TABLE
  public.academic_audit_logs,
  public.login_attempts,
  public.regulation_migrations,
  public.role_permissions,
  public.sessions TO dsu_cfcs_runtime;
GRANT DELETE ON TABLE public.sessions, public.login_attempts, public.role_permissions TO dsu_cfcs_runtime;
-- Limit sequence privileges to sequences owned by application table columns.
DO $$ DECLARE seq record; BEGIN
  FOR seq IN SELECT DISTINCT pg_catalog.pg_get_serial_sequence(format('%I.%I',table_schema,table_name),column_name) AS name
    FROM information_schema.columns WHERE table_schema='public' AND is_identity='YES'
    AND table_name=ANY(ARRAY['academic_audit_logs','academic_exceptions','academic_years','admission_batches','assessment_schemes','attendance_records','classrooms','course_categories','course_enrolments','course_equivalences','course_offerings','course_preferences','course_prerequisite_groups','course_prerequisites','course_sections','courses','credit_limits','curriculum_semesters','departments','eligibility_snapshots','exception_policies','faculty','faculty_availability','faculty_course_competencies','faculty_qualifications','faculty_workloads','graduation_requirements','import_batches','laboratories','login_attempts','offering_mappings','pending_requirements','programmes','registration_approvals','registration_windows','regulation_migrations','regulations','role_permissions','schools','sessions','student_academic_records','student_course_results','students','timetable_slots','users'])
  LOOP
    IF seq.name IS NOT NULL THEN EXECUTE format('GRANT USAGE ON SEQUENCE %s TO dsu_cfcs_runtime',seq.name::regclass); END IF;
  END LOOP;
END $$;
COMMIT;
