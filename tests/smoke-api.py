import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = os.environ.get("KHADAMATI_TEST_URL", "http://127.0.0.1:8080").rstrip("/")


def request(path, payload=None, token=""):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw or "{}")


def main():
    status, bootstrap = request("/api/bootstrap")
    assert status == 200 and bootstrap.get("categories"), "Bootstrap endpoint failed"

    status, provider = request("/api/provider/login", {"phone": "91234567", "pin": "1234"})
    assert status == 200 and provider.get("token"), "Provider login failed"

    status, admin = request("/api/admin/login", {"code": "0000"})
    assert status == 200 and admin.get("token"), "Admin login failed"

    status, invalid_user = request("/api/users/login", {"phone": "12", "name": "test"})
    assert status >= 400 and invalid_user.get("error"), "User validation did not reject an invalid phone"

    status, user = request(
        "/api/users/login",
        {"phone": "95550009", "name": "مستخدم اختبار التعاون", "pin": "2468", "gov": "مسقط", "wilayah": "السيب"},
    )
    assert status == 200 and user.get("token"), "Collaboration test user login failed"
    user_token = user["token"]
    provider_token = provider["token"]

    status, created = request(
        "/api/user/requests",
        {
            "serviceValue": "homecare|electrician",
            "serviceName": "كهربائي",
            "customerName": "مستخدم اختبار التعاون",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.62, "lng": 58.22},
            "urgency": "normal",
            "scheduleType": "specific",
            "requestedAt": "2026-07-10T09:00",
            "note": "اختبار العرض والمحادثة والتتبع",
        },
        user_token,
    )
    assert status in (200, 201) and created.get("request", {}).get("id"), "Request creation failed"
    request_id = created["request"]["id"]

    status, waitlisted = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "waitlist", "enabled": True},
        user_token,
    )
    assert status == 200 and waitlisted["request"]["waitlisted"], "Waitlist update failed"

    status, offered = request(
        "/api/request/collaboration",
        {
            "id": request_id,
            "action": "offer",
            "price": 12,
            "duration": "خلال ساعتين",
            "note": "يشمل المعاينة والتنفيذ",
        },
        provider_token,
    )
    offers = offered.get("request", {}).get("offers", [])
    assert status == 200 and len(offers) == 1, "Provider offer failed"

    status, selected = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "choose_offer", "offerId": offers[0]["id"]},
        user_token,
    )
    assert status == 200 and selected["request"]["acceptedProviderId"] == provider["provider"]["id"], "Offer selection failed"

    status, chatted = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "message", "text": "تم تأكيد الموعد"},
        user_token,
    )
    assert status == 200 and chatted["request"]["messages"], "Request chat failed"

    status, tracked = request(
        "/api/request/collaboration",
        {
            "id": request_id,
            "action": "arrival",
            "status": "onTheWay",
            "location": {"lat": 23.61, "lng": 58.24, "accuracy": 8},
            "etaMinutes": 14,
        },
        provider_token,
    )
    assert status == 200 and tracked["request"]["arrival"]["etaMinutes"] == 14, "Arrival tracking failed"

    request("/api/user/requests", {"id": request_id, "action": "cancel"}, user_token)

    print(json.dumps({
        "ok": True,
        "bootstrap": True,
        "provider_login": True,
        "admin_login": True,
        "user_validation": True,
        "offer_comparison": True,
        "request_chat": True,
        "arrival_tracking": True,
        "waitlist": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        raise
