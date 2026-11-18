ALTER TABLE course_enrolments ADD COLUMN attempt integer NOT NULL DEFAULT 1 CHECK(attempt>0);
ALTER TABLE course_enrolments DROP CONSTRAINT course_enrolments_status_check;
ALTER TABLE course_enrolments ADD CONSTRAINT course_enrolments_status_check CHECK(status IN('Registered','Waitlisted','Pending approval','Dropped','Rejected','Completed','Failed','Debarred'));
ALTER TABLE attendance_records ADD COLUMN attempt integer NOT NULL DEFAULT 1 CHECK(attempt>0);
ALTER TABLE attendance_records DROP CONSTRAINT attendance_records_student_id_course_id_academic_year_id_key;
ALTER TABLE attendance_records ADD UNIQUE(student_id,course_id,academic_year_id,attempt);
UPDATE course_enrolments e SET attempt=1+(SELECT COALESCE(max(r.attempt),0) FROM student_course_results r WHERE r.student_id=e.student_id AND r.course_id=e.course_id AND r.verified) WHERE e.basket IN('Repeat','Backlog') AND e.status IN('Registered','Waitlisted','Pending approval');
INSERT INTO schema_migrations(version) VALUES('004_attempt_lifecycle');
