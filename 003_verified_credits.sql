ALTER TABLE course_categories ADD COLUMN mandatory_for_degree boolean NOT NULL DEFAULT false;
-- Recognition is projected into each student's own curriculum; equivalence never replaces the original result.
CREATE VIEW verified_credit_recognition AS
WITH passed AS (SELECT DISTINCT r.student_id,r.course_id FROM student_course_results r WHERE r.verified AND r.outcome='Passed'),
recognised AS (
 SELECT p.student_id,t.id AS course_id,t.code,t.title,t.credits,t.category_id,t.programme_id,t.regulation_id
 FROM passed p JOIN students s ON s.id=p.student_id
 CROSS JOIN LATERAL (SELECT c.* FROM courses c WHERE c.regulation_id=s.regulation_id AND c.programme_id=s.programme_id
 AND (c.id=p.course_id OR EXISTS(SELECT 1 FROM course_equivalences e WHERE e.approved AND e.recognise_credit AND current_date BETWEEN e.valid_from AND e.valid_until AND e.course_a=LEAST(p.course_id,c.id) AND e.course_b=GREATEST(p.course_id,c.id)))
 ORDER BY (c.id=p.course_id) DESC,c.id LIMIT 1) t),
keyed AS (SELECT r.*,LEAST(r.course_id,COALESCE((SELECT min(c.id) FROM course_equivalences e JOIN courses c ON c.id=CASE WHEN e.course_a=r.course_id THEN e.course_b ELSE e.course_a END WHERE (e.course_a=r.course_id OR e.course_b=r.course_id) AND e.approved AND e.recognise_credit AND current_date BETWEEN e.valid_from AND e.valid_until AND c.programme_id=r.programme_id AND c.regulation_id=r.regulation_id),r.course_id)) AS recognition_key FROM recognised r)
SELECT DISTINCT ON(k.student_id,k.recognition_key) k.student_id,k.course_id,k.recognition_key,k.code,k.title,k.credits,cc.elective,cc.mandatory_for_degree FROM keyed k JOIN course_categories cc ON cc.id=k.category_id ORDER BY k.student_id,k.recognition_key,k.course_id;
INSERT INTO schema_migrations(version) VALUES('003_verified_credits');
