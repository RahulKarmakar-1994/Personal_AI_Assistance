import json
import html
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import finance


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "assistant.db"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")

app = Flask(__name__, static_folder="static", static_url_path="/static")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database():
    DATA_DIR.mkdir(exist_ok=True)
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL DEFAULT 'New conversation',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                due_at TEXT,
                priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low', 'normal', 'high')),
                completed INTEGER NOT NULL DEFAULT 0,
                reminder INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                starts_at TEXT NOT NULL,
                ends_at TEXT,
                reminder_minutes INTEGER NOT NULL DEFAULT 15,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                category TEXT NOT NULL DEFAULT 'Other',
                description TEXT NOT NULL DEFAULT '',
                spent_at TEXT NOT NULL,
                payment_method TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                amount REAL,
                due_at TEXT NOT NULL,
                recurrence TEXT NOT NULL DEFAULT 'none',
                paid INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                frequency TEXT NOT NULL DEFAULT 'daily',
                target INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS habit_logs (
                habit_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(habit_id, day),
                FOREIGN KEY(habit_id) REFERENCES habits(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS reading_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                topic TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                saved INTEGER NOT NULL DEFAULT 0,
                read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('model', ?)",
            (DEFAULT_MODEL,),
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('provider', 'ollama')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('groq_model', ?)",
            (DEFAULT_GROQ_MODEL,),
        )
    finance.initialize(DB_PATH)


def get_model():
    with db() as connection:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'model'"
        ).fetchone()
    return row["value"] if row else DEFAULT_MODEL


def get_setting(key, fallback=""):
    with db() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else fallback


def set_setting(key, value):
    with db() as connection:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def ollama_request(path, payload=None, timeout=120):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def groq_chat(messages, timeout=120):
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Groq is selected but GROQ_API_KEY is not configured.")
    payload = json.dumps(
        {"model": get_setting("groq_model", DEFAULT_GROQ_MODEL), "messages": messages}
    ).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    return str(result["choices"][0]["message"]["content"]).strip()


def parse_json_object(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("The model did not return structured JSON.")
    result = json.loads(text[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("The model returned an invalid command.")
    return result


def parse_spoken_datetime(text, local_now):
    try:
        base = datetime.fromisoformat(local_now)
    except (TypeError, ValueError):
        return None
    value = str(text).lower()
    target = base
    if "day after tomorrow" in value:
        target += timedelta(days=2)
    elif "tomorrow" in value:
        target += timedelta(days=1)
    elif "today" not in value:
        return None
    match = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b", value)
    if not match:
        match = re.search(r"\bat\s+(\d{1,2}):(\d{2})\b", value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    marker = (match.group(3) or "").replace(".", "")
    if marker == "pm" and hour < 12:
        hour += 12
    if marker == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def validate_assistant_command(result, pending=None, user_content="", local_now=""):
    allowed = {"task", "event", "note", "expense", "payment", "habit"}
    capability_question = re.match(
        r"^\s*(can|could|do|will|are)\s+you\s+"
        r"(?:also\s+)?(?:take|create|add|make|manage|track|set|record)\s+"
        r"(?:a\s+|an\s+|my\s+)?(?:notes?|tasks?|reminders?|events?|expenses?|payments?|habits?)"
        r"(?:\s+also)?\s*[?.!]*\s*$",
        str(user_content),
        flags=re.I,
    )
    if capability_question and not pending:
        return {"kind": "chat"}
    if str(result.get("kind", "")).lower() != "action":
        return {"kind": "chat"}
    action = str((pending or {}).get("action") or result.get("action", "")).lower()
    if action not in allowed:
        return {"kind": "chat"}
    fields = result.get("fields") if isinstance(result.get("fields"), dict) else {}
    previous = pending.get("fields", {}) if isinstance(pending, dict) and isinstance(pending.get("fields"), dict) else {}
    if previous:
        fields = {**previous, **{key: value for key, value in fields.items() if previous.get(key) in (None, "") and value not in (None, "")}}
    else:
        fields = {key: value for key, value in fields.items() if value not in (None, "")}
    date_field = "due_at" if action in {"task", "payment"} else "starts_at" if action == "event" else ""
    if date_field and fields.get(date_field) in (None, ""):
        parsed_date = parse_spoken_datetime(user_content, local_now)
        if parsed_date:
            fields[date_field] = parsed_date
    if action == "task" and fields.get("title"):
        fields["title"] = re.sub(r"^\s*remind\s+me\s+(?:to\s+)?", "", str(fields["title"]), flags=re.I).strip()
    required = {
        "task": ["title", "due_at"],
        "event": ["title", "starts_at"],
        "note": ["title", "content"],
        "expense": ["amount"],
        "payment": ["title", "due_at"],
        "habit": ["name", "frequency"],
    }[action]
    missing = [key for key in required if fields.get(key) in (None, "")]
    labels = {
        "title": "What should I call it?",
        "due_at": "When should I remind you? Please give a date and time.",
        "starts_at": "When does the event start?",
        "content": "What would you like the note to contain?",
        "amount": "What amount should I record?",
        "name": "Which habit would you like to track?",
        "frequency": "How often would you like to do it?",
    }
    question = labels.get(missing[0], "Could you provide the missing detail?") if missing else ""
    return {"kind": "action", "action": action, "fields": fields, "missing_fields": missing, "question": question}


def conversation_or_404(conversation_id):
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return row


def is_today_overview_request(content):
    """Return true when the user is asking Smriti to read today's stored plan."""
    text = " ".join(str(content or "").lower().split())
    mentions_today = bool(re.search(r"\b(today|this morning|this afternoon|this evening)\b", text))
    asks_to_read = bool(re.search(
        r"\b(what|which|tell|show|list|anything|any|plan|plans|planned|schedule|agenda|"
        r"task|tasks|reminder|reminders|event|events|due|doing|have)\b",
        text,
    ))
    return mentions_today and asks_to_read


def today_overview(day):
    """Build a dependable response from Smriti records without asking the LLM to remember them."""
    with db() as connection:
        tasks = connection.execute(
            "SELECT title,due_at,priority FROM tasks WHERE completed=0 "
            "AND (due_at IS NULL OR substr(due_at,1,10)<=?) "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,due_at",
            (day,),
        ).fetchall()
        events = connection.execute(
            "SELECT title,starts_at FROM events WHERE substr(starts_at,1,10)=? ORDER BY starts_at",
            (day,),
        ).fetchall()
        payments = connection.execute(
            "SELECT title,amount,due_at FROM payments WHERE paid=0 AND substr(due_at,1,10)<=? "
            "ORDER BY due_at",
            (day,),
        ).fetchall()
        habits = connection.execute(
            "SELECT h.name,h.target,COALESCE(l.count,0) AS today_count FROM habits h "
            "LEFT JOIN habit_logs l ON h.id=l.habit_id AND l.day=? WHERE h.active=1 ORDER BY h.id",
            (day,),
        ).fetchall()

    if not any((tasks, events, payments, habits)):
        return "Good morning. You don't have anything planned in Smriti for today yet."

    lines = ["Here’s what Smriti has for today:"]
    if events:
        lines.append("Schedule: " + "; ".join(
            f"{row['starts_at'][11:16]} — {row['title']}" for row in events
        ))
    if tasks:
        values = []
        for row in tasks:
            label = row["title"]
            if row["due_at"] and row["due_at"][:10] < day:
                label += " (overdue)"
            elif row["due_at"] and len(row["due_at"]) >= 16:
                label += f" at {row['due_at'][11:16]}"
            values.append(label)
        lines.append("Tasks: " + "; ".join(values))
    if payments:
        values = []
        for row in payments:
            amount = f" ₹{row['amount']:g}" if row["amount"] is not None else ""
            overdue = " (overdue)" if row["due_at"][:10] < day else ""
            values.append(f"{row['title']}{amount}{overdue}")
        lines.append("Payments due: " + "; ".join(values))
    if habits:
        remaining = [row for row in habits if row["today_count"] < row["target"]]
        if remaining:
            lines.append("Habits remaining: " + "; ".join(
                f"{row['name']} ({row['today_count']}/{row['target']})" for row in remaining
            ))
        else:
            lines.append("Habits: all completed for today.")
    return "\n".join(lines)


DISCOVERY_FEEDS = (
    ("AI news", "OpenAI", "https://openai.com/news/rss.xml"),
    ("AI news", "Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("AI research", "arXiv AI", "https://export.arxiv.org/rss/cs.AI"),
    ("Materials science", "arXiv Materials", "https://export.arxiv.org/rss/cond-mat.mtrl-sci"),
)


def xml_name(element):
    return element.tag.rsplit("}", 1)[-1].lower()


def child_text(element, names):
    for child in element:
        if xml_name(child) in names:
            return " ".join("".join(child.itertext()).split())
    return ""


def clean_feed_text(value, limit):
    plain = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return " ".join(plain.split())[:limit]


def fetch_discovery_feed(topic, source, url):
    req = urllib.request.Request(url, headers={"User-Agent": "Smriti/0.1 personal research reader"})
    with urllib.request.urlopen(req, timeout=20) as response:
        root = ET.fromstring(response.read(2_000_000))
    items = [node for node in root.iter() if xml_name(node) in {"item", "entry"}]
    results = []
    for item in items[:20]:
        title = clean_feed_text(child_text(item, {"title"}), 500)
        link = child_text(item, {"link"})
        if not link:
            link_node = next((x for x in item if xml_name(x) == "link" and x.get("href")), None)
            link = link_node.get("href", "") if link_node is not None else ""
        summary = clean_feed_text(child_text(item, {"description", "summary", "content"}), 4000)
        published = child_text(item, {"pubdate", "published", "updated", "date"})
        if title and link.startswith(("http://", "https://")):
            results.append({"title": title, "url": link[:2000], "source": source, "topic": topic,
                            "summary": summary, "published_at": published[:100] or None})
    return results


@app.get("/api/discover")
def discover_collection():
    query = str(request.args.get("query", "")).strip()[:200]
    view = str(request.args.get("view", "all"))
    clauses, params = [], []
    if query:
        clauses.append("(title LIKE ? OR summary LIKE ? OR source LIKE ? OR topic LIKE ?)")
        needle = f"%{query}%"
        params.extend([needle, needle, needle, needle])
    if view == "saved":
        clauses.append("saved = 1")
    elif view == "unread":
        clauses.append("read = 0")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM reading_items" + where +
            " ORDER BY saved DESC, COALESCE(published_at, created_at) DESC, id DESC LIMIT 200",
            params,
        ).fetchall()
        last = connection.execute("SELECT value FROM settings WHERE key='discover_last_refresh'").fetchone()
    return jsonify({"items": [dict(row) for row in rows], "last_refresh": last["value"] if last else None})


@app.post("/api/discover/refresh")
def refresh_discovery():
    collected, errors = [], []
    for topic, source, url in DISCOVERY_FEEDS:
        try:
            collected.extend(fetch_discovery_feed(topic, source, url))
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            errors.append(f"{source}: {type(exc).__name__}")
    stamp = now_iso()
    with db() as connection:
        for item in collected:
            connection.execute(
                "INSERT INTO reading_items(title,url,source,topic,summary,published_at,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
                "title=excluded.title,source=excluded.source,topic=excluded.topic,"
                "summary=excluded.summary,published_at=excluded.published_at",
                (item["title"], item["url"], item["source"], item["topic"], item["summary"],
                 item["published_at"], stamp),
            )
        connection.execute(
            "INSERT INTO settings(key,value) VALUES('discover_last_refresh',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (stamp,),
        )
    return jsonify({"fetched": len(collected), "errors": errors, "refreshed_at": stamp})


@app.put("/api/discover/<int:item_id>")
def update_discovery_item(item_id):
    body = request.get_json(silent=True) or {}
    day = str(body.get("day", ""))[:10]
    with db() as connection:
        current = connection.execute("SELECT * FROM reading_items WHERE id=?", (item_id,)).fetchone()
        if not current:
            return jsonify({"error": "Reading item not found."}), 404
        saved = 1 if body.get("saved", current["saved"]) else 0
        read = 1 if body.get("read", current["read"]) else 0
        connection.execute("UPDATE reading_items SET saved=?,read=? WHERE id=?", (saved, read, item_id))
        if read and not current["read"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            habit = connection.execute(
                "SELECT id FROM habits WHERE name='Read AI & science updates' AND active=1 LIMIT 1"
            ).fetchone()
            if habit:
                connection.execute(
                    "INSERT INTO habit_logs(habit_id,day,count) VALUES(?,?,1) "
                    "ON CONFLICT(habit_id,day) DO UPDATE SET count=count+1",
                    (habit["id"], day),
                )
        row = connection.execute("SELECT * FROM reading_items WHERE id=?", (item_id,)).fetchone()
    return jsonify(dict(row))


@app.post("/api/discover/reading-habit")
def create_reading_habit():
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM habits WHERE name='Read AI & science updates' AND active=1 LIMIT 1"
        ).fetchone()
        if not row:
            cursor = connection.execute(
                "INSERT INTO habits(name,frequency,target,active,created_at) VALUES(?,?,?,?,?)",
                ("Read AI & science updates", "daily", 1, 1, now_iso()),
            )
            row = connection.execute("SELECT * FROM habits WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest")


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.get("/api/status")
def status():
    provider = get_setting("provider", "ollama")
    try:
        tags = ollama_request("/api/tags", timeout=4)
        names = [item.get("name", "") for item in tags.get("models", [])]
        ollama_online = True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        ollama_online, names = False, []
    groq_ready = bool(os.environ.get("GROQ_API_KEY", "").strip())
    online = groq_ready if provider == "groq" else ollama_online
    model = get_setting("groq_model", DEFAULT_GROQ_MODEL) if provider == "groq" else get_model()
    return jsonify({"online": online, "provider": provider, "model": model,
                    "models": names, "ollama_online": ollama_online, "groq_ready": groq_ready})


@app.get("/api/settings")
def read_settings():
    return jsonify({"model": get_model(), "provider": get_setting("provider", "ollama"),
                    "groq_model": get_setting("groq_model", DEFAULT_GROQ_MODEL),
                    "groq_ready": bool(os.environ.get("GROQ_API_KEY", "").strip())})


@app.put("/api/settings")
def update_settings():
    body = request.get_json(silent=True) or {}
    model = str(body.get("model", "")).strip()
    provider = str(body.get("provider", "ollama")).strip().lower()
    groq_model = str(body.get("groq_model", DEFAULT_GROQ_MODEL)).strip()
    if not model or len(model) > 100:
        return jsonify({"error": "Enter a valid model name."}), 400
    if provider not in ("ollama", "groq"):
        return jsonify({"error": "Provider must be ollama or groq."}), 400
    if not groq_model or len(groq_model) > 120:
        return jsonify({"error": "Enter a valid Groq model name."}), 400
    set_setting("model", model)
    set_setting("provider", provider)
    set_setting("groq_model", groq_model)
    return jsonify({"model": model, "provider": provider, "groq_model": groq_model})


def validated_text(body, key, label, maximum=5000):
    value = str(body.get(key, "")).strip()
    if not value:
        raise ValueError(f"{label} is required.")
    if len(value) > maximum:
        raise ValueError(f"{label} is too long.")
    return value


@app.get("/api/dashboard")
def dashboard():
    day = request.args.get("date", "")[:10]
    with db() as connection:
        tasks = connection.execute(
            "SELECT * FROM tasks WHERE completed = 0 AND (due_at IS NULL OR substr(due_at, 1, 10) <= ?) "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, due_at",
            (day,),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM events WHERE substr(starts_at, 1, 10) = ? ORDER BY starts_at", (day,)
        ).fetchall()
        notes = connection.execute(
            "SELECT * FROM notes WHERE pinned = 1 ORDER BY updated_at DESC LIMIT 4"
        ).fetchall()
        payments = connection.execute(
            "SELECT * FROM payments WHERE paid = 0 AND substr(due_at,1,10) <= ? ORDER BY due_at", (day,)
        ).fetchall()
        habits = connection.execute(
            "SELECT h.*, COALESCE(l.count,0) AS today_count FROM habits h LEFT JOIN habit_logs l "
            "ON h.id=l.habit_id AND l.day=? WHERE h.active=1 ORDER BY h.id", (day,)
        ).fetchall()
    return jsonify({"tasks": [dict(x) for x in tasks], "events": [dict(x) for x in events],
                    "pinned_notes": [dict(x) for x in notes], "payments": [dict(x) for x in payments],
                    "habits": [dict(x) for x in habits]})


@app.route("/api/expenses", methods=["GET", "POST"])
def expenses_collection():
    if request.method == "GET":
        month = request.args.get("month", "")[:7]
        with db() as connection:
            if month:
                rows = connection.execute("SELECT * FROM expenses WHERE substr(spent_at,1,7)=? ORDER BY spent_at DESC, id DESC", (month,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM expenses ORDER BY spent_at DESC, id DESC").fetchall()
        return jsonify([dict(x) for x in rows])
    body = request.get_json(silent=True) or {}
    try:
        amount = float(body.get("amount", 0))
        if amount <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Enter a valid positive amount."}), 400
    stamp = now_iso()
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO expenses(amount,currency,category,description,spent_at,payment_method,created_at) VALUES(?,?,?,?,?,?,?)",
            (amount, str(body.get("currency", "INR"))[:8], str(body.get("category", "Other"))[:60],
             str(body.get("description", ""))[:1000], body.get("spent_at") or stamp[:10],
             str(body.get("payment_method", ""))[:60], stamp),
        )
        row = connection.execute("SELECT * FROM expenses WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/expenses/<int:item_id>")
def delete_expense(item_id):
    with db() as connection:
        cursor = connection.execute("DELETE FROM expenses WHERE id=?", (item_id,))
    return ("", 204) if cursor.rowcount else (jsonify({"error": "Expense not found."}), 404)


@app.route("/api/payments", methods=["GET", "POST"])
def payments_collection():
    if request.method == "GET":
        with db() as connection:
            rows = connection.execute("SELECT * FROM payments ORDER BY paid, due_at").fetchall()
        return jsonify([dict(x) for x in rows])
    body = request.get_json(silent=True) or {}
    try:
        title = validated_text(body, "title", "Payment title", 300)
        due_at = validated_text(body, "due_at", "Due date", 40)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    recurrence = body.get("recurrence", "none")
    if recurrence not in ("none", "weekly", "monthly", "yearly"):
        recurrence = "none"
    stamp = now_iso()
    amount = body.get("amount")
    amount = float(amount) if amount not in (None, "") else None
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO payments(title,amount,due_at,recurrence,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (title, amount, due_at, recurrence, str(body.get("notes", ""))[:1000], stamp, stamp),
        )
        row = connection.execute("SELECT * FROM payments WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/payments/<int:item_id>", methods=["PUT", "DELETE"])
def payment_item(item_id):
    if request.method == "DELETE":
        with db() as connection:
            cursor = connection.execute("DELETE FROM payments WHERE id=?", (item_id,))
        return ("", 204) if cursor.rowcount else (jsonify({"error": "Payment not found."}), 404)
    body = request.get_json(silent=True) or {}
    with db() as connection:
        current = connection.execute("SELECT * FROM payments WHERE id=?", (item_id,)).fetchone()
        if not current:
            return jsonify({"error": "Payment not found."}), 404
        connection.execute("UPDATE payments SET paid=?,updated_at=? WHERE id=?", (1 if body.get("paid") else 0, now_iso(), item_id))
        row = connection.execute("SELECT * FROM payments WHERE id=?", (item_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/habits", methods=["GET", "POST"])
def habits_collection():
    day = request.args.get("day", "")[:10]
    if request.method == "GET":
        with db() as connection:
            rows = connection.execute(
                "SELECT h.*,COALESCE(l.count,0) AS today_count FROM habits h LEFT JOIN habit_logs l "
                "ON h.id=l.habit_id AND l.day=? WHERE h.active=1 ORDER BY h.id", (day,)
            ).fetchall()
        return jsonify([dict(x) for x in rows])
    body = request.get_json(silent=True) or {}
    try:
        name = validated_text(body, "name", "Habit name", 200)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with db() as connection:
        cursor = connection.execute("INSERT INTO habits(name,frequency,target,created_at) VALUES(?,?,?,?)",
                                    (name, body.get("frequency", "daily"), max(1, int(body.get("target", 1))), now_iso()))
        row = connection.execute("SELECT *,0 AS today_count FROM habits WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/habits/<int:item_id>", methods=["PUT", "DELETE"])
def habit_item(item_id):
    if request.method == "DELETE":
        with db() as connection:
            cursor = connection.execute("DELETE FROM habits WHERE id=?", (item_id,))
        return ("", 204) if cursor.rowcount else (jsonify({"error": "Habit not found."}), 404)
    body = request.get_json(silent=True) or {}
    day = str(body.get("day", ""))[:10]
    count = max(0, int(body.get("count", 0)))
    with db() as connection:
        if count:
            connection.execute("INSERT INTO habit_logs(habit_id,day,count) VALUES(?,?,?) ON CONFLICT(habit_id,day) DO UPDATE SET count=excluded.count", (item_id, day, count))
        else:
            connection.execute("DELETE FROM habit_logs WHERE habit_id=? AND day=?", (item_id, day))
        row = connection.execute("SELECT h.*,? AS today_count FROM habits h WHERE id=?", (count, item_id)).fetchone()
    return jsonify(dict(row)) if row else (jsonify({"error": "Habit not found."}), 404)


@app.route("/api/tasks", methods=["GET", "POST"])
def tasks_collection():
    if request.method == "GET":
        with db() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY completed, CASE priority WHEN 'high' THEN 0 "
                "WHEN 'normal' THEN 1 ELSE 2 END, due_at, id DESC"
            ).fetchall()
        return jsonify([dict(x) for x in rows])
    body = request.get_json(silent=True) or {}
    try:
        title = validated_text(body, "title", "Task title", 300)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    priority = body.get("priority", "normal")
    if priority not in ("low", "normal", "high"):
        priority = "normal"
    stamp = now_iso()
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO tasks(title, details, due_at, priority, reminder, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (title, str(body.get("details", ""))[:5000], body.get("due_at") or None, priority,
             1 if body.get("reminder") else 0, stamp, stamp),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/tasks/<int:item_id>", methods=["PUT", "DELETE"])
def task_item(item_id):
    if request.method == "DELETE":
        with db() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (item_id,))
        return ("", 204) if cursor.rowcount else (jsonify({"error": "Task not found."}), 404)
    body = request.get_json(silent=True) or {}
    with db() as connection:
        current = connection.execute("SELECT * FROM tasks WHERE id = ?", (item_id,)).fetchone()
        if not current:
            return jsonify({"error": "Task not found."}), 404
        connection.execute(
            "UPDATE tasks SET title=?, details=?, due_at=?, priority=?, completed=?, reminder=?, updated_at=? WHERE id=?",
            (str(body.get("title", current["title"]))[:300], str(body.get("details", current["details"]))[:5000],
             body.get("due_at", current["due_at"]), body.get("priority", current["priority"]),
             1 if body.get("completed", current["completed"]) else 0,
             1 if body.get("reminder", current["reminder"]) else 0, now_iso(), item_id),
        )
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (item_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/notes", methods=["GET", "POST"])
def notes_collection():
    if request.method == "GET":
        with db() as connection:
            rows = connection.execute("SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC").fetchall()
        return jsonify([dict(x) for x in rows])
    body = request.get_json(silent=True) or {}
    try:
        title = validated_text(body, "title", "Note title", 300)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    stamp = now_iso()
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO notes(title, content, pinned, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            (title, str(body.get("content", ""))[:20000], 1 if body.get("pinned") else 0, stamp, stamp),
        )
        row = connection.execute("SELECT * FROM notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/notes/<int:item_id>", methods=["PUT", "DELETE"])
def note_item(item_id):
    if request.method == "DELETE":
        with db() as connection:
            cursor = connection.execute("DELETE FROM notes WHERE id = ?", (item_id,))
        return ("", 204) if cursor.rowcount else (jsonify({"error": "Note not found."}), 404)
    body = request.get_json(silent=True) or {}
    with db() as connection:
        current = connection.execute("SELECT * FROM notes WHERE id = ?", (item_id,)).fetchone()
        if not current:
            return jsonify({"error": "Note not found."}), 404
        connection.execute(
            "UPDATE notes SET title=?, content=?, pinned=?, updated_at=? WHERE id=?",
            (str(body.get("title", current["title"]))[:300], str(body.get("content", current["content"]))[:20000],
             1 if body.get("pinned", current["pinned"]) else 0, now_iso(), item_id),
        )
        row = connection.execute("SELECT * FROM notes WHERE id = ?", (item_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/events", methods=["GET", "POST"])
def events_collection():
    if request.method == "GET":
        start = request.args.get("start", "")[:10]
        end = request.args.get("end", "9999-12-31")[:10]
        with db() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE substr(starts_at,1,10) BETWEEN ? AND ? ORDER BY starts_at", (start, end)
            ).fetchall()
        return jsonify([dict(x) for x in rows])
    body = request.get_json(silent=True) or {}
    try:
        title = validated_text(body, "title", "Event title", 300)
        starts_at = validated_text(body, "starts_at", "Start time", 40)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    stamp = now_iso()
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO events(title, details, starts_at, ends_at, reminder_minutes, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (title, str(body.get("details", ""))[:5000], starts_at, body.get("ends_at") or None,
             max(0, min(int(body.get("reminder_minutes", 15)), 10080)), stamp, stamp),
        )
        row = connection.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.delete("/api/events/<int:item_id>")
def delete_event(item_id):
    with db() as connection:
        cursor = connection.execute("DELETE FROM events WHERE id = ?", (item_id,))
    return ("", 204) if cursor.rowcount else (jsonify({"error": "Event not found."}), 404)


@app.get("/api/finance/portfolio")
def finance_portfolio():
    valuation_date = request.args.get("date", "")[:10] or None
    try:
        return jsonify(finance.portfolio(DB_PATH, valuation_date))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/finance/transactions")
def finance_add_transaction():
    try:
        transaction_id = finance.add_transaction(DB_PATH, request.get_json(silent=True) or {})
        return jsonify({"id": transaction_id}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/finance/import")
def finance_import_transactions():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Choose a Smriti mutual-fund CSV file."}), 400
    if not upload.filename.lower().endswith(".csv"):
        return jsonify({"error": "This first importer accepts CSV only. PDF CAS support requires a verified sample format."}), 400
    file_bytes = upload.read(5_000_001)
    if len(file_bytes) > 5_000_000:
        return jsonify({"error": "The import file must be smaller than 5 MB."}), 413
    try:
        return jsonify(finance.import_csv(DB_PATH, file_bytes, upload.filename)), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/finance/imports")
def finance_imports():
    return jsonify(finance.import_history(DB_PATH))


@app.post("/api/finance/nav/refresh")
def finance_refresh_nav():
    try:
        return jsonify(finance.refresh_nav(DB_PATH))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return jsonify({"error": f"NAV refresh failed: {exc}"}), 502


@app.get("/api/conversations")
def list_conversations():
    with db() as connection:
        rows = connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC"
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/api/conversations")
def create_conversation():
    timestamp = now_iso()
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO conversations(title, created_at, updated_at) VALUES(?, ?, ?)",
            ("New conversation", timestamp, timestamp),
        )
        conversation_id = cursor.lastrowid
    return jsonify({"id": conversation_id, "title": "New conversation"}), 201


@app.get("/api/conversations/<int:conversation_id>")
def get_conversation(conversation_id):
    conversation = conversation_or_404(conversation_id)
    if not conversation:
        return jsonify({"error": "Conversation not found."}), 404
    with db() as connection:
        messages = connection.execute(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    result = dict(conversation)
    result["messages"] = [dict(row) for row in messages]
    return jsonify(result)


@app.delete("/api/conversations/<int:conversation_id>")
def delete_conversation(conversation_id):
    with db() as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
    if cursor.rowcount == 0:
        return jsonify({"error": "Conversation not found."}), 404
    return "", 204


@app.post("/api/assistant/interpret")
def interpret_assistant_message():
    body = request.get_json(silent=True) or {}
    content = str(body.get("content", "")).strip()
    pending = body.get("pending") if isinstance(body.get("pending"), dict) else None
    local_now = str(body.get("local_now", ""))[:40]
    user_timezone = str(body.get("timezone", "Asia/Kolkata"))[:80]
    if not content or len(content) > 2000:
        return jsonify({"error": "Please provide a shorter assistant message."}), 400
    prompt = (
        "Classify a message for the Smriti personal assistant. Return ONLY one JSON object. "
        "A question about Smriti's capabilities, such as 'Can you take notes?' or 'Can you set "
        "reminders?', is kind='chat', not an action. 'Take a note' without actual note content is "
        "an action with content=null so Smriti can ask what to record. "
        "Use kind='action' only when the user wants to create a task/reminder, calendar event, "
        "note, expense, payment reminder, or habit. Otherwise use kind='chat'. "
        "For an action return action as task|event|note|expense|payment|habit and a fields object. "
        "Task fields: title, details, due_at, priority, reminder. Event: title, details, starts_at, "
        "ends_at, reminder_minutes. Note: title, content, pinned. Expense: amount, category, "
        "spent_at, payment_method, description. Payment: title, amount, due_at, recurrence, notes. "
        "Habit: name, frequency, target. Dates must be local ISO YYYY-MM-DDTHH:MM and date-only "
        "values YYYY-MM-DD. Resolve today/tomorrow using the supplied clock. Never invent a missing "
        "date, time, or amount; use null. If pending_action is present, keep its action and extract "
        "only the missing information from the new message. "
        "Example output: {\"kind\":\"action\",\"action\":\"task\",\"fields\":{\"title\":\"Call bank\","
        "\"due_at\":null,\"reminder\":true}}. "
        f"Local clock: {local_now}; timezone: {user_timezone}; "
        f"pending_action: {json.dumps(pending, ensure_ascii=False) if pending else 'null'}; "
        f"user_message: {json.dumps(content, ensure_ascii=False)}"
    )
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": content}]
    try:
        if get_setting("provider", "ollama") == "groq":
            raw = groq_chat(messages, timeout=45)
        else:
            result = ollama_request(
                "/api/chat",
                {"model": get_model(), "messages": messages, "stream": False, "format": "json",
                 "options": {"temperature": 0}},
                timeout=45,
            )
            raw = result.get("message", {}).get("content", "")
        command = validate_assistant_command(parse_json_object(raw), pending, content, local_now)
        command["source"] = get_setting("provider", "ollama")
        return jsonify(command)
    except (urllib.error.URLError, TimeoutError, RuntimeError):
        return jsonify({"kind": "unavailable", "source": "fallback"}), 200
    except (urllib.error.HTTPError, json.JSONDecodeError, ValueError, KeyError):
        return jsonify({"kind": "invalid", "source": "fallback"}), 200


@app.post("/api/conversations/<int:conversation_id>/messages")
def send_message(conversation_id):
    if not conversation_or_404(conversation_id):
        return jsonify({"error": "Conversation not found."}), 404

    body = request.get_json(silent=True) or {}
    content = str(body.get("content", "")).strip()
    local_date = str(body.get("local_date", ""))[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", local_date):
        local_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    if not content:
        return jsonify({"error": "Message cannot be empty."}), 400
    if len(content) > 12000:
        return jsonify({"error": "Message is too long for this version."}), 400

    timestamp = now_iso()
    with db() as connection:
        connection.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) "
            "VALUES(?, 'user', ?, ?)",
            (conversation_id, content, timestamp),
        )
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT 16",
            (conversation_id,),
        ).fetchall()

    history = [dict(row) for row in reversed(rows)]
    answer = today_overview(local_date) if is_today_overview_request(content) else ""
    messages = [
        {
            "role": "system",
            "content": (
                "You are Smriti, a concise, friendly personal assistant. Be honest when "
                "you are unsure. Smriti can create notes, tasks, reminders, calendar drafts, "
                "expenses, payment reminders, and habits through its structured action workflow. "
                "When asked whether you can do these things, say yes and briefly explain how. "
                "Never claim that an action was saved unless the app confirms it."
            ),
        },
        *history,
    ]

    try:
        if answer:
            pass
        elif get_setting("provider", "ollama") == "groq":
            answer = groq_chat(messages)
        else:
            result = ollama_request(
                "/api/chat",
                {"model": get_model(), "messages": messages, "stream": False},
            )
            answer = str(result.get("message", {}).get("content", "")).strip()
        if not answer:
            raise ValueError("Ollama returned an empty response")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return jsonify({"error": "Ollama rejected the request.", "detail": detail}), 502
    except (urllib.error.URLError, TimeoutError):
        return jsonify({"error": "The selected AI provider is unavailable."}), 503
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except (json.JSONDecodeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 502

    finished = now_iso()
    title = " ".join(content.split())[:42]
    if len(content) > 42:
        title += "…"
    with db() as connection:
        cursor = connection.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) "
            "VALUES(?, 'assistant', ?, ?)",
            (conversation_id, answer, finished),
        )
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()["count"]
        connection.execute(
            "UPDATE conversations SET title = CASE WHEN ? <= 2 THEN ? ELSE title END, "
            "updated_at = ? WHERE id = ?",
            (count, title, finished, conversation_id),
        )
    return jsonify(
        {"id": cursor.lastrowid, "role": "assistant", "content": answer, "created_at": finished}
    )


initialize_database()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8000")), debug=False)

