from pathlib import Path
import json
import os
import sqlite3

BASE_DIR = Path(__file__).resolve().parent
_legacy_db = BASE_DIR / "foran.sqlite3"
DB_PATH = Path(os.environ.get("KHADAMATI_DB_PATH") or os.environ.get("FORAN_DB_PATH") or (_legacy_db if _legacy_db.exists() else BASE_DIR / "khadamati.sqlite3"))
OUT_PATH = BASE_DIR / "khadamati-export.json"


def rows(con, table):
    return [dict(r) for r in con.execute(f"SELECT * FROM {table}")]


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    data = {
        "settings": rows(con, "settings"),
        "admin_users": rows(con, "admin_users"),
        "categories": rows(con, "categories"),
        "services": rows(con, "services"),
        "providers": rows(con, "providers"),
        "provider_requests": rows(con, "provider_requests"),
        "leads": rows(con, "leads"),
        "finance": rows(con, "finance"),
        "whatsapp_logs": rows(con, "whatsapp_logs"),
        "reviews": rows(con, "reviews"),
        "complaints": rows(con, "complaints"),
        "packages": rows(con, "packages"),
        "subscriptions": rows(con, "subscriptions"),
        "payments": rows(con, "payments"),
        "audit_logs": rows(con, "audit_logs"),
    }
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_PATH)


if __name__ == "__main__":
    main()
