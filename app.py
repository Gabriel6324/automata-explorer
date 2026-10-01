import csv
import io
import json
import os
import re
import secrets
import sqlite3
from collections import deque
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    Response,
)
from werkzeug.security import check_password_hash, generate_password_hash

# Basic paths and Flask configuration for the local prototype.
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AUTOMATA_DB_PATH", str(BASE_DIR / "automata.db")))
SECRET_KEY = os.environ.get("AUTOMATA_SECRET_KEY") or secrets.token_hex(32)

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
try:
    APP_TZ = ZoneInfo("Europe/London") if ZoneInfo else None
except Exception:
    APP_TZ = None


@app.before_request
def local_request_guard():
    try:
        host = urlsplit("http://" + request.host).hostname
    except ValueError:
        host = None
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return "Use the local application address.", 400
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin")
        try:
            same_origin = not origin or (urlsplit(origin).scheme in {"http", "https"} and urlsplit(origin).netloc.lower() == request.host.lower())
        except ValueError:
            same_origin = False
        if not same_origin or request.headers.get("Sec-Fetch-Site") == "cross-site":
            return "Cross-site changes are not allowed.", 403


@app.after_request
def local_response_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


# Store timestamps in the UK timezone when available.
def now_local():
    if APP_TZ is not None:
        return datetime.now(APP_TZ)
    return datetime.now()


def now_iso():
    return now_local().isoformat()


def parse_app_datetime(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TZ)
    return dt.astimezone(APP_TZ)


def parse_form_datetime(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=APP_TZ)
    return dt.astimezone(APP_TZ).isoformat()


def display_dt(value):
    dt = parse_app_datetime(value)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def html_dt_value(value):
    dt = parse_app_datetime(value)
    return dt.strftime("%Y-%m-%dT%H:%M") if dt else ""




# Normalise comma-separated tags for filtering and display.
def normalize_tags(raw):
    seen = []
    for part in (raw or "").split(","):
        tag = part.strip()
        if tag and tag.lower() not in {x.lower() for x in seen}:
            seen.append(tag)
    return seen


def tags_text(raw):
    return ", ".join(normalize_tags(raw))




# Built-in exercises used for seeding and quick teacher setup.
def canonical_exercise_presets():
    return [
        {
            "slug": "even-a",
            "title": "Even number of a",
            "description": "Build a DFA over {a,b} that accepts exactly the strings containing an even number of 'a'.",
            "difficulty": "Easy",
            "tags": "canonical, parity, equivalence",
            "dfa": {
                "states": ["q0", "q1"],
                "alphabet": ["a", "b"],
                "start": "q0",
                "accepting": ["q0"],
                "transitions": {
                    "q0": {"a": "q1", "b": "q0"},
                    "q1": {"a": "q0", "b": "q1"},
                },
            },
        },
        {
            "slug": "binary-divisible-3",
            "title": "Binary strings divisible by 3",
            "description": "Build a DFA over {0,1} that accepts the binary strings whose numeric value is divisible by 3.",
            "difficulty": "Medium",
            "tags": "canonical, arithmetic, minimisation",
            "dfa": {
                "states": ["q0", "q1", "q2"],
                "alphabet": ["0", "1"],
                "start": "q0",
                "accepting": ["q0"],
                "transitions": {
                    "q0": {"0": "q0", "1": "q1"},
                    "q1": {"0": "q2", "1": "q0"},
                    "q2": {"0": "q1", "1": "q2"},
                },
            },
        },
        {
            "slug": "contains-ab",
            "title": "Contains substring 'ab'",
            "description": "Build a DFA over {a,b} that accepts exactly the strings where 'ab' appears as a substring.",
            "difficulty": "Easy",
            "tags": "canonical, substring, feedback",
            "dfa": {
                "states": ["q0", "q1", "q2"],
                "alphabet": ["a", "b"],
                "start": "q0",
                "accepting": ["q2"],
                "transitions": {
                    "q0": {"a": "q1", "b": "q0"},
                    "q1": {"a": "q1", "b": "q2"},
                    "q2": {"a": "q2", "b": "q2"},
                },
            },
        },
    ]


def canonical_preset_by_slug(slug):
    for preset in canonical_exercise_presets():
        if preset["slug"] == slug:
            return preset
    return None
# Turn individual check results into a simple score label.
def score_summary(result, require_determinism=True, require_minimality=True):
    checks = [("equivalent", True)]
    if require_determinism:
        checks.append(("deterministic", True))
    if require_minimality:
        checks.append(("minimal", True))
    total = len(checks)
    earned = sum(1 for key, expected in checks if bool(result.get(key)) == expected)
    return {"earned": earned, "total": total, "label": f"{earned}/{total}"}


# Create a CSV download response for teacher exports.
def csv_response(filename, rows, headers):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        # CSV quoting alone does not stop a spreadsheet from evaluating formulas.
        safe_row = {
            key: "'" + value if isinstance(value, str) and
            (value.lstrip("\ufeff \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")))
            else value
            for key, value in row.items()
        }
        writer.writerow(safe_row)
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def request_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


app.jinja_env.globals.update(display_dt=display_dt, html_dt_value=html_dt_value, tags_text=tags_text)


# ---------------------------
# Database helpers
# ---------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def table_columns(db, table_name):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}


def ensure_column(db, table_name, column_name, ddl):
    if column_name not in table_columns(db, table_name):
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def init_db():
    # Create or upgrade the SQLite tables used by the prototype.
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('teacher', 'student'))
        );

        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            alphabet TEXT NOT NULL,
            target_dfa_json TEXT NOT NULL,
            require_determinism INTEGER NOT NULL DEFAULT 1,
            require_minimality INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            answer_dfa_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS student_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            year_label TEXT,
            is_archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        );


        CREATE TABLE IF NOT EXISTS exercise_groups (
            exercise_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            PRIMARY KEY (exercise_id, group_id),
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            FOREIGN KEY (group_id) REFERENCES student_groups(id)
        );
        """
    )

    ensure_column(db, "users", "email", "email TEXT")
    ensure_column(db, "users", "managed_by", "managed_by INTEGER")
    ensure_column(db, "users", "group_id", "group_id INTEGER")
    ensure_column(db, "users", "must_change_password", "must_change_password INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "users", "created_at", "created_at TEXT")
    ensure_column(db, "users", "initial_password_plain", "initial_password_plain TEXT")
    ensure_column(db, "users", "password_changed_at", "password_changed_at TEXT")

    ensure_column(db, "exercises", "available_from", "available_from TEXT")
    ensure_column(db, "exercises", "available_until", "available_until TEXT")
    ensure_column(db, "exercises", "tags", "tags TEXT")
    ensure_column(db, "exercises", "difficulty", "difficulty TEXT")
    ensure_column(db, "exercises", "source_kind", "source_kind TEXT NOT NULL DEFAULT 'dfa'")
    ensure_column(db, "exercises", "source_payload", "source_payload TEXT")
    ensure_column(db, "exercises", "revision", "revision INTEGER NOT NULL DEFAULT 1")
    ensure_column(db, "submissions", "exercise_revision", "exercise_revision INTEGER NOT NULL DEFAULT 1")

    users = [
        ("teacher1", generate_password_hash("teacher123"), "teacher", "teacher1@example.com"),
        ("student1", generate_password_hash("student123"), "student", "student1@example.com"),
    ]
    now = now_iso()
    for username, password_hash, role, email in users:
        cur.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, password_hash, role, email, now),
        )

    teacher = cur.execute("SELECT id FROM users WHERE username = 'teacher1'").fetchone()
    student = cur.execute("SELECT id FROM users WHERE username = 'student1'").fetchone()
    if teacher and student:
        cur.execute(
            "UPDATE users SET managed_by = COALESCE(managed_by, ?), created_at = COALESCE(created_at, ?) WHERE id = ?",
            (teacher["id"], now, student["id"]),
        )

    db.commit()
    db.close()


# Populate a fresh database with demo accounts, groups and exercises.
def seed_example_exercise_if_empty():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    count = cur.execute("SELECT COUNT(*) AS c FROM exercises").fetchone()["c"]
    if count == 0:
        teacher = cur.execute(
            "SELECT id FROM users WHERE username = 'teacher1'"
        ).fetchone()
        if teacher:
            dfa = {
                "states": ["q0", "q1"],
                "alphabet": ["a", "b"],
                "start": "q0",
                "accepting": ["q0"],
                "transitions": {
                    "q0": {"a": "q1", "b": "q0"},
                    "q1": {"a": "q0", "b": "q1"},
                },
            }
            cur.execute(
                """
                INSERT INTO exercises
                (title, description, alphabet, target_dfa_json, require_determinism, require_minimality, created_by, created_at)
                VALUES (?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    "Even number of 'a'",
                    "Build a DFA over {a,b} that accepts exactly the strings containing an even number of 'a'.",
                    "a,b",
                    json.dumps(dfa),
                    teacher["id"],
                    now_iso(),
                ),
            )
            db.commit()
    db.close()


# ---------------------------
# Auth helpers
# ---------------------------

def login_required(role=None):
    # Redirect unauthenticated users and enforce role-specific pages.
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))
            user = get_db().execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
            if user is None:
                session.clear()
                return redirect(url_for("login"))
            session["role"] = user["role"]
            session["username"] = user["username"]
            if user["must_change_password"] and request.endpoint != "change_password":
                return redirect(url_for("change_password"))
            if role and user["role"] != role:
                flash("You do not have permission to view that page.", "error")
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


# ---------------------------
# DFA helpers
# ---------------------------

# Parse small comma-separated form fields such as states and alphabets.
def split_csv(value):
    return [x.strip() for x in value.split(",") if x.strip()]


def automaton_field_errors(states, alphabet):
    errors = []
    if len(states) != len(set(states)):
        errors.append("State names must be unique.")
    if any(any(c in state for c in ",;=\r\n") for state in states):
        errors.append("State names cannot contain commas, semicolons, equals signs or line breaks.")
    if len(alphabet) != len(set(alphabet)):
        errors.append("Alphabet symbols must be unique.")
    if any(len(symbol) != 1 or symbol in ",;=ε" or symbol.isspace() for symbol in alphabet):
        errors.append("Alphabet symbols must be single characters; delimiters and ε are reserved.")
    return errors


def parse_transitions_text(text):
    transitions = {}
    seen = set()
    errors = []
    lines = [line.strip() for line in text.replace(";", "\n").splitlines() if line.strip()]
    for idx, line in enumerate(lines, start=1):
        if line.count("=") != 1 or line.count(",") != 1:
            errors.append(f"Line {idx}: use format state,symbol=next_state")
            continue
        left, right = line.split("=", 1)
        if "," not in left:
            errors.append(f"Line {idx}: use format state,symbol=next_state")
            continue
        state, symbol = [p.strip() for p in left.split(",", 1)]
        next_state = right.strip()
        if not state or not symbol or not next_state:
            errors.append(f"Line {idx}: missing state, symbol or next_state")
            continue
        key = (state, symbol)
        if key in seen:
            errors.append(f"Line {idx}: duplicate transition for ({state}, {symbol})")
            continue
        seen.add(key)
        transitions.setdefault(state, {})[symbol] = next_state
    return transitions, errors


# Build a DFA dictionary from the structured form fields.
def build_dfa_from_form(form, prefix=""):
    states = split_csv(form.get(f"{prefix}states", ""))
    alphabet = split_csv(form.get(f"{prefix}alphabet", ""))
    start = form.get(f"{prefix}start", "").strip()
    accepting = split_csv(form.get(f"{prefix}accepting", ""))
    transitions_text = form.get(f"{prefix}transitions", "")

    errors = []
    if not states:
        errors.append("States cannot be empty.")
    errors.extend(automaton_field_errors(states, alphabet))
    if not start:
        errors.append("Start state cannot be empty.")
    if start and start not in states:
        errors.append("Start state must be included in states.")
    for state in accepting:
        if state not in states:
            errors.append(f"Accepting state '{state}' is not in states.")

    transitions, transition_errors = parse_transitions_text(transitions_text)
    errors.extend(transition_errors)

    for src, mapping in transitions.items():
        if src not in states:
            errors.append(f"Transition source '{src}' is not in states.")
        for symbol, dst in mapping.items():
            if symbol not in alphabet:
                errors.append(f"Symbol '{symbol}' is not in alphabet.")
            if dst not in states:
                errors.append(f"Transition target '{dst}' is not in states.")

    dfa = {
        "states": states,
        "alphabet": alphabet,
        "start": start,
        "accepting": accepting,
        "transitions": transitions,
    }
    return dfa, errors

# Convert the teacher regex source into tokens for the small regex parser.
def regex_tokens(pattern):
    tokens = []
    for ch in (pattern or '').strip():
        if ch.isspace():
            continue
        tokens.append(ch)
    return tokens


def regex_with_concat(tokens):
    result = []
    def is_atom(tok):
        return tok not in {'|', '.', '*', '(', ')'}
    for i, tok in enumerate(tokens):
        result.append(tok)
        if i == len(tokens) - 1:
            continue
        nxt = tokens[i + 1]
        if (is_atom(tok) or tok in {')', '*'}) and (is_atom(nxt) or nxt == '('):
            result.append('.')
    return result


def regex_to_postfix(pattern):
    tokens = regex_with_concat(regex_tokens(pattern))
    if not tokens:
        return [], ['Regular expression source cannot be empty. Enter a regex in the “Regular expression source” box below, or switch Target source type back to DFA.']
    alphabet = list(dict.fromkeys(tok for tok in tokens if tok not in {'|', '.', '*', '(', ')', 'ε', 'e'}))
    symbol_errors = automaton_field_errors([], alphabet)
    if symbol_errors:
        return [], symbol_errors
    output = []
    stack = []
    errors = []
    prec = {'|': 1, '.': 2, '*': 3}
    for tok in tokens:
        if tok == '(':
            stack.append(tok)
        elif tok == ')':
            while stack and stack[-1] != '(':
                output.append(stack.pop())
            if not stack:
                errors.append('Regex has mismatched parentheses.')
                break
            stack.pop()
        elif tok in prec:
            if tok == '*':
                while stack and stack[-1] != '(' and prec.get(stack[-1], 0) > prec[tok]:
                    output.append(stack.pop())
            else:
                while stack and stack[-1] != '(' and prec.get(stack[-1], 0) >= prec[tok]:
                    output.append(stack.pop())
            stack.append(tok)
        else:
            output.append(tok)
    while stack:
        op = stack.pop()
        if op == '(':
            errors.append('Regex has mismatched parentheses.')
            break
        output.append(op)
    return output, errors


# Build an NFA from the supported regular-expression syntax.
def regex_to_nfa(pattern):
    postfix, errors = regex_to_postfix(pattern)
    if errors:
        return None, errors
    counter = 0
    transitions = {}
    states = []

    def new_state():
        nonlocal counter
        sid = f'r{counter}'
        counter += 1
        states.append(sid)
        transitions.setdefault(sid, {})
        return sid

    def add_edge(src, sym, dst):
        transitions.setdefault(src, {}).setdefault(sym, set()).add(dst)

    stack = []
    alphabet = []
    for tok in postfix:
        if tok not in {'|', '.', '*'}:
            s = new_state()
            e = new_state()
            sym = '' if tok in {'ε', 'e'} else tok
            add_edge(s, sym, e)
            if sym and sym not in alphabet:
                alphabet.append(sym)
            stack.append((s, e))
        elif tok == '.':
            if len(stack) < 2:
                return None, ['Regex concatenation is incomplete.']
            b = stack.pop()
            a = stack.pop()
            add_edge(a[1], '', b[0])
            stack.append((a[0], b[1]))
        elif tok == '|':
            if len(stack) < 2:
                return None, ['Regex union is incomplete.']
            b = stack.pop()
            a = stack.pop()
            s = new_state()
            e = new_state()
            add_edge(s, '', a[0])
            add_edge(s, '', b[0])
            add_edge(a[1], '', e)
            add_edge(b[1], '', e)
            stack.append((s, e))
        elif tok == '*':
            if not stack:
                return None, ['Regex star is incomplete.']
            a = stack.pop()
            s = new_state()
            e = new_state()
            add_edge(s, '', a[0])
            add_edge(s, '', e)
            add_edge(a[1], '', a[0])
            add_edge(a[1], '', e)
            stack.append((s, e))
    if len(stack) != 1:
        return None, ['Regex could not be parsed into a single automaton.']
    start, accept = stack.pop()
    return {
        'states': states,
        'alphabet': alphabet,
        'start': start,
        'accepting': [accept],
        'transitions': transitions,
    }, []


def normalize_nfa_symbol(symbol):
    raw = (symbol or '').strip()
    if raw in {'ε', 'eps', 'epsilon', 'lambda', ''}:
        return ''
    return raw


# Validate teacher-entered NFA JSON before subset construction.
def parse_nfa_json(text):
    if not (text or '').strip():
        return None, ['NFA JSON source cannot be empty. Enter NFA JSON in the “NFA JSON source” box below, or switch Target source type back to DFA.']
    try:
        raw = json.loads(text)
    except Exception as exc:
        return None, [f'Invalid NFA JSON: {exc}']
    if not isinstance(raw, dict):
        return None, ['NFA JSON must be an object.']
    errors = []
    fields = {}
    for name in ('states', 'alphabet', 'accepting'):
        value = raw.get(name, [])
        if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
            errors.append(f'NFA {name} must be an array of non-empty strings.')
            fields[name] = []
        else:
            fields[name] = [x.strip() for x in value]
    states, alphabet, accepting = fields['states'], fields['alphabet'], fields['accepting']
    start = raw.get('start', '')
    if not isinstance(start, str):
        errors.append('NFA start must be a string.')
        start = ''
    start = start.strip()
    errors.extend(automaton_field_errors(states, alphabet))
    transitions = {}
    if not states:
        errors.append('NFA states cannot be empty.')
    if not start:
        errors.append('NFA start state cannot be empty.')
    if start and start not in states:
        errors.append('NFA start state must be listed in states.')
    for st in accepting:
        if st not in states:
            errors.append(f"NFA accepting state '{st}' is not in states.")
    raw_trans = raw.get('transitions', {})
    if not isinstance(raw_trans, dict):
        errors.append('NFA transitions must be an object.')
        raw_trans = {}
    for src, mapping in raw_trans.items():
        src = str(src).strip()
        if src not in states:
            errors.append(f"NFA transition source '{src}' is not in states.")
        transitions.setdefault(src, {})
        if not isinstance(mapping, dict):
            errors.append(f"NFA transitions for '{src}' must be an object.")
            continue
        for sym, dsts in mapping.items():
            norm_sym = normalize_nfa_symbol(sym)
            if norm_sym and norm_sym not in alphabet:
                errors.append(f"NFA symbol '{norm_sym}' is not in alphabet.")
            if isinstance(dsts, str):
                if ',' in dsts:
                    dst_list = [x.strip() for x in dsts.split(',') if x.strip()]
                else:
                    dst_list = [dsts.strip()] if dsts.strip() else []
            elif isinstance(dsts, list):
                if any(not isinstance(x, str) or not x.strip() for x in dsts):
                    errors.append('NFA transition targets must be strings or arrays of non-empty strings.')
                    continue
                dst_list = [x.strip() for x in dsts]
            else:
                errors.append('NFA transition targets must be strings or arrays of non-empty strings.')
                continue
            for dst in dst_list:
                if dst not in states:
                    errors.append(f"NFA transition target '{dst}' is not in states.")
                transitions.setdefault(src, {}).setdefault(norm_sym, set()).add(dst)
    if errors:
        return None, errors
    return {
        'states': states,
        'alphabet': alphabet,
        'start': start,
        'accepting': accepting,
        'transitions': transitions,
    }, []


def epsilon_closure(nfa, states):
    stack = list(states)
    closure = set(states)
    while stack:
        state = stack.pop()
        for nxt in nfa['transitions'].get(state, {}).get('', set()):
            if nxt not in closure:
                closure.add(nxt)
                stack.append(nxt)
    return closure


def nfa_move(nfa, states, symbol):
    result = set()
    for state in states:
        result.update(nfa['transitions'].get(state, {}).get(symbol, set()))
    return result


# Convert an NFA to a DFA using subset construction.
def nfa_to_dfa(nfa):
    alphabet = list(dict.fromkeys(nfa['alphabet']))
    start_set = frozenset(sorted(epsilon_closure(nfa, {nfa['start']})))
    queue = deque([start_set])
    seen = {start_set: 'q0'}
    transitions = {}
    accepting = []
    while queue:
        subset = queue.popleft()
        name = seen[subset]
        transitions.setdefault(name, {})
        if set(subset) & set(nfa['accepting']) and name not in accepting:
            accepting.append(name)
        for symbol in alphabet:
            moved = nfa_move(nfa, subset, symbol)
            if not moved:
                continue
            target = frozenset(sorted(epsilon_closure(nfa, moved)))
            if target not in seen:
                seen[target] = f"q{len(seen)}"
                queue.append(target)
            transitions[name][symbol] = seen[target]
    states = [name for _, name in sorted(seen.items(), key=lambda item: int(item[1][1:]) if item[1][1:].isdigit() else item[1])]
    return {
        'states': states,
        'alphabet': alphabet,
        'start': 'q0',
        'accepting': accepting,
        'transitions': transitions,
    }




def nfa_transition_count(nfa):
    count = 0
    for mapping in (nfa.get("transitions") or {}).values():
        for targets in mapping.values():
            count += len(targets)
    return count


def dfa_transition_count(dfa):
    return sum(len(mapping) for mapping in (dfa.get("transitions") or {}).values())


# Generate preview data for DFA, regex or NFA source modes.
def source_preview_payload(source_kind, source_payload):
    source_kind = (source_kind or "dfa").strip().lower()
    if source_kind == "regex":
        nfa, errors = regex_to_nfa(source_payload or "")
        if errors:
            return None, errors
        dfa = nfa_to_dfa(nfa)
        return {
            "dfa": dfa,
            "source_kind": "regex",
            "summary": {
                "title": "Regular expression → DFA preview",
                "notes": [
                    "Supported regex operators: |, *, parentheses, and implicit concatenation.",
                    f"Intermediate NFA size: {len(nfa['states'])} states, {nfa_transition_count(nfa)} transition edge(s).",
                    f"Derived DFA size: {len(dfa['states'])} states, {dfa_transition_count(dfa)} transition(s).",
                ],
            },
        }, []
    if source_kind == "nfa":
        nfa, errors = parse_nfa_json(source_payload or "")
        if errors:
            return None, errors
        dfa = nfa_to_dfa(nfa)
        epsilon_edges = sum(len(targets) for mapping in (nfa.get("transitions") or {}).values() for sym, targets in mapping.items() if sym == "")
        return {
            "dfa": dfa,
            "source_kind": "nfa",
            "summary": {
                "title": "NFA JSON → DFA preview",
                "notes": [
                    "Use JSON with states, alphabet, start, accepting, and transitions.",
                    f"Input NFA size: {len(nfa['states'])} states, {nfa_transition_count(nfa)} transition edge(s).",
                    f"Epsilon transition count: {epsilon_edges}.",
                    f"Derived DFA size: {len(dfa['states'])} states, {dfa_transition_count(dfa)} transition(s).",
                ],
            },
        }, []
    return None, ["Source preview only supports regex and NFA source kinds."]


def build_target_dfa_from_form(form):
    source_kind = (form.get('source_kind', 'dfa') or 'dfa').strip().lower()
    source_payload = ''
    if source_kind == 'regex':
        source_payload = form.get('regex_source', '').strip()
        nfa, errors = regex_to_nfa(source_payload)
        if errors:
            return None, errors, source_kind, source_payload
        return nfa_to_dfa(nfa), [], source_kind, source_payload
    if source_kind == 'nfa':
        source_payload = form.get('nfa_source', '').strip()
        nfa, errors = parse_nfa_json(source_payload)
        if errors:
            return None, errors, source_kind, source_payload
        return nfa_to_dfa(nfa), [], source_kind, source_payload
    dfa, errors = build_dfa_from_form(form)
    return dfa, errors, 'dfa', ''


def reachable_states(dfa):
    visited = set()
    q = deque([dfa["start"]])
    while q:
        state = q.popleft()
        if state in visited:
            continue
        visited.add(state)
        for symbol in dfa["alphabet"]:
            nxt = dfa["transitions"].get(state, {}).get(symbol)
            if nxt and nxt not in visited:
                q.append(nxt)
    return visited


# Add a dead state when needed so every transition is defined.
def complete_dfa(dfa, alphabet=None):
    alphabet = sorted(set(alphabet or dfa["alphabet"]))
    states = list(dict.fromkeys(dfa["states"]))
    transitions = {s: dict(dfa["transitions"].get(s, {})) for s in states}
    dead = "__dead__"
    need_dead = False
    while dead in states:
        dead += "_"

    for state in list(states):
        transitions.setdefault(state, {})
        for symbol in alphabet:
            if symbol not in transitions[state]:
                need_dead = True
                transitions[state][symbol] = dead

    if need_dead:
        states.append(dead)
        transitions[dead] = {symbol: dead for symbol in alphabet}

    return {
        "states": states,
        "alphabet": alphabet,
        "start": dfa["start"],
        "accepting": list(dict.fromkeys(dfa["accepting"])),
        "transitions": transitions,
    }


# Remove unreachable states before minimisation checks.
def trim_dfa(dfa):
    reach = reachable_states(dfa)
    states = [s for s in dfa["states"] if s in reach]
    transitions = {s: {a: t for a, t in dfa["transitions"].get(s, {}).items() if t in reach} for s in states}
    accepting = [s for s in dfa["accepting"] if s in reach]
    return {
        "states": states,
        "alphabet": list(dfa["alphabet"]),
        "start": dfa["start"],
        "accepting": accepting,
        "transitions": transitions,
    }


# Find groups of states that behave equivalently.
def equivalent_state_groups(dfa):
    dfa = trim_dfa(complete_dfa(dfa))
    states = set(dfa["states"])
    alphabet = list(dfa["alphabet"])
    accepting = set(dfa["accepting"])
    non_accepting = states - accepting

    P = []
    if accepting:
        P.append(accepting)
    if non_accepting:
        P.append(non_accepting)
    W = P.copy()

    def predecessors(symbol, subset):
        pred = set()
        for s in states:
            if dfa["transitions"][s][symbol] in subset:
                pred.add(s)
        return pred

    while W:
        A = W.pop()
        for c in alphabet:
            X = predecessors(c, A)
            new_P = []
            for Y in P:
                inter = Y & X
                diff = Y - X
                if inter and diff:
                    new_P.extend([inter, diff])
                    if Y in W:
                        W.remove(Y)
                        W.extend([inter, diff])
                    else:
                        W.append(inter if len(inter) <= len(diff) else diff)
                else:
                    new_P.append(Y)
            P = new_P

    groups = [sorted(block) for block in P if block]
    groups.sort(key=lambda b: (dfa["start"] not in b, b[0]))
    return groups




# Keep partition-refinement snapshots for minimality feedback.
def partition_refinement_snapshots(dfa):
    original_states = set(dfa["states"])
    dfa = trim_dfa(complete_dfa(dfa))
    if not dfa.get("states"):
        return []
    alphabet = list(dfa["alphabet"])
    accepting = set(dfa["accepting"])
    states = sorted(dfa["states"])

    partition = []
    acc_block = sorted([state for state in states if state in accepting])
    rej_block = sorted([state for state in states if state not in accepting])
    if acc_block:
        partition.append(acc_block)
    if rej_block:
        partition.append(rej_block)

    snapshots = [{
        "round": 0,
        "title": "Round 0 · Initial partition",
        "reason": "Split accepting and non-accepting states first.",
        "blocks": [list(block) for block in partition],
        "splits": [],
    }]

    while True:
        block_index = {state: index for index, block in enumerate(partition) for state in block}
        next_partition = []
        split_rows = []
        changed = False

        for block in partition:
            groups = {}
            for state in block:
                signature = tuple(block_index[dfa["transitions"][state][symbol]] for symbol in alphabet)
                groups.setdefault(signature, []).append(state)
            refined = [sorted(group) for group in groups.values()]
            refined.sort(key=lambda group: group[0])
            next_partition.extend(refined)
            if len(refined) > 1:
                changed = True
                split_rows.append({
                    "from": sorted(block),
                    "parts": [
                        {
                            "states": group,
                            "signature": ", ".join(
                                f"{symbol}→B{block_index[dfa['transitions'][group[0]][symbol]] + 1}" for symbol in alphabet
                            ),
                        }
                        for group in refined
                    ],
                })

        if not changed:
            break

        partition = next_partition
        snapshots.append({
            "round": len(snapshots),
            "title": f"Round {len(snapshots)} · Refinement",
            "reason": "States stay together only when every symbol leads into the same current block.",
            "blocks": [list(block) for block in partition],
            "splits": split_rows,
        })

    cleaned_snapshots = []
    for snapshot in snapshots:
        blocks = []
        for block in snapshot["blocks"]:
            visible = [state for state in block if state in original_states]
            if visible:
                blocks.append(visible)
        if not blocks:
            continue
        cleaned_split_rows = []
        for split in snapshot["splits"]:
            from_block = [state for state in split["from"] if state in original_states]
            parts = []
            for part in split["parts"]:
                visible_states = [state for state in part["states"] if state in original_states]
                if visible_states:
                    parts.append({"states": visible_states, "signature": part["signature"]})
            if from_block and len(parts) > 1:
                cleaned_split_rows.append({"from": from_block, "parts": parts})
        cleaned_snapshots.append({
            "round": snapshot["round"],
            "title": snapshot["title"],
            "reason": snapshot["reason"],
            "blocks": blocks,
            "splits": cleaned_split_rows,
        })

    return cleaned_snapshots


def partition_feedback_lines(merge_groups, partition_snapshots):
    feedback = []
    if merge_groups:
        feedback.append(
            "Final stable blocks show states that behave the same on every future input: "
            + " | ".join("{" + ", ".join(group) + "}" for group in merge_groups[:4])
        )
    if partition_snapshots and len(partition_snapshots) > 1:
        last = partition_snapshots[-1]
        feedback.append(
            "Partition refinement stabilised at "
            + " | ".join("{" + ", ".join(block) + "}" for block in last["blocks"])
            + "."
        )
    return feedback


# Minimise a DFA using partition refinement.
def hopcroft_minimize(dfa):
    dfa = trim_dfa(complete_dfa(dfa))
    alphabet = list(dfa["alphabet"])
    accepting = set(dfa["accepting"])
    sorted_blocks = equivalent_state_groups(dfa)
    rep_to_name = {}
    new_states = []
    for i, block in enumerate(sorted_blocks):
        name = f"M{i}"
        new_states.append(name)
        for s in block:
            rep_to_name[s] = name

    new_transitions = {}
    for block in sorted_blocks:
        rep = block[0]
        new_name = rep_to_name[rep]
        new_transitions[new_name] = {}
        for c in alphabet:
            new_transitions[new_name][c] = rep_to_name[dfa["transitions"][rep][c]]

    new_accepting = sorted({rep_to_name[s] for s in accepting})
    return {
        "states": new_states,
        "alphabet": alphabet,
        "start": rep_to_name[dfa["start"]],
        "accepting": new_accepting,
        "transitions": new_transitions,
    }


def is_minimal(dfa):
    completed = complete_dfa(dfa)
    original_states = set(dfa.get("states", []))
    reachable = reachable_states(completed) if dfa.get("start") else set()
    has_unreachable_original_states = bool(original_states - reachable)

    cleaned = trim_dfa(completed)
    minimized = hopcroft_minimize(dfa)
    size_is_minimal = len(cleaned["states"]) == len(minimized["states"])
    return (not has_unreachable_original_states) and size_is_minimal, minimized


# Search the product automaton for the shortest distinguishing word.
def shortest_counterexample(dfa1, dfa2):
    alphabet = sorted(set(dfa1["alphabet"]) | set(dfa2["alphabet"]))
    a = complete_dfa(trim_dfa(dfa1), alphabet)
    b = complete_dfa(trim_dfa(dfa2), alphabet)

    start_pair = (a["start"], b["start"])
    q = deque([(start_pair, "")])
    visited = {start_pair}

    while q:
        (s1, s2), word = q.popleft()
        accept1 = s1 in a["accepting"]
        accept2 = s2 in b["accepting"]
        if accept1 != accept2:
            return word or "ε"
        for symbol in alphabet:
            n1 = a["transitions"][s1][symbol]
            n2 = b["transitions"][s2][symbol]
            pair = (n1, n2)
            if pair not in visited:
                visited.add(pair)
                next_word = word + symbol
                q.append((pair, next_word))
    return None


# Record the state path followed by a DFA on one input word.
def trace_word(dfa, word):
    symbols = set() if word == "ε" else set(word)
    alphabet = sorted(set(dfa["alphabet"]) | symbols)
    dfa = complete_dfa(trim_dfa(dfa), alphabet)
    current = dfa["start"]
    steps = [{"state": current, "symbol": "start"}]
    if word == "ε":
        return {
            "steps": steps,
            "accepted": current in dfa["accepting"],
        }
    for symbol in word:
        nxt = dfa["transitions"][current][symbol]
        steps.append({"state": nxt, "symbol": symbol})
        current = nxt
    return {
        "steps": steps,
        "accepted": current in dfa["accepting"],
    }


def missing_transition_pairs(dfa):
    missing = []
    for state in dfa["states"]:
        for symbol in dfa["alphabet"]:
            if symbol not in dfa["transitions"].get(state, {}):
                missing.append((state, symbol))
    return missing


def is_total_deterministic(dfa):
    states = list(dfa.get("states", []))
    alphabet = list(dfa.get("alphabet", []))
    transitions = dfa.get("transitions", {})

    state_set = set(states)
    alphabet_set = set(alphabet)

    if len(state_set) != len(states) or len(alphabet_set) != len(alphabet):
        return False
    if dfa.get("start") not in state_set:
        return False
    if any(state not in state_set for state in dfa.get("accepting", [])):
        return False

    for state in states:
        mapping = transitions.get(state, {})
        if set(mapping.keys()) != alphabet_set:
            return False
        if any(next_state not in state_set for next_state in mapping.values()):
            return False

    return True


# Build student-facing hints from the checking results.
def make_hints(answer_dfa, target_dfa, deterministic_ok, minimal_ok, counterexample, merge_groups=None, partition_feedback=None):
    hints = []
    missing = missing_transition_pairs(answer_dfa)
    unreachable = sorted(set(answer_dfa["states"]) - reachable_states(complete_dfa(answer_dfa))) if answer_dfa.get("start") else []
    if not deterministic_ok:
        hints.append("Check whether each state has exactly one valid transition for every symbol in the alphabet.")
    if missing:
        sample = ", ".join(f"{s} on {a}" for s, a in missing[:4])
        hints.append(f"Some transitions are missing: {sample}{' ...' if len(missing) > 4 else ''}.")
    if counterexample is not None:
        hints.append(f"Try tracing the counterexample: {counterexample}.")
    if unreachable:
        hints.append(f"Unreachable states can often be removed first: {', '.join(unreachable[:4])}{' ...' if len(unreachable) > 4 else ''}.")
    if not minimal_ok:
        hints.append("Your DFA can still be minimized. Look for states that behave the same on every future input.")
        if merge_groups:
            sample_groups = [", ".join(group) for group in merge_groups[:3]]
            hints.append(f"Possible merge group(s): {' | '.join(sample_groups)}.")
        elif unreachable:
            hints.append("No merge group was detected among reachable states. The current non-minimality is likely caused by unreachable states.")
        if partition_feedback:
            hints.extend(partition_feedback[:2])
    if not hints:
        hints.append("Looks good. Compare your state structure with the target DFA if needed.")
    return hints


# Run all configured checks for a submitted DFA.
def evaluate_submission(answer_dfa, target_dfa, require_determinism=True, require_minimality=True):
    deterministic_ok = is_total_deterministic(answer_dfa)
    equivalent_counterexample = shortest_counterexample(answer_dfa, target_dfa)
    equivalent_ok = equivalent_counterexample is None
    minimal_ok, minimized = is_minimal(answer_dfa)
    merge_groups = []
    partition_snapshots = []
    partition_feedback = []
    if not minimal_ok:
        partition_snapshots = partition_refinement_snapshots(answer_dfa)
        if partition_snapshots:
            merge_groups = [group for group in partition_snapshots[-1]["blocks"] if len(group) > 1]
        if not merge_groups:
            original_states = set(answer_dfa["states"])
            visible_groups = [[state for state in group if state in original_states] for group in equivalent_state_groups(answer_dfa)]
            merge_groups = [group for group in visible_groups if len(group) > 1]
        partition_feedback = partition_feedback_lines(merge_groups, partition_snapshots)
    hints = make_hints(answer_dfa, target_dfa, deterministic_ok, minimal_ok, equivalent_counterexample, merge_groups, partition_feedback)
    trace = None
    if equivalent_counterexample is not None:
        trace = {
            "answer": trace_word(answer_dfa, equivalent_counterexample),
            "target": trace_word(target_dfa, equivalent_counterexample),
        }
    result = {
        "equivalent": equivalent_ok,
        "deterministic": deterministic_ok if require_determinism else None,
        "minimal": minimal_ok if require_minimality else None,
        "counterexample": equivalent_counterexample,
        "counterexample_trace": trace,
        "hints": hints,
        "minimized_state_count": len(minimized["states"]),
        "merge_groups": merge_groups,
        "partition_feedback": partition_feedback,
        "partition_snapshots": partition_snapshots,
    }
    result["score"] = score_summary(result, require_determinism, require_minimality)
    return result


def dfa_to_initial_form(dfa):
    return {
        "states": ",".join(dfa.get("states", [])),
        "alphabet": ",".join(dfa.get("alphabet", [])),
        "start": dfa.get("start", ""),
        "accepting": ",".join(dfa.get("accepting", [])),
        "transitions": "\n".join(
            f"{src},{sym}={dst}"
            for src, mapping in dfa.get("transitions", {}).items()
            for sym, dst in mapping.items()
        ),
    }


def blank_initial_from_target(target_dfa):
    return {
        "states": "",
        "alphabet": ",".join(target_dfa.get("alphabet", [])),
        "start": "",
        "accepting": "",
        "transitions": "",
    }


def result_meets_requirements(result, exercise):
    if "current_revision" in exercise.keys() and exercise["exercise_revision"] != exercise["current_revision"]:
        return False
    if not result.get("equivalent", False):
        return False
    if bool(exercise["require_determinism"]) and not result.get("deterministic", False):
        return False
    if bool(exercise["require_minimality"]) and not result.get("minimal", False):
        return False
    return True


# ---------------------------
# User/group helpers
# ---------------------------

# Load the logged-in user from the current session.
def current_user_row():
    if "user_id" not in session:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


def make_unique_username(db, base_username):
    username = base_username
    i = 2
    while db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base_username}{i}"
        i += 1
    return username


def username_from_email(email):
    prefix = email.split("@", 1)[0].strip().lower()
    prefix = re.sub(r"[^a-z0-9._-]", "_", prefix)
    return prefix or "student"


def random_numeric_password(length=8):
    return "".join(secrets.choice("0123456789") for _ in range(length))


def get_managed_exercises(db, teacher_id):
    return db.execute(
        "SELECT e.*, u.username AS creator FROM exercises e JOIN users u ON e.created_by = u.id WHERE e.created_by = ? ORDER BY e.id DESC",
        (teacher_id,),
    ).fetchall()


def get_teacher_groups(db, teacher_id, include_archived=True):
    query = "SELECT * FROM student_groups WHERE teacher_id = ?"
    params = [teacher_id]
    if not include_archived:
        query += " AND is_archived = 0"
    query += " ORDER BY is_archived ASC, year_label ASC, name ASC, id DESC"
    return db.execute(query, params).fetchall()


def get_managed_students(db, teacher_id, include_archived_groups=True):
    groups = get_teacher_groups(db, teacher_id, include_archived=True)
    group_map = {g["id"]: g for g in groups}
    students = db.execute(
        "SELECT * FROM users WHERE role = 'student' AND managed_by = ? ORDER BY username",
        (teacher_id,),
    ).fetchall()
    result = []
    for s in students:
        group = group_map.get(s["group_id"])
        if not include_archived_groups and group is not None and group["is_archived"]:
            continue
        item = dict(s)
        item["group"] = group
        result.append(item)
    return result


def get_latest_submissions_map(db, student_ids=None, exercise_ids=None):
    query = (
        "SELECT s.*, e.require_determinism, e.require_minimality, e.revision AS current_revision "
        "FROM submissions s "
        "JOIN exercises e ON s.exercise_id = e.id "
        "JOIN (SELECT user_id, exercise_id, MAX(id) AS max_id FROM submissions GROUP BY user_id, exercise_id) latest "
        "ON latest.max_id = s.id WHERE 1=1"
    )
    params = []
    if student_ids:
        placeholders = ",".join("?" for _ in student_ids)
        query += f" AND s.user_id IN ({placeholders})"
        params.extend(student_ids)
    if exercise_ids:
        placeholders = ",".join("?" for _ in exercise_ids)
        query += f" AND s.exercise_id IN ({placeholders})"
        params.extend(exercise_ids)
    rows = db.execute(query, params).fetchall()
    latest = {}
    for row in rows:
        result = json.loads(row["result_json"])
        stale = row["exercise_revision"] != row["current_revision"]
        latest[(row["user_id"], row["exercise_id"])] = {
            "row": row,
            "result": result if not stale else {},
            "stale": stale,
            "completed": result_meets_requirements(result, row),
        }
    return latest


# Summarise a student's latest and best attempt for each visible exercise.
def build_student_progress(db, user_id):
    rows = db.execute(
        """
        SELECT s.*, e.require_determinism, e.require_minimality, e.revision AS current_revision
        FROM submissions s
        JOIN exercises e ON s.exercise_id = e.id
        WHERE s.user_id = ?
        ORDER BY s.exercise_id, s.id ASC
        """,
        (user_id,),
    ).fetchall()
    progress = {}
    for row in rows:
        result = json.loads(row["result_json"])
        exercise_id = row["exercise_id"]
        stale = row["exercise_revision"] != row["current_revision"]
        item = progress.setdefault(exercise_id, {
            "latest_result": result,
            "stale": stale,
            "completed": result_meets_requirements(result, row),
            "time": row["created_at"],
            "best_score": None,
            "best_score_earned": -1,
        })
        # Always keep the latest submission in sync.
        item["latest_result"] = result
        item["stale"] = stale
        item["completed"] = result_meets_requirements(result, row)
        item["time"] = row["created_at"]
        current_earned = (result.get("score") or {}).get("earned", 0)
        if not stale and (item.get("best_score") is None or current_earned > item.get("best_score_earned", -1)):
            item["best_score"] = result.get("score")
            item["best_score_earned"] = current_earned
    return progress


# Build dashboard counts and progress rows for a teacher.
def build_teacher_overview(db, teacher_id, exercises=None):
    students = get_managed_students(db, teacher_id, include_archived_groups=False)
    if exercises is None:
        exercises = get_managed_exercises(db, teacher_id)
    exercise_ids = [ex["id"] for ex in exercises]
    student_ids = [s["id"] for s in students]
    latest_map = get_latest_submissions_map(db, student_ids, exercise_ids)

    progress_map = {}
    overview = []
    for stu in students:
        student_progress = {}
        last_activity = None
        visible_exercises = [ex for ex in exercises if exercise_is_visible_to_group(db, ex, stu.get("group_id"))]
        for ex in exercises:
            item = latest_map.get((stu["id"], ex["id"]))
            if item:
                progress_map.setdefault(stu["id"], {})[ex["id"]] = {
                    "completed": item["completed"],
                    "result": item["result"],
                    "stale": item["stale"],
                    "time": item["row"]["created_at"],
                }
                student_progress[ex["id"]] = item
                time = item["row"]["created_at"]
                if last_activity is None or parse_app_datetime(time) > parse_app_datetime(last_activity):
                    last_activity = time
        visible_ids = {ex["id"] for ex in visible_exercises}
        completed_visible = sum(1 for ex_id, item in student_progress.items() if ex_id in visible_ids and item["completed"])
        attempted_visible = sum(1 for ex_id in student_progress if ex_id in visible_ids)
        overview.append({
            "id": stu["id"],
            "username": stu["username"],
            "email": stu.get("email") or "—",
            "group_name": stu["group"]["name"] if stu.get("group") else "—",
            "group_archived": bool(stu["group"]["is_archived"]) if stu.get("group") else False,
            "completed_count": completed_visible,
            "attempted_count": attempted_visible,
            "total_exercises": len(visible_exercises),
            "last_activity": last_activity,
        })

    return students, exercises, overview, progress_map


def filter_teacher_exercises(db, exercises, q='', group_filter='all', window_filter='all', difficulty_filter='all'):
    q = (q or '').strip().lower()
    filtered = []
    for ex in exercises:
        group_names = exercise_group_names(db, ex['id'])
        status = exercise_window_status(ex)
        tags_blob = (ex['tags'] or '') if 'tags' in ex.keys() else ''
        difficulty = (ex['difficulty'] or 'Standard') if 'difficulty' in ex.keys() else 'Standard'
        title_match = not q or q in ex['title'].lower() or q in (ex['description'] or '').lower() or q in tags_blob.lower()
        if not title_match:
            continue
        if difficulty_filter != 'all' and difficulty.lower() != difficulty_filter.lower():
            continue
        if group_filter == 'restricted' and not group_names:
            continue
        if group_filter == 'open_all' and group_names:
            continue
        if window_filter == 'always_open' and (ex['available_from'] or ex['available_until']):
            continue
        if window_filter in {'open', 'not_open', 'closed'} and status != window_filter:
            continue
        filtered.append(ex)
    return filtered


def filter_student_exercises(exercises, progress, q='', status_filter='all', difficulty_filter='all'):
    q = (q or '').strip().lower()
    rows = []
    for ex in exercises:
        item = progress.get(ex['id'])
        if ex['window_status'] == 'not_open':
            status_key = 'locked'
        elif ex['window_status'] == 'closed':
            status_key = 'locked'
        elif item and item['completed']:
            status_key = 'completed'
        elif item:
            status_key = 'progress'
        else:
            status_key = 'not_started'
        title_match = not q or q in ex['title'].lower() or q in (ex.get('description') or '').lower() or q in ', '.join(ex.get('tags_list', [])).lower()
        if not title_match:
            continue
        if difficulty_filter != 'all' and (ex.get('difficulty') or 'Standard').lower() != difficulty_filter.lower():
            continue
        if status_filter != 'all' and status_key != status_filter:
            continue
        rows.append(ex)
    return rows


def build_exercise_student_status(db, teacher_id, exercise_id):
    students = get_managed_students(db, teacher_id)
    student_ids = [s["id"] for s in students]
    latest_map = get_latest_submissions_map(db, student_ids, [exercise_id])
    exercise = db.execute("SELECT * FROM exercises WHERE id = ? AND created_by = ?", (exercise_id, teacher_id)).fetchone()
    rows = []
    for stu in students:
        visible = exercise_is_visible_to_group(db, exercise, stu.get("group_id")) if exercise else True
        item = latest_map.get((stu["id"], exercise_id))
        if item:
            status = "Needs resubmission" if item["stale"] else ("Completed" if item["completed"] else "Attempted")
            equivalent = item["result"].get("equivalent")
            minimal = item["result"].get("minimal")
            time = item["row"]["created_at"]
        else:
            status = "Not assigned" if not visible else "Not started"
            equivalent = None
            minimal = None
            time = None
        rows.append({
            "username": stu["username"],
            "email": stu.get("email") or "—",
            "group_name": stu["group"]["name"] if stu.get("group") else "—",
            "status": status,
            "equivalent": equivalent,
            "minimal": minimal,
            "time": time,
        })
    return rows


# Return only the exercises visible to the student through their groups.
def student_visible_exercises(db, user_id):
    student = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if student and student["managed_by"]:
        raw = db.execute(
            "SELECT * FROM exercises WHERE created_by = ? ORDER BY id DESC",
            (student["managed_by"],),
        ).fetchall()
    else:
        raw = db.execute("SELECT * FROM exercises ORDER BY id DESC").fetchall()
    rows = []
    group_id = student["group_id"] if student else None
    for ex in raw:
        if not exercise_is_visible_to_group(db, ex, group_id):
            continue
        rows.append(exercise_summary_row(db, ex))
    return rows


def student_can_access_exercise(db, user_id, exercise):
    student = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if student and student["managed_by"] and exercise["created_by"] != student["managed_by"]:
        return False
    if student and not exercise_is_visible_to_group(db, exercise, student["group_id"]):
        return False
    return exercise_window_status(exercise) == "open"


def get_latest_submission_for_student(db, exercise_id, user_id):
    row = db.execute(
        "SELECT * FROM submissions WHERE exercise_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
        (exercise_id, user_id),
    ).fetchone()
    return row




def get_exercise_group_ids(db, exercise_id):
    rows = db.execute("SELECT group_id FROM exercise_groups WHERE exercise_id = ?", (exercise_id,)).fetchall()
    return [r["group_id"] for r in rows]


def set_exercise_group_ids(db, exercise_id, group_ids, preserve_archived=False):
    owner = db.execute("SELECT created_by FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    if owner is None:
        raise ValueError("Exercise not found.")
    group_ids = parse_group_ids(db, owner["created_by"], group_ids)
    if preserve_archived:
        archived = db.execute(
            "SELECT eg.group_id FROM exercise_groups eg JOIN student_groups g ON g.id = eg.group_id "
            "WHERE eg.exercise_id = ? AND g.teacher_id = ? AND g.is_archived = 1",
            (exercise_id, owner["created_by"]),
        ).fetchall()
        group_ids = sorted(set(group_ids) | {row["group_id"] for row in archived})
    db.execute("DELETE FROM exercise_groups WHERE exercise_id = ?", (exercise_id,))
    for gid in group_ids:
        db.execute("INSERT OR IGNORE INTO exercise_groups (exercise_id, group_id) VALUES (?, ?)", (exercise_id, gid))


def parse_group_ids(db, teacher_id, values):
    try:
        ids = sorted({int(x) for x in values if str(x).strip()})
    except (ValueError, TypeError):
        raise ValueError("Group IDs must be integers.") from None
    allowed = {group["id"] for group in get_teacher_groups(db, teacher_id, include_archived=False)}
    if not set(ids).issubset(allowed):
        raise ValueError("Choose an active group belonging to your account.")
    return ids


def parse_exercise_access_form(db, teacher_id, form, errors):
    dates = []
    for field, label in (("available_from", "Start"), ("available_until", "End")):
        try:
            dates.append(parse_form_datetime(form.get(field, "").strip()))
        except (ValueError, OverflowError):
            errors.append(f"{label} time must be a valid date and time.")
            dates.append(None)
    if all(dates) and parse_app_datetime(dates[0]) > parse_app_datetime(dates[1]):
        errors.append("End time must be after start time.")
    try:
        groups = parse_group_ids(db, teacher_id, form.getlist("group_ids"))
    except ValueError as exc:
        errors.append(str(exc))
        groups = []
    return *dates, groups


# Classify exercise availability using its optional time window.
def exercise_window_status(exercise):
    now = now_local()
    start = parse_app_datetime(exercise["available_from"]) if "available_from" in exercise.keys() else None
    end = parse_app_datetime(exercise["available_until"]) if "available_until" in exercise.keys() else None
    if start and now < start:
        return "not_open"
    if end and now > end:
        return "closed"
    return "open"


def exercise_is_visible_to_group(db, exercise, group_id):
    group_ids = get_exercise_group_ids(db, exercise["id"])
    if not group_ids:
        return True
    return group_id in group_ids


def exercise_group_names(db, exercise_id):
    rows = db.execute(
        "SELECT g.name FROM student_groups g JOIN exercise_groups eg ON eg.group_id = g.id WHERE eg.exercise_id = ? ORDER BY g.name",
        (exercise_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def exercise_summary_row(db, ex):
    d = dict(ex)
    d["group_names"] = exercise_group_names(db, ex["id"])
    d["window_status"] = exercise_window_status(ex)
    d["tags_list"] = normalize_tags(ex["tags"] if "tags" in ex.keys() else "")
    d["difficulty"] = (ex["difficulty"] if "difficulty" in ex.keys() and ex["difficulty"] else "Standard")
    return d


# ---------------------------
# Routes
# ---------------------------
# Routes are grouped by public access, teacher workflow and student workflow.
@app.route("/")
def index():
    user = current_user_row()
    if user is None:
        session.clear()
        return redirect(url_for("login"))
    session["role"] = user["role"]
    session["username"] = user["username"]
    if user["role"] == "teacher":
        return redirect(url_for("teacher_dashboard"))
    return redirect(url_for("student_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash(f"Welcome, {user['username']}.", "success")
            if user["must_change_password"]:
                flash("Please change your password first.", "error")
                return redirect(url_for("change_password"))
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        old_password = request.form.get("old_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        user = current_user_row()
        if not check_password_hash(user["password_hash"], old_password):
            flash("Old password is incorrect.", "error")
        elif len(new_password) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new_password != confirm_password:
            flash("The new passwords do not match.", "error")
        else:
            db = get_db()
            db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, initial_password_plain = NULL, password_changed_at = ? WHERE id = ?",
                (generate_password_hash(new_password), now_iso(), session["user_id"]),
            )
            db.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("index"))
    return render_template("change_password.html")


@app.route("/api/source-preview", methods=["POST"])
@login_required(role="teacher")
def api_source_preview():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("source_kind", "dfa"), str) or not isinstance(payload.get("source_payload", ""), str):
        return jsonify({"ok": False, "errors": ["Source type and source payload must be strings in a JSON object."]}), 400
    source_kind = (payload.get("source_kind") or "dfa").strip().lower()
    source_payload = payload.get("source_payload") or ""
    preview, errors = source_preview_payload(source_kind, source_payload)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    return jsonify({"ok": True, **preview})


@app.route("/teacher")
@login_required(role="teacher")
def teacher_dashboard():
    # Show exercise filters, summary cards and student progress.
    db = get_db()
    teacher_id = session["user_id"]
    q = request.args.get("q", "").strip()
    group_filter = request.args.get("group_filter", "all").strip() or "all"
    window_filter = request.args.get("window_filter", "all").strip() or "all"
    difficulty_filter = request.args.get("difficulty", "all").strip() or "all"
    raw_exercises = get_managed_exercises(db, teacher_id)
    filtered_raw = filter_teacher_exercises(db, raw_exercises, q=q, group_filter=group_filter, window_filter=window_filter, difficulty_filter=difficulty_filter)
    exercises = [exercise_summary_row(db, ex) for ex in filtered_raw]
    students, exercise_rows, overview, progress_map = build_teacher_overview(db, teacher_id, exercises=filtered_raw)
    active_groups = get_teacher_groups(db, teacher_id, include_archived=False)
    latest_submissions_count = db.execute(
        "SELECT COUNT(*) AS c FROM submissions s JOIN exercises e ON e.id = s.exercise_id WHERE e.created_by = ?",
        (teacher_id,),
    ).fetchone()["c"]
    summary = {
        "exercise_count": len(exercises),
        "open_now": sum(1 for ex in exercises if ex["window_status"] == "open"),
        "student_count": len(students),
        "group_count": len(active_groups),
        "submission_count": latest_submissions_count,
    }
    return render_template(
        "teacher_dashboard.html",
        exercises=exercises,
        students=students,
        exercise_rows=exercise_rows,
        overview=overview,
        progress_map=progress_map,
        q=q,
        group_filter=group_filter,
        window_filter=window_filter,
        difficulty_filter=difficulty_filter,
        active_groups=active_groups,
        summary=summary,
        canonical_presets=canonical_exercise_presets(),
    )




@app.route("/teacher/exercises/preset/<slug>", methods=["POST"])
@login_required(role="teacher")
def teacher_create_preset_exercise(slug):
    db = get_db()
    preset = canonical_preset_by_slug(slug)
    if not preset:
        flash("Preset not found.", "error")
        return redirect(url_for("teacher_dashboard"))
    try:
        selected_groups = parse_group_ids(db, session["user_id"], request.form.getlist("group_ids"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("teacher_dashboard"))
    cur = db.execute(
        """
        INSERT INTO exercises
        (title, description, alphabet, target_dfa_json, require_determinism, require_minimality, created_by, created_at, available_from, available_until, difficulty, tags)
        VALUES (?, ?, ?, ?, 1, 1, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            preset["title"],
            preset["description"],
            ",".join(preset["dfa"]["alphabet"]),
            json.dumps(preset["dfa"]),
            session["user_id"],
            now_iso(),
            preset.get("difficulty", "Standard"),
            tags_text(preset.get("tags", "")),
        ),
    )
    exercise_id = cur.lastrowid
    if selected_groups:
        set_exercise_group_ids(db, exercise_id, selected_groups)
    db.commit()
    flash(f"Preset exercise created: {preset['title']}", "success")
    return redirect(url_for("teacher_dashboard"))


@app.route("/teacher/export/exercises.csv")
@login_required(role="teacher")
def teacher_export_exercises_csv():
    db = get_db()
    teacher_id = session["user_id"]
    q = request.args.get("q", "").strip()
    group_filter = request.args.get("group_filter", "all").strip() or "all"
    window_filter = request.args.get("window_filter", "all").strip() or "all"
    difficulty_filter = request.args.get("difficulty", "all").strip() or "all"
    raw_exercises = get_managed_exercises(db, teacher_id)
    rows = []
    for ex in filter_teacher_exercises(db, raw_exercises, q=q, group_filter=group_filter, window_filter=window_filter, difficulty_filter=difficulty_filter):
        rows.append({
            "id": ex["id"],
            "title": ex["title"],
            "alphabet": ex["alphabet"],
            "difficulty": ex["difficulty"] or "Standard",
            "tags": tags_text(ex["tags"] or ""),
            "groups": ", ".join(exercise_group_names(db, ex["id"])) or "All active groups / no restriction",
            "available_from": display_dt(ex["available_from"]),
            "available_until": display_dt(ex["available_until"]),
            "window_status": exercise_window_status(ex),
            "created_at": display_dt(ex["created_at"]),
        })
    return csv_response(
        "teacher_exercises.csv",
        rows,
        ["id", "title", "alphabet", "difficulty", "tags", "groups", "available_from", "available_until", "window_status", "created_at"],
    )


@app.route("/teacher/export/progress.csv")
@login_required(role="teacher")
def teacher_export_progress_csv():
    db = get_db()
    teacher_id = session["user_id"]
    q = request.args.get("q", "").strip()
    group_filter = request.args.get("group_filter", "all").strip() or "all"
    window_filter = request.args.get("window_filter", "all").strip() or "all"
    difficulty_filter = request.args.get("difficulty", "all").strip() or "all"
    raw_exercises = get_managed_exercises(db, teacher_id)
    filtered_raw = filter_teacher_exercises(db, raw_exercises, q=q, group_filter=group_filter, window_filter=window_filter, difficulty_filter=difficulty_filter)
    _students, exercise_rows, overview, progress_map = build_teacher_overview(db, teacher_id, exercises=filtered_raw)
    rows = []
    for stu in overview:
        for ex in exercise_rows:
            item = progress_map.get(stu["id"], {}).get(ex["id"])
            status = "Not started"
            equivalent = ""
            minimal = ""
            counterexample = ""
            time = ""
            if item:
                status = "Needs resubmission" if item["stale"] else ("Completed" if item["completed"] else "Attempted")
                if not item["stale"]:
                    equivalent = "Yes" if item["result"].get("equivalent") else "No"
                    minimal = "Yes" if item["result"].get("minimal") else "No"
                counterexample = item["result"].get("counterexample") or ""
                time = display_dt(item["time"])
            rows.append({
                "student": stu["username"],
                "email": stu["email"],
                "group": stu["group_name"],
                "exercise_id": ex["id"],
                "exercise_title": ex["title"],
                "status": status,
                "equivalent": equivalent,
                "minimal": minimal,
                "counterexample": counterexample,
                "last_update": time,
            })
    return csv_response(
        "teacher_progress.csv",
        rows,
        ["student", "email", "group", "exercise_id", "exercise_title", "status", "equivalent", "minimal", "counterexample", "last_update"],
    )


@app.route("/teacher/students", methods=["GET", "POST"])
@login_required(role="teacher")
def teacher_students():
    # Create and manage the students owned by the teacher.
    db = get_db()
    teacher_id = session["user_id"]
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        group_id = request.form.get("group_id") or None
        try:
            group_ids = parse_group_ids(db, teacher_id, [group_id] if group_id else [])
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("teacher_students"))
        group_id = group_ids[0] if group_ids else None
        if not email or "@" not in email:
            flash("Please enter a valid student email.", "error")
        elif db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            flash("That email is already in use.", "error")
        else:
            base_username = username_from_email(email)
            username = make_unique_username(db, base_username)
            password_plain = random_numeric_password(8)
            db.execute(
                """
                INSERT INTO users (username, password_hash, role, email, managed_by, group_id, must_change_password, created_at, initial_password_plain)
                VALUES (?, ?, 'student', ?, ?, ?, 1, ?, ?)
                """,
                (
                    username,
                    generate_password_hash(password_plain),
                    email,
                    teacher_id,
                    group_id,
                    now_iso(),
                    password_plain,
                ),
            )
            db.commit()
            flash(f"Student created. Username: {username} | Temporary password: {password_plain}", "success")
            return redirect(url_for("teacher_students"))

    q = request.args.get("q", "").strip()
    group_filter = request.args.get("group_filter", "ungrouped").strip() or "ungrouped"

    groups = get_teacher_groups(db, teacher_id)
    active_groups = get_teacher_groups(db, teacher_id, include_archived=False)
    students = get_managed_students(db, teacher_id, include_archived_groups=True)

    student_ids = [s["id"] for s in students]
    exercise_ids = [e["id"] for e in get_managed_exercises(db, teacher_id)]
    latest_map = get_latest_submissions_map(db, student_ids, exercise_ids)

    rows = []
    for stu in students:
        items = [v for (uid, _), v in latest_map.items() if uid == stu["id"]]
        if stu.get("initial_password_plain"):
            password_status = stu.get("initial_password_plain")
        elif stu.get("password_changed_at"):
            password_status = "Password changed"
        else:
            password_status = "Not available"
        group_obj = stu.get("group")
        rows.append({
            "id": stu["id"],
            "username": stu["username"],
            "email": stu.get("email") or "—",
            "group_id": stu.get("group_id"),
            "group_name": group_obj["name"] if group_obj else "No group",
            "group_archived": bool(group_obj["is_archived"]) if group_obj else False,
            "attempted_count": len(items),
            "completed_count": sum(1 for x in items if x["completed"]),
            "password_status": password_status,
        })

    def row_matches_query(row):
        if not q:
            return True
        needle = q.lower()
        return needle in row["username"].lower() or needle in row["email"].lower()

    filtered_rows = [r for r in rows if row_matches_query(r)]

    if group_filter == "ungrouped":
        current_section_rows = [r for r in filtered_rows if not r["group_id"]]
        current_section_title = "Ungrouped students"
        current_anchor = "group-ungrouped"
    else:
        selected_group = next((g for g in active_groups if str(g["id"]) == group_filter), None)
        if not selected_group:
            group_filter = "ungrouped"
            current_section_rows = [r for r in filtered_rows if not r["group_id"]]
            current_section_title = "Ungrouped students"
            current_anchor = "group-ungrouped"
        else:
            current_section_rows = [r for r in filtered_rows if str(r["group_id"]) == group_filter]
            current_section_title = selected_group["name"]
            current_anchor = f"group-{selected_group['id']}"

    search_results = []
    if q:
        for r in rows:
            if row_matches_query(r):
                jump_group = "ungrouped" if not r["group_id"] else str(r["group_id"])
                jump_anchor = "group-ungrouped" if not r["group_id"] else f"group-{r['group_id']}"
                search_results.append({
                    **r,
                    "jump_url": url_for("teacher_students", group_filter=jump_group, q=q) + f"#{jump_anchor}",
                })

    return render_template(
        "teacher_students.html",
        groups=groups,
        group_options=active_groups,
        q=q,
        group_filter=group_filter,
        current_section_rows=current_section_rows,
        current_section_title=current_section_title,
        current_anchor=current_anchor,
        search_results=search_results,
    )


@app.route("/teacher/students/<int:student_id>/group", methods=["POST"])
@login_required(role="teacher")
def teacher_update_student_group(student_id):
    db = get_db()
    teacher_id = session["user_id"]
    student = db.execute(
        "SELECT * FROM users WHERE id = ? AND role = 'student' AND managed_by = ?",
        (student_id, teacher_id),
    ).fetchone()
    if not student:
        flash("Student not found.", "error")
        return redirect(url_for("teacher_students"))

    group_id = request.form.get("group_id") or None
    try:
        group_ids = parse_group_ids(db, teacher_id, [group_id] if group_id else [])
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("teacher_students"))
    group_id = group_ids[0] if group_ids else None
    db.execute("UPDATE users SET group_id = ? WHERE id = ?", (group_id, student_id))
    db.commit()
    flash("Student group updated.", "success")
    return redirect(url_for("teacher_students"))


@app.route("/teacher/groups", methods=["GET", "POST"])
@login_required(role="teacher")
def teacher_groups():
    db = get_db()
    teacher_id = session["user_id"]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        year_label = request.form.get("year_label", "").strip()
        if not name:
            flash("Group name cannot be empty.", "error")
        else:
            db.execute(
                "INSERT INTO student_groups (teacher_id, name, year_label, is_archived, created_at) VALUES (?, ?, ?, 0, ?)",
                (teacher_id, name, year_label, now_iso()),
            )
            db.commit()
            flash("Student group created.", "success")
            return redirect(url_for("teacher_groups"))

    q = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "all").strip() or "all"
    groups = get_teacher_groups(db, teacher_id)
    rows = []
    for grp in groups:
        member_count = db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = 'student' AND managed_by = ? AND group_id = ?",
            (teacher_id, grp["id"]),
        ).fetchone()["c"]
        assigned_exercise_count = db.execute(
            "SELECT COUNT(*) AS c FROM exercise_groups eg JOIN exercises e ON e.id = eg.exercise_id WHERE eg.group_id = ? AND e.created_by = ?",
            (grp["id"], teacher_id),
        ).fetchone()["c"]
        row = {**dict(grp), "member_count": member_count, "assigned_exercise_count": assigned_exercise_count}
        name_match = not q or q in (grp['name'] or '').lower() or q in (grp['year_label'] or '').lower()
        status_match = status_filter == 'all' or (status_filter == 'active' and not grp['is_archived']) or (status_filter == 'archived' and grp['is_archived'])
        if name_match and status_match:
            rows.append(row)
    return render_template("teacher_groups.html", groups=rows, q=q, status_filter=status_filter)


@app.route("/teacher/groups/<int:group_id>/edit", methods=["GET", "POST"])
@login_required(role="teacher")
def teacher_edit_group(group_id):
    db = get_db()
    teacher_id = session["user_id"]
    group = db.execute(
        "SELECT * FROM student_groups WHERE id = ? AND teacher_id = ?",
        (group_id, teacher_id),
    ).fetchone()
    if not group:
        flash("Group not found.", "error")
        return redirect(url_for("teacher_groups"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        year_label = request.form.get("year_label", "").strip()
        if not name:
            flash("Group name cannot be empty.", "error")
        else:
            db.execute(
                "UPDATE student_groups SET name = ?, year_label = ? WHERE id = ? AND teacher_id = ?",
                (name, year_label, group_id, teacher_id),
            )
            db.commit()
            flash("Group details updated.", "success")
            return redirect(url_for("teacher_groups"))

    initial = {"name": group["name"], "year_label": group["year_label"] or ""}
    return render_template("teacher_group_form.html", mode="edit", group=group, initial=initial)


@app.route("/teacher/groups/<int:group_id>/archive", methods=["POST"])
@login_required(role="teacher")
def teacher_archive_group(group_id):
    db = get_db()
    teacher_id = session["user_id"]
    group = db.execute(
        "SELECT * FROM student_groups WHERE id = ? AND teacher_id = ?",
        (group_id, teacher_id),
    ).fetchone()
    if not group:
        flash("Group not found.", "error")
        return redirect(url_for("teacher_groups"))
    new_value = 0 if group["is_archived"] else 1
    db.execute("UPDATE student_groups SET is_archived = ? WHERE id = ?", (new_value, group_id))
    db.commit()
    flash("Group updated.", "success")
    return redirect(url_for("teacher_groups"))


@app.route("/teacher/exercises/new", methods=["GET", "POST"])
@login_required(role="teacher")
def teacher_new_exercise():
    # Save a new exercise from the selected source mode.
    db = get_db()
    active_groups = get_teacher_groups(db, session["user_id"], include_archived=False)
    initial = {
        "title": "",
        "description": "",
        "source_kind": "dfa",
        "regex_source": "",
        "nfa_source": '{\n  "states": ["q0", "q1"],\n  "alphabet": ["a", "b"],\n  "start": "q0",\n  "accepting": ["q1"],\n  "transitions": {\n    "q0": {"a": ["q0", "q1"], "b": ["q0"]},\n    "q1": {"b": ["q1"], "ε": ["q0"]}\n  }\n}',
        "states": "q0,q1",
        "alphabet": "a,b",
        "start": "q0",
        "accepting": "q0",
        "transitions": "q0,a=q1\nq0,b=q0\nq1,a=q0\nq1,b=q1",
        "require_determinism": True,
        "require_minimality": True,
        "available_from": "",
        "available_until": "",
        "group_ids": [],
        "difficulty": "Standard",
        "tags": "",
    }
    if request.method == "POST":
        dfa, errors, source_kind, source_payload = build_target_dfa_from_form(request.form)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        available_from, available_until, selected_groups = parse_exercise_access_form(db, session["user_id"], request.form, errors)
        difficulty = request.form.get("difficulty", "Standard").strip() or "Standard"
        tags_raw = request.form.get("tags", "").strip()
        if not title:
            errors.append("Title cannot be empty.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("exercise_form.html", mode="new", initial=request.form, group_options=active_groups)

        cur = db.execute(
            """
            INSERT INTO exercises
            (title, description, alphabet, target_dfa_json, require_determinism, require_minimality, created_by, created_at, available_from, available_until, difficulty, tags, source_kind, source_payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                description,
                ",".join(dfa["alphabet"]),
                json.dumps(dfa),
                1 if request.form.get("require_determinism") else 0,
                1 if request.form.get("require_minimality") else 0,
                session["user_id"],
                now_iso(),
                available_from,
                available_until,
                difficulty,
                tags_text(tags_raw),
                source_kind,
                source_payload,
            ),
        )
        exercise_id = cur.lastrowid
        set_exercise_group_ids(db, exercise_id, selected_groups)
        db.commit()
        flash("Exercise created.", "success")
        return redirect(url_for("teacher_dashboard"))

    return render_template("exercise_form.html", mode="new", initial=initial, group_options=active_groups)


@app.route("/teacher/exercises/<int:exercise_id>/edit", methods=["GET", "POST"])
@login_required(role="teacher")
def teacher_edit_exercise(exercise_id):
    db = get_db()
    teacher_id = session["user_id"]
    exercise = db.execute(
        "SELECT * FROM exercises WHERE id = ? AND created_by = ?",
        (exercise_id, teacher_id),
    ).fetchone()
    if not exercise:
        flash("Exercise not found.", "error")
        return redirect(url_for("teacher_dashboard"))

    active_groups = get_teacher_groups(db, teacher_id, include_archived=False)
    if request.method == "POST":
        dfa, errors, source_kind, source_payload = build_target_dfa_from_form(request.form)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        available_from, available_until, selected_groups = parse_exercise_access_form(db, teacher_id, request.form, errors)
        difficulty = request.form.get("difficulty", "Standard").strip() or "Standard"
        tags_raw = request.form.get("tags", "").strip()
        if not title:
            errors.append("Title cannot be empty.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("exercise_form.html", mode="edit", exercise_id=exercise_id, initial=request.form, group_options=active_groups)

        grading_changed = (
            dfa != json.loads(exercise["target_dfa_json"])
            or bool(request.form.get("require_determinism")) != bool(exercise["require_determinism"])
            or bool(request.form.get("require_minimality")) != bool(exercise["require_minimality"])
        )
        revision = exercise["revision"] + int(grading_changed)
        db.execute(
            """
            UPDATE exercises
            SET title = ?, description = ?, alphabet = ?, target_dfa_json = ?,
                require_determinism = ?, require_minimality = ?, available_from = ?, available_until = ?, difficulty = ?, tags = ?, source_kind = ?, source_payload = ?, revision = ?
            WHERE id = ? AND created_by = ?
            """,
            (
                title,
                description,
                ",".join(dfa["alphabet"]),
                json.dumps(dfa),
                1 if request.form.get("require_determinism") else 0,
                1 if request.form.get("require_minimality") else 0,
                available_from,
                available_until,
                difficulty,
                tags_text(tags_raw),
                source_kind,
                source_payload,
                revision,
                exercise_id,
                teacher_id,
            ),
        )
        set_exercise_group_ids(db, exercise_id, selected_groups, preserve_archived=True)
        db.commit()
        flash("Exercise updated.", "success")
        return redirect(url_for("teacher_view_exercise", exercise_id=exercise_id))

    target = json.loads(exercise["target_dfa_json"])
    initial = dfa_to_initial_form(target)
    initial.update({
        "title": exercise["title"],
        "description": exercise["description"] or "",
        "require_determinism": bool(exercise["require_determinism"]),
        "require_minimality": bool(exercise["require_minimality"]),
        "available_from": html_dt_value(exercise["available_from"]),
        "available_until": html_dt_value(exercise["available_until"]),
        "group_ids": [str(x) for x in get_exercise_group_ids(db, exercise_id)],
        "difficulty": exercise["difficulty"] or "Standard",
        "tags": exercise["tags"] or "",
        "source_kind": exercise["source_kind"] or "dfa",
        "regex_source": exercise["source_payload"] if (exercise["source_kind"] or "dfa") == "regex" else "",
        "nfa_source": exercise["source_payload"] if (exercise["source_kind"] or "dfa") == "nfa" else initial.get("nfa_source", ""),
    })
    return render_template("exercise_form.html", mode="edit", exercise_id=exercise_id, initial=initial, group_options=active_groups)


@app.route("/teacher/exercises/<int:exercise_id>/duplicate", methods=["POST"])
@login_required(role="teacher")
def teacher_duplicate_exercise(exercise_id):
    db = get_db()
    teacher_id = session["user_id"]
    exercise = db.execute(
        "SELECT * FROM exercises WHERE id = ? AND created_by = ?",
        (exercise_id, teacher_id),
    ).fetchone()
    if not exercise:
        flash("Exercise not found.", "error")
        return redirect(url_for("teacher_dashboard"))
    cur = db.execute(
        """
        INSERT INTO exercises
        (title, description, alphabet, target_dfa_json, require_determinism, require_minimality, created_by, created_at, available_from, available_until, difficulty, tags, source_kind, source_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{exercise['title']} (copy)",
            exercise["description"],
            exercise["alphabet"],
            exercise["target_dfa_json"],
            exercise["require_determinism"],
            exercise["require_minimality"],
            teacher_id,
            now_iso(),
            exercise["available_from"],
            exercise["available_until"],
            exercise["difficulty"],
            exercise["tags"],
            exercise["source_kind"] or "dfa",
            exercise["source_payload"],
        ),
    )
    new_id = cur.lastrowid
    active_ids = {group["id"] for group in get_teacher_groups(db, teacher_id, include_archived=False)}
    set_exercise_group_ids(db, new_id, [gid for gid in get_exercise_group_ids(db, exercise_id) if gid in active_ids])
    db.commit()
    flash("Exercise duplicated.", "success")
    return redirect(url_for("teacher_edit_exercise", exercise_id=new_id))


@app.route("/teacher/exercises/access", methods=["GET", "POST"])
@login_required(role="teacher")
def teacher_exercise_access():
    # Update the matrix that maps groups to exercises.
    db = get_db()
    teacher_id = session["user_id"]
    groups = get_teacher_groups(db, teacher_id, include_archived=False)
    all_exercises = get_managed_exercises(db, teacher_id)
    q = request.args.get("q", "").strip().lower()
    exercises = [ex for ex in all_exercises if not q or q in ex['title'].lower() or q in (ex['description'] or '').lower()]
    if request.method == "POST":
        try:
            assignments = [(ex["id"], parse_group_ids(db, teacher_id, request.form.getlist(f"groups_{ex['id']}"))) for ex in exercises]
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("teacher_exercise_access", q=q))
        for exercise_id, selected in assignments:
            set_exercise_group_ids(db, exercise_id, selected, preserve_archived=True)
        db.commit()
        flash("Exercise group access updated.", "success")
        return redirect(url_for("teacher_exercise_access", q=q))
    assignment_map = {ex["id"]: get_exercise_group_ids(db, ex["id"]) for ex in all_exercises}
    return render_template("teacher_exercise_access.html", exercises=exercises, groups=groups, assignment_map=assignment_map, q=q)


@app.route("/teacher/exercises/<int:exercise_id>")
@login_required(role="teacher")
def teacher_view_exercise(exercise_id):
    db = get_db()
    exercise = db.execute(
        "SELECT * FROM exercises WHERE id = ? AND created_by = ?",
        (exercise_id, session["user_id"]),
    ).fetchone()
    if not exercise:
        flash("Exercise not found.", "error")
        return redirect(url_for("teacher_dashboard"))
    managed_students = get_managed_students(db, session["user_id"])
    managed_ids = [s["id"] for s in managed_students]
    submissions = []
    if managed_ids:
        placeholders = ",".join("?" for _ in managed_ids)
        submissions = db.execute(
            f"""
            SELECT s.*, u.username
            FROM submissions s JOIN users u ON s.user_id = u.id
            WHERE s.exercise_id = ? AND s.user_id IN ({placeholders})
            ORDER BY s.id DESC
            """,
            [exercise_id, *managed_ids],
        ).fetchall()
    target_dfa = json.loads(exercise["target_dfa_json"])
    student_status_rows = build_exercise_student_status(db, session["user_id"], exercise_id)
    detail_summary = {
        "students_total": len(student_status_rows),
        "assigned": sum(1 for row in student_status_rows if row["status"] != "Not assigned"),
        "completed": sum(1 for row in student_status_rows if row["status"] == "Completed"),
        "attempted": sum(1 for row in student_status_rows if row["status"] in {"Attempted", "Needs resubmission"}),
        "not_started": sum(1 for row in student_status_rows if row["status"] == "Not started"),
        "not_assigned": sum(1 for row in student_status_rows if row["status"] == "Not assigned"),
    }

    return render_template(
        "teacher_exercise_detail.html",
        exercise=exercise,
        submissions=submissions,
        target_dfa=target_dfa,
        decode_json=json.loads,
        student_status_rows=student_status_rows,
        assigned_group_names=exercise_group_names(db, exercise_id),
        detail_summary=detail_summary,
    )


@app.route("/teacher/exercises/<int:exercise_id>/submissions.csv")
@login_required(role="teacher")
def teacher_export_exercise_submissions_csv(exercise_id):
    db = get_db()
    teacher_id = session["user_id"]
    exercise = db.execute(
        "SELECT * FROM exercises WHERE id = ? AND created_by = ?",
        (exercise_id, teacher_id),
    ).fetchone()
    if not exercise:
        flash("Exercise not found.", "error")
        return redirect(url_for("teacher_dashboard"))
    managed_students = get_managed_students(db, teacher_id)
    managed_ids = [s["id"] for s in managed_students]
    rows = []
    if managed_ids:
        placeholders = ",".join("?" for _ in managed_ids)
        submissions = db.execute(
            f"""
            SELECT s.*, u.username, u.email
            FROM submissions s JOIN users u ON s.user_id = u.id
            WHERE s.exercise_id = ? AND s.user_id IN ({placeholders})
            ORDER BY s.id DESC
            """,
            [exercise_id, *managed_ids],
        ).fetchall()
        for srow in submissions:
            result = json.loads(srow["result_json"])
            rows.append({
                "exercise_id": exercise_id,
                "exercise_title": exercise["title"],
                "exercise_revision": srow["exercise_revision"],
                "current_revision": exercise["revision"],
                "version_status": "Current" if srow["exercise_revision"] == exercise["revision"] else "Previous version",
                "student": srow["username"],
                "email": srow["email"] or "",
                "equivalent": "Yes" if result.get("equivalent") else "No",
                "deterministic": "Yes" if result.get("deterministic") else "No",
                "minimal": "Yes" if result.get("minimal") else "No",
                "counterexample": result.get("counterexample") or "",
                "score": (result.get("score") or {}).get("label", ""),
                "created_at": display_dt(srow["created_at"]),
            })
    return csv_response(
        f"exercise_{exercise_id}_submissions.csv",
        rows,
        ["exercise_id", "exercise_title", "exercise_revision", "current_revision", "version_status", "student", "email", "equivalent", "deterministic", "minimal", "counterexample", "score", "created_at"],
    )


@app.route("/student")
@login_required(role="student")
def student_dashboard():
    # Show only exercises assigned and available to the student.
    db = get_db()
    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "all").strip() or "all"
    difficulty_filter = request.args.get("difficulty", "all").strip() or "all"
    exercises_all = student_visible_exercises(db, session["user_id"])
    progress = build_student_progress(db, session["user_id"])
    exercises = filter_student_exercises(exercises_all, progress, q=q, status_filter=status_filter, difficulty_filter=difficulty_filter)
    summary = {
        "available": sum(1 for ex in exercises_all if ex["window_status"] == "open"),
        "completed": sum(1 for ex in exercises_all if progress.get(ex["id"]) and progress[ex["id"]]["completed"]),
        "in_progress": sum(1 for ex in exercises_all if ex["window_status"] == "open" and progress.get(ex["id"]) and not progress[ex["id"]]["completed"]),
        "locked": sum(1 for ex in exercises_all if ex["window_status"] != "open"),
        "total": len(exercises_all),
    }
    return render_template("student_dashboard.html", exercises=exercises, progress=progress, q=q, status_filter=status_filter, difficulty_filter=difficulty_filter, summary=summary)


@app.route("/student/exercises/<int:exercise_id>", methods=["GET", "POST"])
@login_required(role="student")
def student_attempt_exercise(exercise_id):
    # Accept a DFA submission and store the checking result.
    db = get_db()
    exercise = db.execute("SELECT * FROM exercises WHERE id = ?", (exercise_id,)).fetchone()
    if not exercise:
        flash("Exercise not found.", "error")
        return redirect(url_for("student_dashboard"))
    if not student_can_access_exercise(db, session["user_id"], exercise):
        if exercise_window_status(exercise) != "open":
            flash("This exercise is not currently available.", "error")
        else:
            flash("You do not have access to this exercise.", "error")
        return redirect(url_for("student_dashboard"))

    target_dfa = json.loads(exercise["target_dfa_json"])
    latest_submission = get_latest_submission_for_student(db, exercise_id, session["user_id"])
    if latest_submission:
        initial = dfa_to_initial_form(json.loads(latest_submission["answer_dfa_json"]))
    else:
        initial = blank_initial_from_target(target_dfa)

    result = None
    if request.method == "POST":
        answer_dfa, errors = build_dfa_from_form(request.form)
        if set(answer_dfa["alphabet"]) != set(target_dfa["alphabet"]):
            errors.append("Alphabet must match the exercise: " + ", ".join(target_dfa["alphabet"]) + ".")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "student_attempt.html",
                exercise=exercise,
                target_dfa=target_dfa,
                initial=request.form,
                result=None,
            )

        result = evaluate_submission(
            answer_dfa,
            target_dfa,
            bool(exercise["require_determinism"]),
            bool(exercise["require_minimality"]),
        )
        db.execute(
            """
            INSERT INTO submissions (exercise_id, user_id, answer_dfa_json, result_json, created_at, exercise_revision)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                exercise_id,
                session["user_id"],
                json.dumps(answer_dfa),
                json.dumps(result),
                now_iso(),
                exercise["revision"],
            ),
        )
        db.commit()
        flash("Submission checked and saved.", "success")
        return render_template(
            "student_attempt.html",
            exercise=exercise,
            target_dfa=target_dfa,
            initial=request.form,
            result=result,
        )

    return render_template(
        "student_attempt.html",
        exercise=exercise,
        target_dfa=target_dfa,
        initial=initial,
        result=result,
    )


@app.route("/student/submissions")
@login_required(role="student")
def student_submissions():
    db = get_db()
    submissions = db.execute(
        """
        SELECT s.*, e.title, e.revision AS current_revision
        FROM submissions s JOIN exercises e ON s.exercise_id = e.id
        WHERE s.user_id = ?
        ORDER BY s.id DESC
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("student_submissions.html", submissions=submissions, decode_json=json.loads)


# ---------------------------
# Startup
# ---------------------------
if __name__ == "__main__":
    init_db()
    seed_example_exercise_if_empty()
    app.run(debug=False, use_reloader=False, host="127.0.0.1", port=5000)
