import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as automata
from seed_demo import seed_demo


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = automata.DB_PATH
        self.old_testing = automata.app.config["TESTING"]
        automata.DB_PATH = Path(self.temp.name) / "test.db"
        automata.app.config["TESTING"] = True
        automata.init_db()
        automata.seed_example_exercise_if_empty()
        self.client = automata.app.test_client()

    def tearDown(self):
        automata.DB_PATH = self.old_path
        automata.app.config["TESTING"] = self.old_testing
        self.temp.cleanup()

    def login(self, role):
        response = self.client.post("/login", data={
            "username": f"{role}1", "password": f"{role}123",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["role"], role)

    def test_teacher_pages_and_source_preview(self):
        self.login("teacher")
        for path in ["/teacher", "/teacher/exercises/new", "/teacher/groups",
                     "/teacher/students", "/teacher/exercises/access"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        response = self.client.post("/api/source-preview", json={
            "source_kind": "regex", "source_payload": "(a|b)*ab",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_anonymous_and_student_role_access(self):
        self.assertEqual(self.client.get("/teacher").status_code, 302)
        self.login("student")
        self.assertEqual(self.client.get("/teacher").status_code, 302)
        self.assertEqual(self.client.post("/api/source-preview", json={}).status_code, 302)

    def test_student_submission_is_saved_with_feedback(self):
        self.login("student")
        self.assertEqual(self.client.get("/student/exercises/1").status_code, 200)
        form = automata.dfa_to_initial_form(
            automata.canonical_preset_by_slug("even-a")["dfa"]
        )
        response = self.client.post("/student/exercises/1", data=form)
        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(automata.DB_PATH) as db:
            row = db.execute("SELECT result_json FROM submissions").fetchone()
        result = json.loads(row[0])
        self.assertTrue(all(result[key] for key in ["equivalent", "deterministic", "minimal"]))
        self.assertEqual(result["score"]["earned"], result["score"]["total"])
        self.assertEqual(self.client.get("/student/submissions").status_code, 200)

    def test_demo_seed_is_repeatable(self):
        self.assertEqual(seed_demo(), 4)
        self.assertEqual(seed_demo(), 0)
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM exercises").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)

    def test_changed_exercise_alphabet_is_rejected_without_saving(self):
        self.login("student")
        for alphabet in [["a"], ["a", "b", "c"]]:
            with self.subTest(alphabet=alphabet):
                answer = copy.deepcopy(automata.canonical_preset_by_slug("even-a")["dfa"])
                answer["alphabet"] = alphabet
                for state, edges in answer["transitions"].items():
                    answer["transitions"][state] = {symbol: edges.get(symbol, state) for symbol in alphabet}
                response = self.client.post("/student/exercises/1", data=automata.dfa_to_initial_form(answer))
                self.assertEqual(response.status_code, 200)
                self.assertIn("Alphabet must match the exercise: a, b.", response.get_data(as_text=True))
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM submissions").fetchone()[0], 0)

    def test_alphabet_order_does_not_change_the_submission(self):
        self.login("student")
        answer = copy.deepcopy(automata.canonical_preset_by_slug("even-a")["dfa"])
        answer["alphabet"] = ["b", "a"]
        response = self.client.post("/student/exercises/1", data=automata.dfa_to_initial_form(answer))
        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(automata.DB_PATH) as db:
            row = db.execute("SELECT result_json FROM submissions").fetchone()
        self.assertTrue(json.loads(row[0])["equivalent"])

    def exercise_form(self, dfa=None):
        form = automata.dfa_to_initial_form(dfa or automata.canonical_preset_by_slug('even-a')['dfa'])
        form.update(title='Parity exercise', require_determinism='on', require_minimality='on')
        return form

    def test_bad_nfa_preview_returns_400(self):
        self.login('teacher')
        for source in ['[]', 'null', '{"states":null}', '{"transitions":[]}']:
            response = self.client.post('/api/source-preview', json={'source_kind':'nfa', 'source_payload':source})
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.get_json()['errors'])

    def test_invalid_dates_and_groups_do_not_write_exercises(self):
        self.login('teacher')
        for route in ['/teacher/exercises/new', '/teacher/exercises/1/edit']:
            for field, value in [('available_from','not-a-date'), ('available_until','2026-99-01'), ('group_ids','no-number')]:
                form = self.exercise_form(); form[field] = value
                with self.subTest(route=route, field=field):
                    response = self.client.post(route, data=form)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn('error', response.get_data(as_text=True))
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM exercises').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT revision FROM exercises').fetchone()[0], 1)

    def test_group_assignments_require_active_owned_group(self):
        self.login('teacher')
        with sqlite3.connect(automata.DB_PATH) as db:
            teacher = db.execute("SELECT id FROM users WHERE username='teacher1'").fetchone()[0]
            other = db.execute("INSERT INTO users(username,password_hash,role) VALUES('other','fixture','teacher')").lastrowid
            ids = [db.execute('INSERT INTO student_groups(teacher_id,name,is_archived,created_at) VALUES(?,?,?,?)',
                     (owner, name, archived, automata.now_iso())).lastrowid
                   for owner,name,archived in [(other,'Other',0),(teacher,'Archived',1),(teacher,'Own',0)]]
        for gid in [*ids[:2], 'invalid']:
            self.client.post('/teacher/students', data={'email':'rejected@example.com','group_id':gid})
            self.client.post('/teacher/students/2/group', data={'group_id':gid})
            self.client.post('/teacher/exercises/preset/even-a', data={'group_ids':gid})
            form=self.exercise_form(); form['group_ids']=gid
            self.client.post('/teacher/exercises/new', data=form)
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertIsNone(db.execute("SELECT id FROM users WHERE email='rejected@example.com'").fetchone())
            self.assertIsNone(db.execute("SELECT group_id FROM users WHERE username='student1'").fetchone()[0])
            self.assertEqual(db.execute('SELECT count(*) FROM exercises').fetchone()[0], 1)
        self.client.post('/teacher/students', data={'email':'accepted@example.com','group_id':ids[2]})
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT group_id FROM users WHERE email='accepted@example.com'").fetchone()[0], ids[2])

    def test_access_matrix_validates_all_rows_before_writing(self):
        self.login('teacher'); seed_demo()
        with sqlite3.connect(automata.DB_PATH) as db:
            gid=db.execute("INSERT INTO student_groups(teacher_id,name,created_at) VALUES(1,'Own',?)", (automata.now_iso(),)).lastrowid
            db.execute('INSERT INTO exercise_groups VALUES(1,?)', (gid,))
        self.client.post('/teacher/exercises/access', data={'groups_5':str(gid),'groups_1':'invalid'})
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute('SELECT exercise_id,group_id FROM exercise_groups').fetchall(), [(1,gid)])

    def test_first_password_change_is_enforced_on_every_protected_route(self):
        with sqlite3.connect(automata.DB_PATH) as db:
            db.execute("UPDATE users SET must_change_password=1 WHERE username='student1'")
        response=self.client.post('/login', data={'username':'student1','password':'student123'})
        self.assertTrue(response.location.endswith('/account/password'))
        for path in ['/student','/student/submissions','/student/exercises/1']:
            self.assertTrue(self.client.get(path).location.endswith('/account/password'))
        self.assertTrue(self.client.post('/student/exercises/1', data=self.exercise_form()).location.endswith('/account/password'))
        self.assertEqual(self.client.get('/account/password').status_code, 200)
        self.client.post('/account/password', data={'old_password':'student123','new_password':'changed123','confirm_password':'changed123'})
        self.assertEqual(self.client.get('/student').status_code, 200)

    def test_target_update_marks_history_stale_until_resubmission(self):
        self.login('student')
        even=automata.canonical_preset_by_slug('even-a')['dfa']
        self.client.post('/student/exercises/1', data=automata.dfa_to_initial_form(even))
        self.login('teacher')
        odd=copy.deepcopy(even); odd['accepting']=['q1']
        self.assertEqual(self.client.post('/teacher/exercises/1/edit', data=self.exercise_form(odd)).status_code,302)
        with automata.app.app_context():
            db=automata.get_db(); item=automata.build_student_progress(db,2)[1]
            self.assertFalse(item['completed']); self.assertTrue(item['stale']); self.assertIsNone(item['best_score'])
            self.assertFalse(automata.get_latest_submissions_map(db)[(2,1)]['completed'])
        detail=self.client.get('/teacher/exercises/1').get_data(as_text=True)
        self.assertIn('Previous version', detail); self.assertIn('Needs resubmission', detail)
        csv=self.client.get('/teacher/exercises/1/submissions.csv').get_data(as_text=True)
        self.assertIn('exercise_revision',csv); self.assertIn('Previous version',csv)
        self.login('student')
        self.assertIn('Exercise updated; submit again.', self.client.get('/student').get_data(as_text=True))
        self.assertIn('Previous version', self.client.get('/student/submissions').get_data(as_text=True))
        self.client.post('/student/exercises/1', data=automata.dfa_to_initial_form(odd))
        with automata.app.app_context():
            db=automata.get_db(); item=automata.build_student_progress(db,2)[1]
            self.assertTrue(item['completed']); self.assertFalse(item['stale'])
            self.assertEqual([r['exercise_revision'] for r in db.execute('SELECT * FROM submissions ORDER BY id')], [1,2])

    def test_non_grading_edit_keeps_revision_and_completion(self):
        self.login('student'); self.client.post('/student/exercises/1', data=self.exercise_form())
        self.login('teacher'); form=self.exercise_form(); form['title']='Renamed title'
        self.client.post('/teacher/exercises/1/edit', data=form)
        with automata.app.app_context():
            self.assertEqual(automata.get_db().execute('SELECT revision FROM exercises').fetchone()['revision'],1)
            self.assertTrue(automata.build_student_progress(automata.get_db(),2)[1]['completed'])

    def test_epsilon_exercise_creation_and_submission(self):
        self.login('teacher')
        response=self.client.post('/teacher/exercises/new', data={'title':'Empty word','source_kind':'regex','regex_source':'ε','require_minimality':'on','require_determinism':'on'})
        self.assertEqual(response.status_code,302)
        self.login('student')
        self.assertEqual(self.client.post('/student/exercises/2', data={'states':'q0','alphabet':'','start':'q0','accepting':'q0','transitions':''}).status_code,200)
        with automata.app.app_context():
            self.assertTrue(automata.build_student_progress(automata.get_db(),2)[2]['completed'])

    def test_revision_migration_preserves_legacy_data(self):
        self.login('student'); self.client.post('/student/exercises/1', data=self.exercise_form())
        with sqlite3.connect(automata.DB_PATH) as db:
            db.execute('ALTER TABLE exercises DROP COLUMN revision')
            db.execute('ALTER TABLE submissions DROP COLUMN exercise_revision')
        automata.init_db(); automata.init_db()
        with automata.app.app_context():
            db=automata.get_db()
            self.assertEqual(db.execute('SELECT count(*) FROM submissions').fetchone()[0],1)
            self.assertTrue(automata.build_student_progress(db,2)[1]['completed'])


if __name__ == "__main__":
    unittest.main()
