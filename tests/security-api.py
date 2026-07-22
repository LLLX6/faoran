"""Isolated abuse-case checks for the Khadamati HTTP API."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_CODE = "Audit-Admin-4829"
TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9W"
    "lqAAAAAASUVORK5CYII="
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http(base: str, path: str, payload=None, token: str = "", *, origin="http://127.0.0.1:8080"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json", "Origin": origin}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base}{path}", data=body, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            data = json.loads(raw.decode("utf-8")) if "json" in response.headers.get("Content-Type", "") else raw
            return response.status, data, response.headers
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = raw
        return error.code, data, error.headers


def expect(result, statuses, label):
    status, data, _ = result
    assert status in statuses, f"{label}: HTTP {status} {data}"
    return data


def register_provider(base: str, admin_token: str, suffix: str):
    phone = f"96895550{suffix}"
    pin = f"7{suffix}9"
    registration = expect(
        http(
            base,
            "/api/provider-requests",
            {
                "name": f"مزود أمني {suffix}",
                "phone": phone,
                "pin": pin,
                "providerType": "individual",
                "commercialNo": f"SEC-{suffix}",
                "businessRole": "كهربائي منازل",
                "gov": "مسقط",
                "wilayah": "السيب",
                "location": {"lat": 23.621234, "lng": 58.221234},
                "service": "homecare|electrician",
                "services": [
                    {
                        "catId": "homecare",
                        "serviceId": "electrician",
                        "priceFrom": 8,
                        "areas": ["السيب"],
                    }
                ],
                "priceFrom": 8,
                "note": "خدمة كهرباء منزلية دقيقة وموثوقة",
                "hours": "الأحد: 8:00 ص - 8:00 م",
                "documentsData": [TEST_PNG],
            },
        ),
        {201},
        "provider registration",
    )
    expect(
        http(
            base,
            "/api/admin/request-decision",
            {"id": registration["request"]["id"], "decision": "accept"},
            admin_token,
        ),
        {200},
        "provider approval",
    )
    admin_state = expect(http(base, "/api/admin/session", token=admin_token), {200}, "admin state")
    provider = next(item for item in admin_state["providers"] if item.get("phone") == phone)
    login = expect(http(base, "/api/provider/login", {"phone": phone, "pin": pin}), {200}, "provider login")
    return provider, login["token"], phone, pin


def run():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="khadamati-security-") as temp:
        env = os.environ.copy()
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "KHADAMATI_ENV": "test",
                "KHADAMATI_ADMIN_CODE": ADMIN_CODE,
                "KHADAMATI_DB_PATH": str(Path(temp) / "audit.sqlite3"),
                "KHADAMATI_UPLOAD_DIR": str(Path(temp) / "uploads"),
                "KHADAMATI_BACKUP_DIR": str(Path(temp) / "backups"),
                "KHADAMATI_MEDIA_SIGNING_KEY": "audit-media-signing-key-4829",
                "KHADAMATI_LOGIN_MAX_ATTEMPTS": "3",
                "KHADAMATI_LOGIN_LOCK_MINUTES": "15",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "server.py"], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
                try:
                    if http(base, "/api/bootstrap")[0] == 200:
                        break
                except urllib.error.URLError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("isolated server did not start")

            public = expect(http(base, "/api/bootstrap"), {200}, "public bootstrap")
            assert not public.get("reports") and not public.get("permissions")
            assert not public.get("customerRequests") and not public.get("notifications")
            assert "adminCode" not in public.get("settings", {})
            assert all(not p.get("phone") and not p.get("documents") for p in public.get("providers", []))
            assert all(not r.get("phone") and not r.get("userId") for r in public.get("reviews", []))
            assert all(not item.get("note") and not item.get("images") for item in public.get("marketplaceRequests", []))

            evil_origin = http(base, "/api/bootstrap", origin="https://evil.example")[2]
            assert not evil_origin.get("Access-Control-Allow-Origin"), "untrusted CORS origin was reflected"

            admin = expect(http(base, "/api/admin/login", {"code": ADMIN_CODE}), {200}, "admin login")
            admin_token = admin["token"]
            provider_a, token_a, _, _ = register_provider(base, admin_token, "091")
            provider_b, token_b, _, _ = register_provider(base, admin_token, "092")

            public = expect(http(base, "/api/bootstrap"), {200}, "public provider coordinates")
            public_a = next(item for item in public["providers"] if item["id"] == provider_a["id"])
            assert public_a.get("mapVisible") is True
            assert public_a.get("location", {}).get("lat") == 23.621234
            assert public_a.get("location", {}).get("lng") == 58.221234
            expect(
                http(base, "/api/provider/profile", {"mapVisible": False}, token_a),
                {200},
                "provider map privacy opt-out",
            )
            hidden_public = expect(http(base, "/api/bootstrap"), {200}, "hidden provider coordinates")
            hidden_a = next(item for item in hidden_public["providers"] if item["id"] == provider_a["id"])
            assert hidden_a.get("mapVisible") is False and not hidden_a.get("location")

            private_a = expect(http(base, "/api/provider/me", token=token_a), {200}, "provider private profile")["provider"]
            signed_document = private_a["documents"][0]
            parsed = urllib.parse.urlsplit(signed_document)
            assert parsed.path.startswith("/media/") and parsed.query
            expect(http(base, parsed.path), {403}, "unsigned private document")
            assert http(base, parsed.path + "?" + parsed.query)[0] == 200
            raw_document = "/uploads/" + parsed.path.rsplit("/", 1)[-1]
            expect(http(base, raw_document), {403}, "anonymous raw private document")
            expect(http(base, raw_document, token=token_a), {403}, "provider raw private document")
            assert http(base, raw_document, token=admin_token)[0] == 200
            expired_query = urllib.parse.parse_qs(parsed.query)
            expired_query["exp"] = ["1"]
            expect(http(base, parsed.path + "?" + urllib.parse.urlencode(expired_query, doseq=True)), {403}, "expired media link")

            member_id = "security-manager-a"
            expect(
                http(
                    base,
                    "/api/admin/team",
                    {
                        "id": member_id,
                        "providerId": provider_a["id"],
                        "name": "مدير أمني",
                        "phone": "96894440001",
                        "role": "provider_manager",
                        "pin": "7744",
                        "permissions": ["profile", "requests"],
                        "active": True,
                    },
                    admin_token,
                ),
                {200},
                "admin team seed",
            )
            expect(
                http(base, "/api/provider/team", {"id": member_id, "action": "delete"}, token_b),
                {404},
                "cross-provider team IDOR",
            )
            manager = expect(
                http(base, "/api/provider/login", {"phone": "96894440001", "pin": "7744"}),
                {200},
                "manager login",
            )
            expect(
                http(base, "/api/provider/pin", {"currentPin": "7744", "pin": "8899"}, manager["token"]),
                {403},
                "manager owner-PIN change",
            )

            branch_id = "security-branch-a"
            expect(
                http(
                    base,
                    "/api/admin/branches",
                    {
                        "id": branch_id,
                        "providerId": provider_a["id"],
                        "name": "فرع الاختبار",
                        "gov": "مسقط",
                        "wilayah": "السيب",
                        "location": {"lat": 23.62, "lng": 58.22},
                        "active": True,
                    },
                    admin_token,
                ),
                {200},
                "admin branch seed",
            )
            expect(
                http(base, "/api/provider/branches", {"id": branch_id, "action": "delete"}, token_b),
                {404},
                "cross-provider branch IDOR",
            )

            user = expect(
                http(base, "/api/users/login", {"phone": "96896660001", "name": "مستخدم أمني", "pin": "4268"}),
                {200},
                "user login",
            )
            user_token = user["token"]
            expect(
                http(
                    base,
                    "/api/user/profile",
                    {"name": "مستخدم أمني", "location": {"lat": 23.6, "lng": 58.2}, "avatarData": "data:image/png;base64,aGVsbG8="},
                    user_token,
                ),
                {400},
                "spoofed image upload",
            )
            profile = expect(
                http(
                    base,
                    "/api/user/profile",
                    {"name": "مستخدم أمني", "location": {"lat": 23.6, "lng": 58.2}, "avatarData": TEST_PNG},
                    user_token,
                ),
                {200},
                "valid avatar upload",
            )
            assert "/media/" in profile["user"]["avatar"] and "sig=" in profile["user"]["avatar"]
            expect(http(base, "/api/auth/logout", {}, user_token), {200}, "logout")
            expect(
                http(base, "/api/user/profile", {"name": "revoked"}, user_token),
                {401},
                "revoked user session",
            )

            lock_phone = "96896660002"
            expect(http(base, "/api/users/login", {"phone": lock_phone, "name": "قفل", "pin": "1357"}), {200}, "lock user seed")
            for _ in range(3):
                expect(http(base, "/api/users/login", {"phone": lock_phone, "pin": "9999"}), {403}, "wrong PIN")
            expect(http(base, "/api/users/login", {"phone": lock_phone, "pin": "1357"}), {429}, "locked login")

            expect(
                http(
                    base,
                    "/api/admin/provider-status",
                    {"id": provider_a["id"], "active": False, "verified": True, "featured": False, "status": "unavailable"},
                    admin_token,
                ),
                {200},
                "provider deactivation",
            )
            expect(http(base, "/api/provider/me", token=token_a), {401}, "inactive provider session")

            oversized = b'{"value":"' + (b"a" * 1_100_000) + b'"}'
            request = urllib.request.Request(
                f"{base}/api/admin/login",
                data=oversized,
                headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8080"},
                method="POST",
            )
            try:
                urllib.request.urlopen(request, timeout=20)
                raise AssertionError("oversized JSON body was accepted")
            except urllib.error.HTTPError as error:
                assert error.code == 413, f"oversized body returned HTTP {error.code}"

            sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
            assert "khadamati-app-shell-v52-release" in sw
            assert "api|media|uploads" in sw and "cache: 'no-store'" in sw

            return {
                "public_data_minimization": True,
                "cors_allowlist": True,
                "signed_private_media": True,
                "team_and_branch_idor": True,
                "provider_owner_pin_protection": True,
                "mime_signature_validation": True,
                "session_revocation": True,
                "login_lockout": True,
                "inactive_account_revocation": True,
                "request_size_limit": True,
                "service_worker_private_cache_block": True,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **run()}, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f"Security test failed: {error}", file=sys.stderr)
        raise
