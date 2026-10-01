"""Regression cases found while checking filtered pages and unusual automata."""
import copy
import csv
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import app as automata


class EdgeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = automata.DB_PATH
        automata.DB_PATH = Path(self.temp.name) / "test.db"
        automata.init_db()
        automata.seed_example_exercise_if_empty()
        self.client = automata.app.test_client()

    def tearDown(self):
        automata.DB_PATH = self.old_path
        self.temp.cleanup()

    def login(self, role="teacher"):
        self.client.post("/login", data={"username": role + "1", "password": role + "123"})

    def test_filtered_access_save_preserves_hidden_and_archived_assignments(self):
        self.login()
        self.client.post("/teacher/exercises/preset/contains-ab")
        with sqlite3.connect(automata.DB_PATH) as db:
            active = db.execute("INSERT INTO student_groups(teacher_id,name,created_at) VALUES(1,'Active',?)", (automata.now_iso(),)).lastrowid
            archived = db.execute("INSERT INTO student_groups(teacher_id,name,is_archived,created_at) VALUES(1,'Archived',1,?)", (automata.now_iso(),)).lastrowid
            db.executemany("INSERT INTO exercise_groups VALUES(?,?)", [(1, active), (2, archived)])
        self.client.post("/teacher/exercises/access?q=Contains", data={"groups_2": str(active)})
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT exercise_id,group_id FROM exercise_groups ORDER BY exercise_id,group_id").fetchall(), [(1, active), (2, active), (2, archived)])
        self.client.post("/teacher/exercises/access?q=no-such-title", data={})
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exercise_groups").fetchone()[0], 3)

    def test_edit_does_not_discard_an_archived_group_restriction(self):
        self.login()
        with sqlite3.connect(automata.DB_PATH) as db:
            gid = db.execute("INSERT INTO student_groups(teacher_id,name,is_archived,created_at) VALUES(1,'Archived',1,?)", (automata.now_iso(),)).lastrowid
            db.execute("INSERT INTO exercise_groups VALUES(1,?)", (gid,))
        form = automata.dfa_to_initial_form(automata.canonical_preset_by_slug("even-a")["dfa"])
        form.update(title="Renamed", require_determinism="on", require_minimality="on")
        self.assertEqual(self.client.post("/teacher/exercises/1/edit", data=form).status_code, 302)
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT group_id FROM exercise_groups WHERE exercise_id=1").fetchall(), [(gid,)])

    def test_empty_filter_export_has_no_data_rows(self):
        self.login()
        rows = list(csv.DictReader(io.StringIO(self.client.get("/teacher/export/progress.csv?q=no-such-title").get_data(as_text=True))))
        self.assertEqual(rows, [])
        with automata.app.app_context():
            _, exercises, overview, _ = automata.build_teacher_overview(automata.get_db(), 1, exercises=[])
            self.assertEqual(exercises, [])
            self.assertTrue(all(row["total_exercises"] == 0 for row in overview))

    def test_progress_export_marks_old_answers_as_needing_resubmission(self):
        self.login("student")
        even = automata.canonical_preset_by_slug("even-a")["dfa"]
        self.client.post("/student/exercises/1", data=automata.dfa_to_initial_form(even))
        self.login()
        odd = copy.deepcopy(even)
        odd["accepting"] = ["q1"]
        form = automata.dfa_to_initial_form(odd)
        form.update(title="Changed", require_determinism="on", require_minimality="on")
        self.client.post("/teacher/exercises/1/edit", data=form)
        row = next(csv.DictReader(io.StringIO(self.client.get("/teacher/export/progress.csv").get_data(as_text=True))))
        self.assertEqual(row["status"], "Needs resubmission")
        self.assertEqual((row["equivalent"], row["minimal"]), ("", ""))

    def test_regex_cannot_bypass_reserved_alphabet_validation(self):
        self.login()
        for pattern in [",", ";", "=", "a,", "(a|;)*"]:
            with self.subTest(pattern=pattern):
                nfa, errors = automata.regex_to_nfa(pattern)
                self.assertIsNone(nfa)
                self.assertTrue(errors)
                self.assertEqual(self.client.post("/api/source-preview", json={"source_kind": "regex", "source_payload": pattern}).status_code, 400)

    def test_named_dead_state_is_kept_in_feedback(self):
        dfa = {"states": ["q0", "__dead__"], "alphabet": ["a"], "start": "q0", "accepting": [], "transitions": {"q0": {"a": "__dead__"}, "__dead__": {"a": "__dead__"}}}
        result = automata.evaluate_submission(dfa, dfa)
        self.assertIn(["__dead__", "q0"], result["merge_groups"])
        self.assertEqual({s for group in result["partition_snapshots"][-1]["blocks"] for s in group}, set(dfa["states"]))

    def test_csv_preserves_numbers_and_neutralizes_formula_cells(self):
        values = ["=1+1", "+SUM(1)", "@SUM(1)", "  =2+2", "\t=3+3", "-command", 12, "Normal title"]
        text = automata.csv_response("probe.csv", [{"value": v} for v in values], ["value"]).get_data(as_text=True)
        exported = [row["value"] for row in csv.DictReader(io.StringIO(text))]
        self.assertTrue(all(v.startswith("'") for v in exported[:6]))
        self.assertEqual(exported[6:], ["12", "Normal title"])

    def test_session_role_is_refreshed_from_database(self):
        self.login("student")
        with self.client.session_transaction() as session:
            session["role"] = "teacher"
        self.assertEqual(self.client.get("/", follow_redirects=True).status_code, 200)
        with self.client.session_transaction() as session:
            self.assertEqual(session["role"], "student")

    def test_cross_site_changes_and_untrusted_hosts_are_rejected(self):
        self.login()
        self.assertEqual(self.client.post("/teacher/groups", data={"name": "Unexpected"}, headers={"Origin": "https://other.example"}).status_code, 403)
        self.assertEqual(self.client.post("/teacher/groups", data={"name": "Unexpected"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.get("/teacher", headers={"Host": "other.example"}).status_code, 400)
        with sqlite3.connect(automata.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student_groups").fetchone()[0], 0)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.get("/teacher").status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 302)
        self.assertTrue(self.client.get("/teacher").location.endswith("/login"))


if __name__ == "__main__":
    unittest.main()
