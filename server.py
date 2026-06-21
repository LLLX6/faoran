from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
import base64
import hashlib
import json
import mimetypes
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.request

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
UPLOAD_DIR = Path(os.environ.get("FORAN_UPLOAD_DIR") or (PUBLIC_DIR / "uploads"))
DB_PATH = Path(os.environ.get("FORAN_DB_PATH") or (BASE_DIR / "foran.sqlite3"))
ADMIN_CODE = os.environ.get("FORAN_ADMIN_CODE", "1995")
ADMIN_HASH = hashlib.sha256(ADMIN_CODE.encode("utf-8")).hexdigest()
TOKENS = {}

ALL_PERMISSIONS = [
    "view_reports",
    "manage_providers",
    "review_requests",
    "manage_settings",
    "manage_admins",
    "backup",
]
ROLE_PERMISSIONS = {
    "owner": ALL_PERMISSIONS,
    "manager": ["view_reports", "manage_providers", "review_requests", "backup"],
    "support": ["view_reports", "review_requests"],
}


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def slug(prefix):
    return f"{prefix}_{secrets.token_hex(4)}{int(time.time())}"


def hash_secret(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def jdump(value):
    return json.dumps(value, ensure_ascii=False)


def jload(value, fallback=None):
    if value in (None, ""):
        return fallback
    return json.loads(value)


def normalize_phone(raw):
    phone = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if phone.startswith("0"):
        phone = "968" + phone[1:]
    if len(phone) == 8:
        phone = "968" + phone
    return phone


def default_provider_pin(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return (digits[-4:] or "1234").rjust(4, "0")


SEED_CATEGORIES = [
    {
        "id": "homecare",
        "icon": "🏠",
        "ar": "صيانة المنزل",
        "en": "Home maintenance",
        "active": 1,
        "services": [
            {"id": "electrician", "icon": "⚡", "ar": "كهربائي", "en": "Electrician", "active": 1},
            {"id": "plumber", "icon": "🚿", "ar": "سباك", "en": "Plumber", "active": 1},
            {"id": "ac", "icon": "❄️", "ar": "صيانة مكيفات", "en": "AC maintenance", "active": 1},
            {"id": "appliances", "icon": "🔧", "ar": "صيانة أجهزة منزلية", "en": "Home appliances", "active": 1},
        ],
    },
    {
        "id": "cleaning",
        "icon": "🧼",
        "ar": "التنظيف",
        "en": "Cleaning",
        "active": 1,
        "services": [
            {"id": "home_clean", "icon": "🏡", "ar": "تنظيف منازل", "en": "Home cleaning", "active": 1},
            {"id": "sofa", "icon": "🛋️", "ar": "تنظيف كنب", "en": "Sofa cleaning", "active": 1},
            {"id": "carpet", "icon": "🧽", "ar": "تنظيف سجاد", "en": "Carpet cleaning", "active": 1},
        ],
    },
    {
        "id": "moving",
        "icon": "🚚",
        "ar": "النقل",
        "en": "Moving",
        "active": 1,
        "services": [
            {"id": "furniture_move", "icon": "🚛", "ar": "نقل أثاث", "en": "Furniture moving", "active": 1},
            {"id": "delivery", "icon": "📦", "ar": "توصيل", "en": "Delivery", "active": 1},
        ],
    },
    {
        "id": "cars",
        "icon": "🚗",
        "ar": "السيارات",
        "en": "Cars",
        "active": 1,
        "services": [
            {"id": "car_wash", "icon": "🫧", "ar": "غسيل سيارات", "en": "Car wash", "active": 1},
            {"id": "mechanic", "icon": "🔩", "ar": "ميكانيكي", "en": "Mechanic", "active": 1},
        ],
    },
]

SEED_PROVIDERS = [
    {
        "id": "p1",
        "name": "سالم البلوشي",
        "phone": "91234567",
        "gov": "مسقط",
        "wilayah": "السيب",
        "areas": ["السيب", "بوشر"],
        "bio": "تنفيذ أعمال الكهرباء المنزلية والصيانة الطارئة بدقة وتنظيم.",
        "hours": "8:00 ص - 9:00 م",
        "status": "available",
        "active": 1,
        "verified": 1,
        "featured": 1,
        "package_id": "plus",
        "rating": 4.8,
        "reviews": 38,
        "services": [{"catId": "homecare", "serviceId": "electrician", "priceFrom": 5, "active": True, "areas": ["السيب", "بوشر"]}],
    },
    {
        "id": "p2",
        "name": "النخبة للتنظيف",
        "phone": "92345678",
        "gov": "مسقط",
        "wilayah": "بوشر",
        "areas": ["بوشر", "مطرح", "السيب"],
        "bio": "تنظيف منازل ومجالس وكنب بفرق منظمة ومواعيد واضحة.",
        "hours": "7:00 ص - 10:00 م",
        "status": "busy",
        "active": 1,
        "verified": 1,
        "featured": 1,
        "package_id": "growth",
        "rating": 4.7,
        "reviews": 51,
        "services": [{"catId": "cleaning", "serviceId": "home_clean", "priceFrom": 12, "active": True, "areas": ["بوشر", "السيب"]}],
    },
]


def ensure_column(con, table, column, definition):
    columns = [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_users(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, code_hash TEXT NOT NULL, role TEXT NOT NULL,
              permissions TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS categories(
              id TEXT PRIMARY KEY, icon TEXT, ar TEXT NOT NULL, en TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS services(
              id TEXT NOT NULL, category_id TEXT NOT NULL, icon TEXT, ar TEXT NOT NULL, en TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(id, category_id)
            );
            CREATE TABLE IF NOT EXISTS providers(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL, gov TEXT, wilayah TEXT,
              areas TEXT, bio TEXT, hours TEXT, status TEXT, active INTEGER, verified INTEGER, featured INTEGER,
              package_id TEXT, rating REAL, reviews INTEGER, admin_note TEXT DEFAULT '', image_path TEXT DEFAULT '',
              pin_hash TEXT DEFAULT '', services TEXT NOT NULL,
              stats TEXT NOT NULL DEFAULT '{"views":0,"whatsapp":0,"calls":0}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_requests(
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS leads(
              id TEXT PRIMARY KEY, provider_id TEXT, kind TEXT, customer_name TEXT, phone TEXT, note TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS finance(
              id TEXT PRIMARY KEY, kind TEXT, amount REAL, source TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS whatsapp_logs(
              id TEXT PRIMARY KEY, target TEXT, status TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        ensure_column(con, "providers", "image_path", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "pin_hash", "TEXT DEFAULT ''")
        if con.execute("SELECT COUNT(*) n FROM categories").fetchone()["n"] == 0:
            for c in SEED_CATEGORIES:
                con.execute("INSERT INTO categories VALUES(?,?,?,?,?)", (c["id"], c["icon"], c["ar"], c["en"], c["active"]))
                for s in c["services"]:
                    con.execute("INSERT INTO services VALUES(?,?,?,?,?,?)", (s["id"], c["id"], s["icon"], s["ar"], s["en"], s["active"]))
        if con.execute("SELECT COUNT(*) n FROM providers").fetchone()["n"] == 0:
            for p in SEED_PROVIDERS:
                con.execute(
                    """INSERT INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
                    package_id,rating,reviews,services,stats,pin_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p["id"], p["name"], p["phone"], p["gov"], p["wilayah"], jdump(p["areas"]), p["bio"], p["hours"],
                        p["status"], p["active"], p["verified"], p["featured"], p["package_id"], p["rating"], p["reviews"],
                        jdump(p["services"]), jdump({"views": 0, "whatsapp": 0, "calls": 0}),
                        hash_secret(default_provider_pin(p["phone"])),
                    ),
                )
        for r in con.execute("SELECT id, phone FROM providers WHERE COALESCE(pin_hash,'')=''"):
            con.execute("UPDATE providers SET pin_hash=? WHERE id=?", (hash_secret(default_provider_pin(r["phone"])), r["id"]))
        con.execute(
            "INSERT OR IGNORE INTO settings VALUES('platform', ?)",
            (jdump({"nameAr": "فوراً", "nameEn": "Fawran", "adminWhatsapp": "96890000000", "monthlyGoal": 500}),),
        )
        con.execute("INSERT OR IGNORE INTO settings VALUES('adminHash', ?)", (ADMIN_HASH,))
        if con.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"] == 0:
            con.execute(
                "INSERT INTO admin_users VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP)",
                ("admin_owner", "المالك", ADMIN_HASH, "owner", jdump(ALL_PERMISSIONS)),
            )


def image_url(path):
    return f"/{path.replace(os.sep, '/')}" if path else ""


def row_provider(r, private=False):
    d = dict(r)
    d["areas"] = jload(d["areas"], [])
    d["services"] = jload(d["services"], [])
    d["stats"] = jload(d["stats"], {"views": 0, "whatsapp": 0, "calls": 0})
    for k in ("active", "verified", "featured"):
        d[k] = bool(d[k])
    d["packageId"] = d.pop("package_id", "")
    d["adminNote"] = d.pop("admin_note", "")
    d["imagePath"] = d.pop("image_path", "")
    d["imageUrl"] = image_url(d["imagePath"])
    d["pinConfigured"] = bool(d.pop("pin_hash", ""))
    if not private:
        d.pop("adminNote", None)
    return d


def admin_public(r):
    d = dict(r)
    d.pop("code_hash", None)
    d["permissions"] = jload(d["permissions"], [])
    d["active"] = bool(d["active"])
    return d


def token_session(headers):
    token = headers.get("Authorization", "").replace("Bearer ", "")
    return TOKENS.get(token)


def permissions_for(role, selected=None):
    if selected:
        return [p for p in selected if p in ALL_PERMISSIONS]
    return ROLE_PERMISSIONS.get(role, [])


def has_permission(session, permission):
    if not session or session.get("kind") != "admin":
        return False
    return session.get("role") == "owner" or permission in session.get("permissions", [])


def get_bootstrap(session=None):
    with db() as con:
        categories = []
        for c in con.execute("SELECT * FROM categories ORDER BY rowid"):
            cd = dict(c)
            cd["active"] = bool(cd["active"])
            cd["services"] = [dict(s) | {"active": bool(s["active"])} for s in con.execute("SELECT id,icon,ar,en,active FROM services WHERE category_id=? ORDER BY rowid", (c["id"],))]
            categories.append(cd)
        providers = [row_provider(r, private=bool(session and session.get("kind") == "admin")) for r in con.execute("SELECT * FROM providers ORDER BY featured DESC, rating DESC")]
        requests = []
        if has_permission(session, "review_requests"):
            for r in con.execute("SELECT * FROM provider_requests ORDER BY created_at DESC"):
                payload = jload(r["payload"], {}) | {"createdAt": r["created_at"]}
                payload.pop("pinHash", None)
                requests.append(payload)
        settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
        stats = {
            "providers": len(providers),
            "activeProviders": len([p for p in providers if p["active"]]),
            "requests": con.execute("SELECT COUNT(*) n FROM provider_requests").fetchone()["n"],
            "leads": con.execute("SELECT COUNT(*) n FROM leads").fetchone()["n"],
            "revenue": con.execute("SELECT COALESCE(SUM(amount),0) n FROM finance WHERE kind='revenue'").fetchone()["n"],
            "whatsappLogs": con.execute("SELECT COUNT(*) n FROM whatsapp_logs").fetchone()["n"],
        }
        data = {
            "categories": categories,
            "providers": providers,
            "requests": requests,
            "settings": settings,
            "stats": stats,
            "integrations": {"whatsappConfigured": whatsapp_configured(), "postgresReady": True},
            "permissions": ALL_PERMISSIONS,
        }
        if session and session.get("kind") == "admin":
            data["adminUser"] = {k: session[k] for k in ("id", "name", "role", "permissions")}
            if has_permission(session, "manage_admins"):
                data["adminUsers"] = [admin_public(r) for r in con.execute("SELECT * FROM admin_users ORDER BY created_at")]
        return data


def get_classic_state():
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key='classicState'").fetchone()
        if not row:
            return None
        return jload(row["value"], None)


def save_classic_state(data):
    if not isinstance(data, dict):
        raise ValueError("state_must_be_object")
    data["serverSavedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('classicState', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (jdump(data),),
        )
    return data["serverSavedAt"]


def whatsapp_configured():
    return bool(os.environ.get("WHATSAPP_ACCESS_TOKEN") and os.environ.get("WHATSAPP_PHONE_NUMBER_ID"))


def log_whatsapp(target, status, detail):
    with db() as con:
        con.execute("INSERT INTO whatsapp_logs VALUES(?,?,?,?,CURRENT_TIMESTAMP)", (slug("wa"), target, status, detail[:900]))


def send_whatsapp(to, text):
    target = normalize_phone(to)
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    version = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
    if not token or not phone_id or not target:
        log_whatsapp(target or str(to), "skipped", "WhatsApp Cloud API environment variables are not configured")
        return {"ok": False, "configured": False}
    payload = {
        "messaging_product": "whatsapp",
        "to": target,
        "type": "text",
        "text": {"preview_url": False, "body": text[:3500]},
    }
    req = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{phone_id}/messages",
        data=jdump(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as res:
            body = res.read().decode("utf-8")
            log_whatsapp(target, "sent", body)
            return {"ok": True, "configured": True, "response": jload(body, {})}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        log_whatsapp(target, "failed", detail)
        return {"ok": False, "configured": True, "error": detail}
    except Exception as err:
        log_whatsapp(target, "failed", str(err))
        return {"ok": False, "configured": True, "error": str(err)}


def save_data_url(provider_id, image_data):
    if not image_data:
        return ""
    if not image_data.startswith("data:image/") or ";base64," not in image_data:
        raise ValueError("invalid_image")
    head, raw = image_data.split(";base64,", 1)
    mime = head.replace("data:", "")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime)
    if not ext:
        raise ValueError("unsupported_image_type")
    blob = base64.b64decode(raw, validate=True)
    if len(blob) > 2_500_000:
        raise ValueError("image_too_large")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{provider_id}.{ext}"
    rel = f"uploads/{filename}"
    (UPLOAD_DIR / filename).write_bytes(blob)
    return rel


def upsert_provider(con, data):
    p = data | {"id": data.get("id") or slug("p")}
    existing = con.execute("SELECT * FROM providers WHERE id=?", (p["id"],)).fetchone()
    existing_provider = row_provider(existing, private=True) if existing else {}
    image_path = data.get("imagePath") or ""
    if data.get("imageData"):
        image_path = save_data_url(p["id"], data["imageData"])
    elif not image_path:
        image_path = existing_provider.get("imagePath", "")
    pin_hash = data.get("pinHash") or ""
    if data.get("pin"):
        pin_hash = hash_secret(data["pin"])
    if not pin_hash:
        existing_hash = existing["pin_hash"] if existing else ""
        pin_hash = existing_hash or hash_secret(default_provider_pin(p.get("phone")))
    con.execute(
        """INSERT INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
        package_id,rating,reviews,admin_note,image_path,pin_hash,services,stats) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,phone=excluded.phone,gov=excluded.gov,
        wilayah=excluded.wilayah,areas=excluded.areas,bio=excluded.bio,hours=excluded.hours,status=excluded.status,
        active=excluded.active,verified=excluded.verified,featured=excluded.featured,package_id=excluded.package_id,
        rating=excluded.rating,reviews=excluded.reviews,admin_note=excluded.admin_note,image_path=excluded.image_path,
        pin_hash=excluded.pin_hash,services=excluded.services""",
        (
            p["id"], p.get("name", ""), p.get("phone", ""), p.get("gov", ""), p.get("wilayah", ""),
            jdump(p.get("areas", [])), p.get("bio", ""), p.get("hours", ""), p.get("status", "available"),
            int(bool(p.get("active", True))), int(bool(p.get("verified", False))), int(bool(p.get("featured", False))),
            p.get("packageId", existing_provider.get("packageId", "intro")),
            float(p.get("rating", existing_provider.get("rating", 0)) or 0),
            int(p.get("reviews", existing_provider.get("reviews", 0)) or 0),
            p.get("adminNote", ""), image_path, pin_hash, jdump(p.get("services", [])),
            jdump(p.get("stats", existing_provider.get("stats", {"views": 0, "whatsapp": 0, "calls": 0}))),
        ),
    )
    p["imagePath"] = image_path
    return p


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data, status=200):
        raw = jdump(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def session(self):
        return token_session(self.headers)

    def require_admin(self, permission="view_reports"):
        session = self.session()
        if not has_permission(session, permission):
            self.send_json({"error": "permission_denied", "permission": permission}, 403)
            return None
        return session

    def require_provider(self):
        session = self.session()
        if not session or session.get("kind") != "provider":
            self.send_json({"error": "provider_auth_required"}, 401)
            return None
        return session

    def send_upload(self, path):
        filename = path.removeprefix("/uploads/")
        if not filename or "/" in filename or "\\" in filename:
            return self.send_error(404)
        target = (UPLOAD_DIR / filename).resolve()
        try:
            target.relative_to(UPLOAD_DIR.resolve())
        except ValueError:
            return self.send_error(404)
        if not target.is_file():
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as f:
            self.copyfile(f, self.wfile)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/uploads/"):
            return self.send_upload(path)
        if path == "/api/classic-state":
            state = get_classic_state()
            return self.send_json({"ok": True, "state": state})
        if path == "/api/bootstrap":
            return self.send_json(get_bootstrap(self.session()))
        if path == "/api/admin/session":
            session = self.require_admin()
            if not session:
                return
            return self.send_json(get_bootstrap(session))
        if path == "/api/provider/me":
            session = self.require_provider()
            if not session:
                return
            with db() as con:
                row = con.execute("SELECT * FROM providers WHERE id=?", (session["providerId"],)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                return self.send_json({"provider": row_provider(row, private=True)})
        if path == "/api/backup":
            session = self.require_admin("backup")
            if not session:
                return
            return self.send_json(get_bootstrap(session))
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        data = self.read_json()
        if path == "/api/classic-state":
            try:
                saved_at = save_classic_state(data.get("state", data))
                return self.send_json({"ok": True, "savedAt": saved_at})
            except ValueError as err:
                return self.send_json({"error": str(err)}, 400)
        if path == "/api/admin/login":
            code_hash = hash_secret(data.get("code", ""))
            with db() as con:
                row = con.execute("SELECT * FROM admin_users WHERE code_hash=? AND active=1", (code_hash,)).fetchone()
            if not row:
                return self.send_json({"error": "invalid_code"}, 403)
            user = admin_public(row)
            token = secrets.token_urlsafe(24)
            TOKENS[token] = {"kind": "admin", **user}
            return self.send_json({"token": token, "user": user})
        if path == "/api/provider/login":
            phone = normalize_phone(data.get("phone", ""))
            pin_hash = hash_secret(data.get("pin", ""))
            with db() as con:
                row = con.execute("SELECT * FROM providers WHERE (phone=? OR phone=?) AND pin_hash=?", (phone, phone.replace("968", "", 1), pin_hash)).fetchone()
            if not row:
                return self.send_json({"error": "invalid_provider_login"}, 403)
            provider = row_provider(row, private=True)
            token = secrets.token_urlsafe(24)
            TOKENS[token] = {"kind": "provider", "providerId": provider["id"], "name": provider["name"]}
            return self.send_json({"token": token, "provider": provider})
        if path == "/api/provider-requests":
            pin = str(data.get("pin", "")).strip()
            item = {
                "id": slug("req"),
                "name": data.get("name", "").strip(),
                "phone": data.get("phone", "").strip(),
                "gov": data.get("gov", "مسقط"),
                "wilayah": data.get("wilayah", ""),
                "service": data.get("service", ""),
                "note": data.get("note", ""),
                "pinHash": hash_secret(pin) if len(pin) >= 4 else "",
            }
            if not item["name"] or not item["phone"] or not item["pinHash"]:
                return self.send_json({"error": "name_phone_pin_required"}, 400)
            with db() as con:
                con.execute("INSERT INTO provider_requests(id,payload) VALUES(?,?)", (item["id"], jdump(item)))
                settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
            send_whatsapp(settings.get("adminWhatsapp"), f"طلب مزود جديد في فوراً: {item['name']} - {item['phone']} - {item['service']}")
            safe_item = item.copy()
            safe_item.pop("pinHash", None)
            return self.send_json({"ok": True, "request": safe_item}, 201)
        if path == "/api/leads":
            return self.save_lead(data)
        if path.startswith("/api/provider/"):
            return self.provider_post(path, data)
        if path.startswith("/api/admin/"):
            return self.admin_post(path, data)
        self.send_json({"error": "not_found"}, 404)

    def save_lead(self, data):
        item = {
            "id": slug("lead"),
            "provider_id": data.get("providerId"),
            "kind": data.get("kind", "whatsapp"),
            "customer_name": data.get("customerName", ""),
            "phone": data.get("phone", ""),
            "note": data.get("note", ""),
        }
        with db() as con:
            con.execute("INSERT INTO leads VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)", tuple(item.values()))
            provider = None
            if item["kind"] in ("views", "whatsapp", "calls") and item["provider_id"]:
                r = con.execute("SELECT * FROM providers WHERE id=?", (item["provider_id"],)).fetchone()
                if r:
                    provider = row_provider(r, private=True)
                    stats = provider["stats"]
                    stats[item["kind"]] = int(stats.get(item["kind"], 0)) + 1
                    con.execute("UPDATE providers SET stats=? WHERE id=?", (jdump(stats), item["provider_id"]))
        if data.get("notifyProvider") and provider:
            send_whatsapp(provider["phone"], f"تنبيه من فوراً: لديك تواصل جديد. {item['note']}".strip())
        return self.send_json({"ok": True}, 201)

    def provider_post(self, path, data):
        session = self.require_provider()
        if not session:
            return
        with db() as con:
            row = con.execute("SELECT * FROM providers WHERE id=?", (session["providerId"],)).fetchone()
            if not row:
                return self.send_json({"error": "not_found"}, 404)
            provider = row_provider(row, private=True)
            if path == "/api/provider/profile":
                provider.update({
                    "name": data.get("name", provider["name"]),
                    "phone": data.get("phone", provider["phone"]),
                    "gov": data.get("gov", provider["gov"]),
                    "wilayah": data.get("wilayah", provider["wilayah"]),
                    "areas": data.get("areas", provider["areas"]),
                    "bio": data.get("bio", provider["bio"]),
                    "hours": data.get("hours", provider["hours"]),
                    "status": data.get("status", provider["status"]),
                    "services": data.get("services", provider["services"]),
                    "active": provider["active"],
                    "verified": provider["verified"],
                    "featured": provider["featured"],
                })
                upsert_provider(con, provider)
                return self.send_json({"ok": True})
            if path == "/api/provider/image":
                image_path = save_data_url(provider["id"], data.get("imageData", ""))
                con.execute("UPDATE providers SET image_path=? WHERE id=?", (image_path, provider["id"]))
                return self.send_json({"ok": True, "imageUrl": image_url(image_path)})
            if path == "/api/provider/pin":
                if len(str(data.get("pin", ""))) < 4:
                    return self.send_json({"error": "pin_too_short"}, 400)
                con.execute("UPDATE providers SET pin_hash=? WHERE id=?", (hash_secret(data["pin"]), provider["id"]))
                return self.send_json({"ok": True})
        return self.send_json({"error": "not_found"}, 404)

    def admin_post(self, path, data):
        permission = {
            "/api/admin/providers": "manage_providers",
            "/api/admin/provider-status": "manage_providers",
            "/api/admin/request-decision": "review_requests",
            "/api/admin/settings": "manage_settings",
            "/api/admin/users": "manage_admins",
            "/api/admin/test-whatsapp": "manage_settings",
        }.get(path, "view_reports")
        session = self.require_admin(permission)
        if not session:
            return
        with db() as con:
            if path == "/api/admin/providers":
                p = upsert_provider(con, data)
                return self.send_json({"ok": True, "provider": p})
            if path == "/api/admin/provider-status":
                con.execute(
                    "UPDATE providers SET active=?, verified=?, featured=?, status=? WHERE id=?",
                    (int(data.get("active", 1)), int(data.get("verified", 0)), int(data.get("featured", 0)), data.get("status", "available"), data.get("id")),
                )
                return self.send_json({"ok": True})
            if path == "/api/admin/request-decision":
                row = con.execute("SELECT payload FROM provider_requests WHERE id=?", (data.get("id"),)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                payload = jload(row["payload"], {})
                con.execute("DELETE FROM provider_requests WHERE id=?", (data.get("id"),))
                if data.get("decision") == "accept":
                    provider = {
                        "id": slug("p"),
                        "name": payload.get("name", ""),
                        "phone": payload.get("phone", ""),
                        "gov": payload.get("gov", ""),
                        "wilayah": payload.get("wilayah", ""),
                        "areas": [payload.get("wilayah", "")],
                        "bio": payload.get("note", ""),
                        "hours": "",
                        "status": "available",
                        "active": True,
                        "verified": False,
                        "featured": False,
                        "packageId": "intro",
                        "rating": 0,
                        "reviews": 0,
                        "services": [],
                        "stats": {"views": 0, "whatsapp": 0, "calls": 0},
                        "adminNote": "تم قبوله من الطلبات",
                        "pinHash": payload.get("pinHash") or hash_secret(default_provider_pin(payload.get("phone", ""))),
                    }
                    upsert_provider(con, provider)
                    send_whatsapp(provider["phone"], "تم قبول حسابك كمزود في فوراً. يمكنك الدخول من بوابة المزودين.")
                return self.send_json({"ok": True})
            if path == "/api/admin/settings":
                con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(data),))
                return self.send_json({"ok": True})
            if path == "/api/admin/users":
                role = data.get("role", "support")
                perms = permissions_for(role, data.get("permissions"))
                user_id = data.get("id") or slug("admin")
                existing = con.execute("SELECT code_hash FROM admin_users WHERE id=?", (user_id,)).fetchone()
                code_hash = existing["code_hash"] if existing else ""
                if data.get("code"):
                    code_hash = hash_secret(data["code"])
                if not code_hash:
                    return self.send_json({"error": "code_required"}, 400)
                con.execute(
                    """INSERT INTO admin_users(id,name,code_hash,role,permissions,active) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,code_hash=excluded.code_hash,
                    role=excluded.role,permissions=excluded.permissions,active=excluded.active""",
                    (user_id, data.get("name", "مشرف"), code_hash, role, jdump(perms), int(bool(data.get("active", True)))),
                )
                return self.send_json({"ok": True})
            if path == "/api/admin/test-whatsapp":
                return self.send_json(send_whatsapp(data.get("to"), data.get("message", "اختبار من منصة فوراً")))
        self.send_json({"error": "not_found"}, 404)


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"Fawran platform running: http://{display_host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
