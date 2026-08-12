from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pypdf import PdfReader

BASE_DIR = Path(__file__).resolve().parent
APP_ENV = os.getenv("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
UPLOAD_DIR = Path(os.getenv("APP_UPLOAD_DIR", str(BASE_DIR / "uploads"))).expanduser().resolve()
DB_PATH = DATA_DIR / "feedback.db"
SOURCE_PDF = DATA_DIR / "ThingsToDo_source.pdf"
BUNDLED_SOURCE_PDF = BASE_DIR / "data" / "ThingsToDo_source.pdf"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
if not SOURCE_PDF.exists() and BUNDLED_SOURCE_PDF.exists() and SOURCE_PDF != BUNDLED_SOURCE_PDF:
    import shutil
    shutil.copy2(BUNDLED_SOURCE_PDF, SOURCE_PDF)

def local_session_secret() -> str:
    """Use an environment secret when provided, otherwise create a private local secret file."""
    env_secret = os.getenv("SESSION_SECRET")
    if env_secret:
        return env_secret
    secret_file = DATA_DIR / ".session_secret"
    if not secret_file.exists():
        secret_file.write_text(secrets.token_urlsafe(48))
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
    return secret_file.read_text().strip()


app = FastAPI(title="Team Feedback Hub")
app.add_middleware(
    SessionMiddleware,
    secret_key=local_session_secret(),
    same_site="lax",
    https_only=IS_PRODUCTION,
    max_age=60 * 60 * 24 * 14,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

STATUS_CHOICES = [
    ("open", "Open"),
    ("assigned", "Assigned"),
    ("in_progress", "In Progress"),
    ("needs_review", "Needs Review"),
    ("resolved", "Resolved"),
    ("closed", "Closed"),
]
PRIORITY_CHOICES = [
    ("blocker", "Blocker"),
    ("critical", "Critical"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
]
TYPE_CHOICES = [
    ("bug", "Bug"),
    ("suggestion", "Suggestion"),
    ("task", "Task"),
    ("improvement", "Improvement"),
]
AREAS = ["General", "Tournament", "Hotel", "Rooms", "Roster", "Payments", "Refunds", "CSR", "Sales", "Compliance", "UI / Navigation", "Notifications", "Other"]
ROLE_CHOICES = ["admin", "manager", "team_member", "viewer"]


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 160_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$", 1)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 160_000).hex()
    return hmac.compare_digest(actual, expected)


LOGIN_ATTEMPTS: dict[str, list[float]] = {}

def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token

def check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Security token expired. Refresh the page and try again.")

def valid_http_url(value: str) -> bool:
    if not value:
        return True
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False

def login_is_limited(request: Request) -> bool:
    key = request.client.host if request.client else "unknown"
    cutoff = time.time() - 15 * 60
    recent = [t for t in LOGIN_ATTEMPTS.get(key, []) if t >= cutoff]
    LOGIN_ATTEMPTS[key] = recent
    return len(recent) >= 10

def record_failed_login(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())

def clear_login_failures(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    LOGIN_ATTEMPTS.pop(key, None)

def bootstrap_production_admin(conn: sqlite3.Connection) -> None:
    if not IS_PRODUCTION:
        return
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    admin_name = os.getenv("ADMIN_NAME", "Administrator").strip() or "Administrator"
    if not admin_email or len(admin_password) < 12:
        raise RuntimeError("Production requires ADMIN_EMAIL and ADMIN_PASSWORD of at least 12 characters.")
    existing = conn.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
    seeded = conn.execute("SELECT id FROM users WHERE email='robert@example.com'").fetchone()
    if existing:
        conn.execute(
            "UPDATE users SET name=?, password_hash=?, role='admin', active=1 WHERE id=?",
            (admin_name, hash_password(admin_password), existing["id"]),
        )
        admin_id = existing["id"]
    elif seeded:
        conn.execute(
            "UPDATE users SET name=?, email=?, password_hash=?, role='admin', active=1 WHERE id=?",
            (admin_name, admin_email, hash_password(admin_password), seeded["id"]),
        )
        admin_id = seeded["id"]
    else:
        cur = conn.execute(
            "INSERT INTO users(name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,1,?)",
            (admin_name, admin_email, hash_password(admin_password), "admin", now()),
        )
        admin_id = cur.lastrowid
    # Keep imported assignee profiles visible, but invalidate every demo password in production.
    for row in conn.execute("SELECT id FROM users WHERE email LIKE '%@example.com' AND id<>?", (admin_id,)).fetchall():
        conn.execute("UPDATE users SET password_hash=?, active=1 WHERE id=?", (hash_password(secrets.token_urlsafe(48)), row["id"]))
    conn.commit()

def init_db():
    conn = db()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'team_member',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            ticket_type TEXT NOT NULL DEFAULT 'bug',
            priority TEXT NOT NULL DEFAULT 'medium',
            status TEXT NOT NULL DEFAULT 'open',
            area TEXT NOT NULL DEFAULT 'General',
            submitted_by INTEGER,
            assigned_to INTEGER,
            due_date TEXT,
            steps_to_reproduce TEXT DEFAULT '',
            expected_behavior TEXT DEFAULT '',
            actual_behavior TEXT DEFAULT '',
            root_cause TEXT DEFAULT '',
            impact TEXT DEFAULT '',
            resolution TEXT DEFAULT '',
            technical_notes TEXT DEFAULT '',
            pr_refs TEXT DEFAULT '',
            commit_refs TEXT DEFAULT '',
            environment TEXT DEFAULT 'Staging',
            related_tickets TEXT DEFAULT '',
            source_text TEXT DEFAULT '',
            source_page TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(submitted_by) REFERENCES users(id),
            FOREIGN KEY(assigned_to) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            label TEXT NOT NULL DEFAULT 'Related link',
            url TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            stored_name TEXT NOT NULL,
            content_type TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        users = [
            ("Robert", "robert@example.com", "admin"),
            ("Patrick", "patrick@example.com", "manager"),
            ("Una", "una@example.com", "team_member"),
            ("Rowan", "rowan@example.com", "team_member"),
            ("Dave", "dave@example.com", "team_member"),
            ("Mat", "mat@example.com", "team_member"),
            ("Jay", "jay@example.com", "team_member"),
        ]
        for name, email, role in users:
            conn.execute(
                "INSERT INTO users (name,email,password_hash,role,active,created_at) VALUES (?,?,?,?,1,?)",
                (name, email, hash_password("welcome123"), role, now()),
            )
        conn.commit()

    bootstrap_production_admin(conn)

    if conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0 and SOURCE_PDF.exists():
        import_source_pdf(conn)

    conn.close()


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def valid_ticket_line(line: str):
    m = re.match(r"\s*(-?\d{3,5})\b(.*)$", line)
    if not m:
        return None
    raw, rest = m.group(1), m.group(2).strip()
    if raw == "422":
        return None
    signals = ("Open", "In progress", "Resolved", "Blocker", "Sugg", "PARTIALLY", "CONFIRMED", "Unproven", "ROOT CAUSE", "NOT on", "Not a missing")
    if any(s.lower() in rest.lower() for s in signals) or raw in {"-00171", "192", "0086"}:
        return raw.lstrip("-")
    return None


def detect_area(text: str) -> str:
    t = text.lower()
    if "refund" in t: return "Refunds"
    if "hotel" in t: return "Hotel"
    if "room" in t: return "Rooms"
    if "roster" in t or "player" in t or "eligibility" in t: return "Roster"
    if "payment" in t or "admin fee" in t or "deposit" in t: return "Payments"
    if "csr" in t: return "CSR"
    if "sales" in t: return "Sales"
    if "waiver" in t or "compliance" in t: return "Compliance"
    if "notification" in t or "remind" in t: return "Notifications"
    if "scroll" in t or "button" in t or "tab title" in t or "navigate" in t: return "UI / Navigation"
    if "tournament" in t or "division" in t or "ice time" in t: return "Tournament"
    return "General"


def detect_assignee(text: str) -> Optional[str]:
    patterns = [
        (r"\bPatrick\b|\bPat…", "Patrick"),
        (r"\bRowan\b|\bRo…", "Rowan"),
        (r"\bUna\b|\bUna…", "Una"),
        (r"\bDave\b", "Dave"),
        (r"\bMat\b", "Mat"),
        (r"\bJay\b", "Jay"),
    ]
    for pat, name in patterns:
        if re.search(pat, text[:1200], re.I):
            return name
    return None


def extract_description(chunk: str) -> str:
    clean = normalize_ws(chunk)
    # Prefer reporter-facing text that follows a priority + assignee marker.
    assignee = r"(?:Una…?|Rowan|Ro…?|Patrick|Pat…?|Dave|Mat|Jay)"
    marker = re.search(rf"(?:Blocker|Sugg…?|Suggestion)\s+{assignee}\s+(.+?)(?=https?://|$)", clean, re.I)
    if marker:
        desc = marker.group(1)
    else:
        marker = re.search(rf"(?:Open|In progress|Resolved)(?:\s*\([^)]*\))?\s+(?:Blocker\s+|Sugg…?\s+)?{assignee}\s+(.+?)(?=https?://|$)", clean, re.I)
        desc = marker.group(1) if marker else clean

    desc = re.sub(r"https?://\S+", "", desc)
    # Remove obvious engineering audit prefaces if they accidentally lead.
    desc = re.sub(r"^(?:CONFIRMED|ROOT CAUSE FOUND|PARTIALLY FIXED|Partially delivered|Unproven|NOT on staging|Not a missing feature).*?(?=(?:As an|I |The |On |There |It |Some |Deactivated ))", "", desc, flags=re.I)
    desc = normalize_ws(desc)
    if len(desc) > 900:
        desc = desc[:897].rstrip() + "…"
    return desc or "Imported feedback from ThingsToDo source document."


def title_from_description(desc: str, ticket_no: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", desc)[0]
    title = sentence.strip(" -")
    if len(title) > 92:
        title = title[:89].rstrip() + "…"
    return title or f"Feedback {ticket_no}"


def import_source_pdf(conn: sqlite3.Connection):
    reader = PdfReader(str(SOURCE_PDF))
    records = []
    current = None

    for page_num in range(2, min(36, len(reader.pages)) + 1):
        text = reader.pages[page_num - 1].extract_text() or ""
        lines = text.splitlines()
        for line in lines:
            ticket_id = valid_ticket_line(line)
            if ticket_id:
                if current:
                    records.append(current)
                current = {"ticket_no": ticket_id, "pages": [page_num], "lines": [line]}
            elif current:
                current["lines"].append(line)
                if page_num not in current["pages"]:
                    current["pages"].append(page_num)
    if current:
        records.append(current)

    user_map = {row["name"]: row["id"] for row in conn.execute("SELECT id,name FROM users")}
    submitter = user_map.get("Robert")
    seen = set()

    for rec in records:
        no = rec["ticket_no"]
        # The source contains ticket 00152 twice. Keep both without overwriting.
        if no in seen:
            suffix = 2
            candidate = f"{no}-{suffix}"
            while candidate in seen:
                suffix += 1
                candidate = f"{no}-{suffix}"
            no = candidate
        seen.add(no)
        chunk = "\n".join(rec["lines"])
        flat = normalize_ws(chunk)
        desc = extract_description(chunk)
        status = "resolved" if re.search(r"\bResolved\b", flat, re.I) else "in_progress" if re.search(r"\bIn progress\b", flat, re.I) else "open"
        priority = "blocker" if re.search(r"\bBlocker\b", flat, re.I) else "high" if re.search(r"\bHigh\b", flat, re.I) else "medium"
        ticket_type = "suggestion" if re.search(r"Sugg", flat, re.I) else "bug"
        assignee_name = detect_assignee(flat)
        assignee_id = user_map.get(assignee_name) if assignee_name else None
        urls = re.findall(r"https?://[^\s]+", chunk)
        # PDF extraction sometimes adds punctuation at the end.
        urls = [u.rstrip(".,);]") for u in urls]
        pr_refs = ", ".join(sorted(set(re.findall(r"PR\s*#\d+|#\d{3,5}", flat, re.I))))
        commits = ", ".join(sorted(set(re.findall(r"\b[a-f0-9]{9,40}\b", flat, re.I))))
        created = now()
        cur = conn.execute(
            """INSERT INTO tickets
            (ticket_no,title,description,ticket_type,priority,status,area,submitted_by,assigned_to,
             technical_notes,pr_refs,commit_refs,environment,source_text,source_page,created_at,updated_at,resolved_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (no, title_from_description(desc, no), desc, ticket_type, priority, status, detect_area(flat), submitter, assignee_id,
             flat, pr_refs, commits, "Staging", flat, ", ".join(map(str, rec["pages"])), created, created, created if status == "resolved" else None),
        )
        tid = cur.lastrowid
        for idx, url in enumerate(urls[:8], 1):
            conn.execute("INSERT INTO links (ticket_id,label,url,created_by,created_at) VALUES (?,?,?,?,?)", (tid, "Source link" if idx == 1 else f"Source link {idx}", url, submitter, created))
        conn.execute("INSERT INTO activity (ticket_id,user_id,action,details,created_at) VALUES (?,?,?,?,?)", (tid, submitter, "Imported", f"Imported from ThingsToDo PDF, page(s) {', '.join(map(str, rec['pages']))}", created))
    conn.commit()


def current_user(request: Request):
    uid = request.session.get("user_id")
    if not uid:
        return None
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
    conn.close()
    return user


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    return user


def can_manage(user) -> bool:
    return user and user["role"] in {"admin", "manager"}


def log_activity(conn, ticket_id, user_id, action, details=""):
    conn.execute("INSERT INTO activity (ticket_id,user_id,action,details,created_at) VALUES (?,?,?,?,?)", (ticket_id, user_id, action, details, now()))


def template_context(request: Request, **kwargs):
    user = current_user(request)
    return {
        "request": request,
        "user": user,
        "status_choices": STATUS_CHOICES,
        "priority_choices": PRIORITY_CHOICES,
        "type_choices": TYPE_CHOICES,
        "areas": AREAS,
        "can_manage": can_manage(user),
        "csrf_token": csrf_token(request),
        "is_production": IS_PRODUCTION,
        **kwargs,
    }


@app.exception_handler(401)
async def auth_redirect(request: Request, exc):
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", template_context(request, error=None))


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    if login_is_limited(request):
        return templates.TemplateResponse("login.html", template_context(request, error="Too many sign-in attempts. Try again in 15 minutes."), status_code=429)
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND active=1", (email.strip(),)).fetchone()
    conn.close()
    if not user or not verify_password(password, user["password_hash"]):
        record_failed_login(request)
        return templates.TemplateResponse("login.html", template_context(request, error="Email or password is incorrect."), status_code=400)
    clear_login_failures(request)
    request.session["user_id"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_user(request)
    conn = db()
    stats = {}
    for key in ["open", "in_progress", "needs_review", "resolved", "closed"]:
        stats[key] = conn.execute("SELECT COUNT(*) FROM tickets WHERE status=?", (key,)).fetchone()[0]
    stats["blocker"] = conn.execute("SELECT COUNT(*) FROM tickets WHERE priority='blocker' AND status NOT IN ('resolved','closed')").fetchone()[0]
    stats["unassigned"] = conn.execute("SELECT COUNT(*) FROM tickets WHERE assigned_to IS NULL AND status NOT IN ('resolved','closed')").fetchone()[0]
    my_tasks = conn.execute("""
        SELECT t.*, u.name assignee_name FROM tickets t LEFT JOIN users u ON t.assigned_to=u.id
        WHERE t.assigned_to=? AND t.status NOT IN ('resolved','closed')
        ORDER BY CASE t.priority WHEN 'blocker' THEN 1 WHEN 'critical' THEN 2 WHEN 'high' THEN 3 WHEN 'medium' THEN 4 ELSE 5 END, t.updated_at DESC LIMIT 8
    """, (user["id"],)).fetchall()
    workload = conn.execute("""
        SELECT u.name, COUNT(t.id) task_count FROM users u LEFT JOIN tickets t ON u.id=t.assigned_to AND t.status NOT IN ('resolved','closed')
        WHERE u.active=1 GROUP BY u.id ORDER BY task_count DESC, u.name LIMIT 8
    """).fetchall()
    recent = conn.execute("""
        SELECT a.*, u.name user_name, t.ticket_no, t.title FROM activity a
        LEFT JOIN users u ON a.user_id=u.id LEFT JOIN tickets t ON a.ticket_id=t.id
        ORDER BY a.id DESC LIMIT 10
    """).fetchall()
    conn.close()
    return templates.TemplateResponse("dashboard.html", template_context(request, stats=stats, my_tasks=my_tasks, workload=workload, recent=recent))


@app.get("/tickets", response_class=HTMLResponse)
def ticket_list(request: Request, q: str = "", status: str = "", priority: str = "", assignee: str = "", area: str = "", view: str = "all"):
    user = require_user(request)
    conn = db()
    where, params = ["1=1"], []
    if q:
        where.append("(t.ticket_no LIKE ? OR t.title LIKE ? OR t.description LIKE ? OR t.technical_notes LIKE ?)")
        term = f"%{q}%"; params += [term, term, term, term]
    if status:
        where.append("t.status=?"); params.append(status)
    if priority:
        where.append("t.priority=?"); params.append(priority)
    if assignee:
        where.append("t.assigned_to=?"); params.append(assignee)
    if area:
        where.append("t.area=?"); params.append(area)
    if view == "mine":
        where.append("t.assigned_to=?"); params.append(user["id"])
    elif view == "completed":
        where.append("t.status IN ('resolved','closed')")
    elif view == "active":
        where.append("t.status NOT IN ('resolved','closed')")
    tickets = conn.execute(f"""
        SELECT t.*, u.name assignee_name, s.name submitter_name,
               (SELECT COUNT(*) FROM attachments a WHERE a.ticket_id=t.id) attachment_count,
               (SELECT COUNT(*) FROM comments c WHERE c.ticket_id=t.id) comment_count
        FROM tickets t LEFT JOIN users u ON t.assigned_to=u.id LEFT JOIN users s ON t.submitted_by=s.id
        WHERE {' AND '.join(where)}
        ORDER BY CASE t.priority WHEN 'blocker' THEN 1 WHEN 'critical' THEN 2 WHEN 'high' THEN 3 WHEN 'medium' THEN 4 ELSE 5 END, t.updated_at DESC
    """, params).fetchall()
    users = conn.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return templates.TemplateResponse("tickets.html", template_context(request, tickets=tickets, users=users, filters={"q":q,"status":status,"priority":priority,"assignee":assignee,"area":area,"view":view}))


@app.get("/tickets/new", response_class=HTMLResponse)
def new_ticket_page(request: Request):
    require_user(request)
    conn = db(); users = conn.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name").fetchall(); conn.close()
    return templates.TemplateResponse("ticket_form.html", template_context(request, ticket=None, users=users))


@app.post("/tickets/new")
def create_ticket(
    request: Request,
    title: str = Form(...), description: str = Form(""), ticket_type: str = Form("bug"), priority: str = Form("medium"),
    status: str = Form("open"), area: str = Form("General"), assigned_to: str = Form(""), due_date: str = Form(""),
    steps_to_reproduce: str = Form(""), expected_behavior: str = Form(""), actual_behavior: str = Form(""),
    root_cause: str = Form(""), impact: str = Form(""), resolution: str = Form(""), technical_notes: str = Form(""),
    pr_refs: str = Form(""), commit_refs: str = Form(""), environment: str = Form("Staging"), related_tickets: str = Form(""),
    link_url: str = Form(""), link_label: str = Form("Related link"), files: list[UploadFile] = File(default=[]), csrf_token: str = Form(...),
):
    check_csrf(request, csrf_token)
    user = require_user(request); conn = db()
    next_num = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM tickets").fetchone()[0]
    ticket_no = f"N{next_num:05d}"
    assigned_id = int(assigned_to) if assigned_to else None
    created = now()
    cur = conn.execute("""INSERT INTO tickets
        (ticket_no,title,description,ticket_type,priority,status,area,submitted_by,assigned_to,due_date,steps_to_reproduce,expected_behavior,actual_behavior,root_cause,impact,resolution,technical_notes,pr_refs,commit_refs,environment,related_tickets,created_at,updated_at,resolved_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticket_no,title.strip(),description.strip(),ticket_type,priority,status,area,user["id"],assigned_id,due_date or None,steps_to_reproduce,expected_behavior,actual_behavior,root_cause,impact,resolution,technical_notes,pr_refs,commit_refs,environment,related_tickets,created,created,created if status in {"resolved","closed"} else None))
    tid = cur.lastrowid
    if link_url.strip():
        if not valid_http_url(link_url):
            conn.rollback(); conn.close(); raise HTTPException(400, "Links must start with http:// or https://")
        conn.execute("INSERT INTO links(ticket_id,label,url,created_by,created_at) VALUES(?,?,?,?,?)", (tid,link_label or "Related link",link_url.strip(),user["id"],created))
    save_uploads(conn, tid, user["id"], files)
    log_activity(conn, tid, user["id"], "Created ticket", title.strip())
    conn.commit(); conn.close()
    return RedirectResponse(f"/tickets/{tid}", status_code=303)


ALLOWED_UPLOAD_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt", ".csv",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"
}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def save_uploads(conn, ticket_id: int, user_id: int, uploads: list[UploadFile]):
    for upload in uploads or []:
        if not upload or not upload.filename:
            continue
        original_name = Path(upload.filename).name
        ext = Path(original_name).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(400, f"File type {ext or '(none)'} is not allowed.")
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", original_name)
        stored = f"{ticket_id}_{secrets.token_hex(6)}_{safe}"
        target = UPLOAD_DIR / stored
        total = 0
        try:
            with target.open("wb") as f:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(400, "Uploads are limited to 20 MB per file.")
                    f.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        conn.execute("INSERT INTO attachments(ticket_id,filename,stored_name,content_type,created_by,created_at) VALUES(?,?,?,?,?,?)", (ticket_id, original_name, stored, upload.content_type or "", user_id, now()))


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(request: Request, ticket_id: int):
    require_user(request); conn = db()
    ticket = conn.execute("""SELECT t.*,u.name assignee_name,s.name submitter_name FROM tickets t
        LEFT JOIN users u ON t.assigned_to=u.id LEFT JOIN users s ON t.submitted_by=s.id WHERE t.id=?""", (ticket_id,)).fetchone()
    if not ticket: conn.close(); raise HTTPException(404)
    links = conn.execute("SELECT * FROM links WHERE ticket_id=? ORDER BY id", (ticket_id,)).fetchall()
    attachments = conn.execute("SELECT a.*,u.name uploader_name FROM attachments a LEFT JOIN users u ON a.created_by=u.id WHERE a.ticket_id=? ORDER BY a.id DESC", (ticket_id,)).fetchall()
    comments = conn.execute("SELECT c.*,u.name user_name FROM comments c JOIN users u ON c.user_id=u.id WHERE c.ticket_id=? ORDER BY c.id", (ticket_id,)).fetchall()
    activity = conn.execute("SELECT a.*,u.name user_name FROM activity a LEFT JOIN users u ON a.user_id=u.id WHERE a.ticket_id=? ORDER BY a.id DESC", (ticket_id,)).fetchall()
    users = conn.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return templates.TemplateResponse("ticket_detail.html", template_context(request, ticket=ticket, links=links, attachments=attachments, comments=comments, activity=activity, users=users))


@app.get("/tickets/{ticket_id}/edit", response_class=HTMLResponse)
def edit_ticket_page(request: Request, ticket_id: int):
    user = require_user(request); conn = db()
    ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not ticket: conn.close(); raise HTTPException(404)
    if user["role"] == "viewer": conn.close(); raise HTTPException(403)
    users = conn.execute("SELECT id,name FROM users WHERE active=1 ORDER BY name").fetchall(); conn.close()
    return templates.TemplateResponse("ticket_form.html", template_context(request, ticket=ticket, users=users))


@app.post("/tickets/{ticket_id}/edit")
def edit_ticket(
    request: Request, ticket_id: int,
    title: str = Form(...), description: str = Form(""), ticket_type: str = Form("bug"), priority: str = Form("medium"),
    status: str = Form("open"), area: str = Form("General"), assigned_to: str = Form(""), due_date: str = Form(""),
    steps_to_reproduce: str = Form(""), expected_behavior: str = Form(""), actual_behavior: str = Form(""),
    root_cause: str = Form(""), impact: str = Form(""), resolution: str = Form(""), technical_notes: str = Form(""),
    pr_refs: str = Form(""), commit_refs: str = Form(""), environment: str = Form("Staging"), related_tickets: str = Form(""), csrf_token: str = Form(...),
):
    check_csrf(request, csrf_token)
    user = require_user(request)
    if user["role"] == "viewer": raise HTTPException(403)
    conn = db(); old = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not old: conn.close(); raise HTTPException(404)
    assigned_id = int(assigned_to) if assigned_to else None
    resolved_at = old["resolved_at"]
    if status in {"resolved", "closed"} and not resolved_at: resolved_at = now()
    if status not in {"resolved", "closed"}: resolved_at = None
    conn.execute("""UPDATE tickets SET title=?,description=?,ticket_type=?,priority=?,status=?,area=?,assigned_to=?,due_date=?,steps_to_reproduce=?,expected_behavior=?,actual_behavior=?,root_cause=?,impact=?,resolution=?,technical_notes=?,pr_refs=?,commit_refs=?,environment=?,related_tickets=?,updated_at=?,resolved_at=? WHERE id=?""",
                 (title.strip(),description.strip(),ticket_type,priority,status,area,assigned_id,due_date or None,steps_to_reproduce,expected_behavior,actual_behavior,root_cause,impact,resolution,technical_notes,pr_refs,commit_refs,environment,related_tickets,now(),resolved_at,ticket_id))
    changes=[]
    for field,label,newv in [("status","Status",status),("priority","Priority",priority),("assigned_to","Assignee",assigned_id),("area","Area",area)]:
        if old[field] != newv: changes.append(f"{label}: {old[field] or 'None'} → {newv or 'None'}")
    log_activity(conn,ticket_id,user["id"],"Updated ticket","; ".join(changes) or "Ticket details edited")
    conn.commit(); conn.close()
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.post("/tickets/{ticket_id}/quick-update")
def quick_update(request: Request, ticket_id: int, status: str = Form(""), assigned_to: str = Form(""), priority: str = Form(""), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user = require_user(request)
    if user["role"] == "viewer": raise HTTPException(403)
    conn = db(); old=conn.execute("SELECT * FROM tickets WHERE id=?",(ticket_id,)).fetchone()
    if not old: conn.close(); raise HTTPException(404)
    updates=[]; vals=[]; details=[]
    if status and status != old["status"]:
        updates.append("status=?"); vals.append(status); details.append(f"Status: {old['status']} → {status}")
        if status in {"resolved","closed"}: updates.append("resolved_at=?"); vals.append(now())
        else: updates.append("resolved_at=NULL")
    if priority and priority != old["priority"]:
        updates.append("priority=?"); vals.append(priority); details.append(f"Priority: {old['priority']} → {priority}")
    if assigned_to != "":
        new_assignee = int(assigned_to) if assigned_to != "0" else None
        if new_assignee != old["assigned_to"]:
            updates.append("assigned_to=?"); vals.append(new_assignee); details.append("Assignment changed")
    if updates:
        updates.append("updated_at=?"); vals.append(now()); vals.append(ticket_id)
        conn.execute(f"UPDATE tickets SET {', '.join(updates)} WHERE id=?", vals)
        log_activity(conn,ticket_id,user["id"],"Quick update","; ".join(details))
        conn.commit()
    conn.close(); return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.post("/tickets/{ticket_id}/comment")
def add_comment(request: Request, ticket_id: int, body: str = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user=require_user(request); conn=db()
    if body.strip():
        conn.execute("INSERT INTO comments(ticket_id,user_id,body,created_at) VALUES(?,?,?,?)",(ticket_id,user["id"],body.strip(),now()))
        log_activity(conn,ticket_id,user["id"],"Commented",body.strip()[:140]); conn.commit()
    conn.close(); return RedirectResponse(f"/tickets/{ticket_id}#comments",status_code=303)


@app.post("/tickets/{ticket_id}/link")
def add_link(request: Request, ticket_id: int, url: str = Form(...), label: str = Form("Related link"), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user=require_user(request); conn=db()
    if url.strip():
        if not valid_http_url(url):
            conn.close(); raise HTTPException(400, "Links must start with http:// or https://")
        conn.execute("INSERT INTO links(ticket_id,label,url,created_by,created_at) VALUES(?,?,?,?,?)",(ticket_id,label.strip() or "Related link",url.strip(),user["id"],now()))
        log_activity(conn,ticket_id,user["id"],"Added link",url.strip()); conn.commit()
    conn.close(); return RedirectResponse(f"/tickets/{ticket_id}#evidence",status_code=303)


@app.post("/tickets/{ticket_id}/attachments")
def add_attachments(request: Request, ticket_id: int, files: list[UploadFile] = File(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user=require_user(request); conn=db(); save_uploads(conn,ticket_id,user["id"],files)
    log_activity(conn,ticket_id,user["id"],"Uploaded attachment",f"{len(files)} file(s)"); conn.commit(); conn.close()
    return RedirectResponse(f"/tickets/{ticket_id}#evidence",status_code=303)


@app.get("/attachments/{attachment_id}")
def download_attachment(request: Request, attachment_id: int):
    require_user(request)
    conn = db()
    item = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
    conn.close()
    if not item:
        raise HTTPException(404)
    path = UPLOAD_DIR / item["stored_name"]
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type=item["content_type"] or "application/octet-stream", filename=item["filename"])

@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    user=require_user(request)
    if not can_manage(user): raise HTTPException(403)
    conn=db(); users=conn.execute("""SELECT u.*, (SELECT COUNT(*) FROM tickets t WHERE t.assigned_to=u.id AND t.status NOT IN ('resolved','closed')) active_tasks FROM users u ORDER BY u.name""").fetchall(); conn.close()
    return templates.TemplateResponse("users.html",template_context(request,users=users,role_choices=ROLE_CHOICES))


@app.post("/users")
def create_user(request: Request, name: str=Form(...), email: str=Form(...), role: str=Form("team_member"), password: str=Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user=require_user(request)
    if not can_manage(user): raise HTTPException(403)
    if len(password) < 12:
        raise HTTPException(400, "Temporary passwords must be at least 12 characters.")
    if role not in ROLE_CHOICES:
        raise HTTPException(400, "Invalid role.")
    conn=db()
    try:
        conn.execute("INSERT INTO users(name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,1,?)",(name.strip(),email.strip().lower(),hash_password(password),role,now())); conn.commit()
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400,"Email already exists")
    conn.close(); return RedirectResponse("/users",status_code=303)


@app.post("/users/{target_id}/update")
def update_user(request: Request, target_id: int, name: str = Form(...), email: str = Form(...), role: str = Form(...), active: str = Form("1"), new_password: str = Form(""), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user = require_user(request)
    if not can_manage(user):
        raise HTTPException(403)
    if role not in ROLE_CHOICES:
        raise HTTPException(400, "Invalid role.")
    is_active = 1 if active == "1" else 0
    if target_id == user["id"] and not is_active:
        raise HTTPException(400, "You cannot deactivate your own account.")
    if new_password and len(new_password) < 12:
        raise HTTPException(400, "New passwords must be at least 12 characters.")
    conn = db()
    target = conn.execute("SELECT * FROM users WHERE id=?", (target_id,)).fetchone()
    if not target:
        conn.close(); raise HTTPException(404)
    try:
        if new_password:
            conn.execute("UPDATE users SET name=?,email=?,role=?,active=?,password_hash=? WHERE id=?", (name.strip(), email.strip().lower(), role, is_active, hash_password(new_password), target_id))
        else:
            conn.execute("UPDATE users SET name=?,email=?,role=?,active=? WHERE id=?", (name.strip(), email.strip().lower(), role, is_active, target_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close(); raise HTTPException(400, "Email already exists")
    conn.close()
    return RedirectResponse("/users", status_code=303)

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    require_user(request)
    return templates.TemplateResponse("account.html", template_context(request, error=None, success=None))

@app.post("/account/password", response_class=HTMLResponse)
def change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...), csrf_token: str = Form(...)):
    check_csrf(request, csrf_token)
    user = require_user(request)
    if len(new_password) < 12:
        return templates.TemplateResponse("account.html", template_context(request, error="New password must be at least 12 characters.", success=None), status_code=400)
    if new_password != confirm_password:
        return templates.TemplateResponse("account.html", template_context(request, error="New passwords do not match.", success=None), status_code=400)
    if not verify_password(current_password, user["password_hash"]):
        return templates.TemplateResponse("account.html", template_context(request, error="Current password is incorrect.", success=None), status_code=400)
    conn = db()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
    conn.commit(); conn.close()
    return templates.TemplateResponse("account.html", template_context(request, error=None, success="Password changed."))

@app.get("/service-worker.js")
def service_worker():
    response = FileResponse(BASE_DIR / "static" / "service-worker.js", media_type="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/offline", response_class=HTMLResponse)
def offline_page(request: Request):
    return templates.TemplateResponse("offline.html", {"request": request})

@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request):
    require_user(request); conn=db()
    rows=conn.execute("""SELECT a.*,u.name user_name,t.ticket_no,t.title FROM activity a LEFT JOIN users u ON a.user_id=u.id LEFT JOIN tickets t ON a.ticket_id=t.id ORDER BY a.id DESC LIMIT 250""").fetchall(); conn.close()
    return templates.TemplateResponse("activity.html",template_context(request,rows=rows))


@app.get("/source")
def source_pdf(request: Request):
    require_user(request)
    return FileResponse(SOURCE_PDF, media_type="application/pdf", filename="ThingsToDo_source.pdf")


@app.get("/health")
def health():
    return {"ok": True, "app": "Team Feedback Hub", "environment": APP_ENV}


init_db()
