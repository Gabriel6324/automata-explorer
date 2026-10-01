"""Add reproducible exercise examples without distributing a user database."""

import json
import sqlite3

import app as automata


def seed_demo():
    automata.init_db()
    automata.seed_example_exercise_if_empty()
    presets = [
        {**item, "source_kind": "dfa", "source_payload": ""}
        for item in automata.canonical_exercise_presets()
        if item["slug"] != "even-a"
    ]
    nfa = {
        "states": ["s0", "s1", "s2"],
        "alphabet": ["a", "b"],
        "start": "s0",
        "accepting": ["s2"],
        "transitions": {
            "s0": {"a": ["s0", "s1"], "b": ["s0"]},
            "s1": {"b": ["s2"]},
            "s2": {"a": ["s2"], "b": ["s2"]},
        },
    }
    for kind, title, payload in [
        ("regex", "Regex source example: Strings ending with ab", "(a|b)*ab"),
        ("nfa", "NFA source example: Contains substring ab", json.dumps(nfa)),
    ]:
        preview, errors = automata.source_preview_payload(kind, payload)
        if errors:
            raise ValueError("; ".join(errors))
        presets.append({
            "title": title,
            "description": "Build a minimal complete DFA for the specified language.",
            "difficulty": "Medium",
            "tags": f"demo, {kind}, conversion",
            "dfa": preview["dfa"],
            "source_kind": kind,
            "source_payload": payload,
        })

    added = 0
    with sqlite3.connect(automata.DB_PATH) as db:
        teacher = db.execute(
            "SELECT id FROM users WHERE username = 'teacher1' AND role = 'teacher'"
        ).fetchone()
        if teacher is None:
            raise RuntimeError("The demo teacher account is unavailable.")
        for item in presets:
            if db.execute(
                "SELECT 1 FROM exercises WHERE title = ? AND created_by = ?",
                (item["title"], teacher[0]),
            ).fetchone():
                continue
            db.execute(
                """INSERT INTO exercises
                (title, description, alphabet, target_dfa_json,
                 require_determinism, require_minimality, created_by,
                 created_at, difficulty, tags, source_kind, source_payload)
                VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?)""",
                (
                    item["title"], item["description"],
                    ",".join(item["dfa"]["alphabet"]), json.dumps(item["dfa"]),
                    teacher[0], automata.now_iso(), item["difficulty"],
                    item["tags"], item["source_kind"], item["source_payload"],
                ),
            )
            added += 1
    return added


if __name__ == "__main__":
    print(f"Demo exercises ready; added {seed_demo()} exercise(s).")
