import json
import os
import sys
import urllib.error
import urllib.request


BASE_URL = os.environ.get("KHADAMATI_TEST_URL", "http://127.0.0.1:8080").rstrip("/")
ADMIN_CODE = os.environ.get("KHADAMATI_TEST_ADMIN_CODE", "")
TEST_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9W"
    "lqAAAAAASUVORK5CYII="
)


def request(path, payload=None, token=""):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8080"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        return error.code, json.loads(raw or "{}")


def expect(status, data, expected, message):
    assert status in expected, f"{message}: HTTP {status} {data}"
    return data


def main():
    assert ADMIN_CODE, "Set KHADAMATI_TEST_ADMIN_CODE for the isolated test server."

    status, public = request("/api/bootstrap")
    expect(status, public, {200}, "Public bootstrap failed")
    assert public.get("categories"), "Public categories are missing"
    for provider in public.get("providers", []):
        assert not provider.get("phone"), "Public provider phone leaked"
        assert not provider.get("documents"), "Public provider documents leaked"
        assert not provider.get("commercialNo"), "Public provider registration leaked"
    assert not public.get("notifications") and not public.get("customerRequests"), "Visitor received private session data"

    status, admin = request("/api/admin/login", {"code": ADMIN_CODE})
    expect(status, admin, {200}, "Admin login failed")
    admin_token = admin["token"]

    provider_phone = "96895550991"
    provider_pin = "7319"
    status, same_phone_user = request(
        "/api/users/login",
        {"phone": provider_phone, "name": "مستخدم ومزود", "pin": "2468"},
    )
    expect(status, same_phone_user, {200}, "User account with provider phone failed")
    status, registration = request(
        "/api/provider-requests",
        {
            "name": "مزود اختبار الإنتاج",
            "phone": provider_phone,
            "pin": provider_pin,
            "providerType": "individual",
            "commercialNo": "TEST-LIC-991",
            "businessRole": "كهربائي منازل",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.62, "lng": 58.22},
            "service": "homecare|electrician",
            "services": [
                {"catId": "homecare", "serviceId": "electrician", "priceFrom": 8, "areas": ["السيب"]},
                {"catId": "homecare", "serviceId": "locks", "priceFrom": 6, "areas": ["السيب"]},
            ],
            "priceFrom": 8,
            "note": "خدمة كهرباء منزلية دقيقة وموثوقة",
            "hours": "الأحد، الاثنين: 8:00 ص - 8:00 م",
            "documentsData": [TEST_PNG],
        },
    )
    expect(status, registration, {201}, "Provider registration failed")
    registration_id = registration["request"]["id"]
    assert registration["request"]["bio"] == "خدمة كهرباء منزلية دقيقة وموثوقة"

    status, pending_login = request(
        "/api/provider/login", {"phone": provider_phone, "pin": provider_pin}
    )
    expect(status, pending_login, {200}, "Pending provider login failed")
    assert pending_login.get("pending") is True
    assert pending_login["request"]["id"] == registration_id
    pending_token = pending_login["token"]

    status, pending_state = request("/api/bootstrap", token=pending_token)
    expect(status, pending_state, {200}, "Pending provider state failed")
    assert len(pending_state.get("requests", [])) == 1
    assert pending_state["requests"][0]["bio"] == "خدمة كهرباء منزلية دقيقة وموثوقة"

    status, decision = request(
        "/api/admin/request-decision",
        {"id": registration_id, "decision": "accept"},
        admin_token,
    )
    expect(status, decision, {200}, "Provider approval failed")
    assert decision.get("provider", {}).get("phone") == provider_phone

    status, expired_pending_state = request("/api/bootstrap", token=pending_token)
    expect(status, expired_pending_state, {200}, "Expired pending session fallback failed")
    assert not expired_pending_state.get("requests"), "Approved request remained in pending state"
    assert expired_pending_state.get("currentProvider", {}).get("phone") == provider_phone

    status, admin_state = request("/api/admin/session", token=admin_token)
    expect(status, admin_state, {200}, "Admin state failed")
    provider = next(item for item in admin_state["providers"] if item.get("phone") == provider_phone)
    provider_id = provider["id"]

    status, pending_subscription = request(
        "/api/admin/subscriptions",
        {
            "action": "request",
            "providerId": provider_id,
            "packageId": "basic_6m",
        },
        admin_token,
    )
    expect(status, pending_subscription, {200}, "Pending subscription request failed")
    pending_id = pending_subscription["subscription"]["subscriptionId"]
    assert pending_subscription["subscription"]["status"] == "pending_payment"

    status, tampered_payment = request(
        "/api/admin/payments",
        {
            "action": "record",
            "subscriptionId": pending_id,
            "amount": 5,
            "method": "bank",
        },
        admin_token,
    )
    expect(status, tampered_payment, {409}, "Tampered subscription amount was accepted")

    status, recorded_payment = request(
        "/api/admin/payments",
        {
            "action": "record",
            "subscriptionId": pending_id,
            "amount": 6,
            "method": "bank",
            "note": "Verified smoke-test transfer",
        },
        admin_token,
    )
    expect(status, recorded_payment, {200}, "Manual payment recording failed")
    assert recorded_payment["payment"]["status"] == "paid"

    status, subscription = request(
        "/api/admin/subscriptions",
        {
            "action": "request",
            "providerId": provider_id,
            "packageId": "professional_12m",
            "approveWithoutPayment": True,
        },
        admin_token,
    )
    expect(status, subscription, {200}, "Professional subscription activation failed")

    status, provider_login = request(
        "/api/provider/login", {"phone": provider_phone[-8:], "pin": provider_pin}
    )
    expect(status, provider_login, {200}, "Provider login with a local 8-digit phone failed")
    provider_token = provider_login["token"]

    status, formatted_provider_login = request(
        "/api/provider/login", {"phone": "+968 9555 0991", "pin": provider_pin}
    )
    expect(status, formatted_provider_login, {200}, "Provider login with a formatted phone failed")

    status, unauthenticated_lead = request(
        "/api/leads",
        {"kind": "request", "providerId": provider_id, "phone": "96899999999"},
    )
    expect(status, unauthenticated_lead, {401}, "Legacy lead endpoint accepted an anonymous request")

    status, provider_quote = request(
        "/api/leads",
        {
            "kind": "quote",
            "providerId": "forged-provider",
            "customerName": "عميل",
            "phone": "96899999999",
            "serviceValue": "homecare|electrician",
            "note": "عرض آمن داخل التطبيق",
        },
        provider_token,
    )
    expect(status, provider_quote, {201}, "Authenticated provider quote failed")
    assert provider_quote["lead"]["provider_id"] == provider_id, "Provider identity was not session-bound"
    assert "phone" not in provider_quote["lead"], "Provider quote response exposed a customer phone"

    status, user = request(
        "/api/users/login",
        {
            "phone": "96895550992",
            "name": "مستخدم اختبار الإنتاج",
            "pin": "2468",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.621, "lng": 58.221},
        },
    )
    expect(status, user, {200}, "User login failed")
    user_token = user["token"]

    status, created = request(
        "/api/user/requests",
        {
            "serviceValue": "homecare|electrician",
            "serviceName": "كهربائي",
            "customerName": "مستخدم اختبار الإنتاج",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.621, "lng": 58.221},
            "urgency": "normal",
            "scheduleType": "specific",
            "requestedAt": "2026-07-20T09:00",
            "note": "فحص انقطاع الكهرباء في المنزل",
        },
        user_token,
    )
    expect(status, created, {201}, "Request creation failed")
    request_id = created["request"]["id"]
    assert created.get("matchedProviders", 0) >= 1, "Exact matching returned no providers"

    status, owner_state = request("/api/bootstrap", token=user_token)
    expect(status, owner_state, {200}, "Request owner bootstrap failed")
    owner_request = next(
        (item for item in owner_state["customerRequests"] if item["id"] == request_id),
        None,
    )
    assert owner_request, "Created request is missing from the owner's active requests"

    status, visitor_state = request("/api/bootstrap")
    expect(status, visitor_state, {200}, "Visitor marketplace bootstrap failed")
    visitor_request = next(
        (item for item in visitor_state.get("marketplaceRequests", []) if item["id"] == request_id),
        None,
    )
    assert visitor_request, "Created request is missing from the request marketplace"
    assert not any(
        key in visitor_request for key in ("phone", "userId", "location", "latitude", "longitude")
    ), "Marketplace request exposed private requester data"

    status, recommender = request(
        "/api/users/login",
        {
            "phone": "96895550993",
            "name": "مستخدم مرشح للمزود",
            "pin": "3579",
            "gov": "مسقط",
            "wilayah": "السيب",
            "location": {"lat": 23.623, "lng": 58.223},
        },
    )
    expect(status, recommender, {200}, "Recommender login failed")
    recommender_token = recommender["token"]

    status, recommender_state = request("/api/bootstrap", token=recommender_token)
    expect(status, recommender_state, {200}, "Recommender bootstrap failed")
    marketplace_request = next(
        (item for item in recommender_state.get("marketplaceRequests", []) if item["id"] == request_id),
        None,
    )
    assert marketplace_request, "Another user cannot see the published request"

    status, owner_candidates = request(
        "/api/request-suggestions", {"action": "candidates", "requestId": request_id}, user_token
    )
    assert status == 403 and owner_candidates.get("error") == "request_owner_cannot_suggest", (
        "The request owner was allowed to recommend a provider to themselves"
    )

    status, provider_candidates = request(
        "/api/request-suggestions", {"action": "candidates", "requestId": request_id}, provider_token
    )
    assert status in {401, 403}, "A provider account was allowed to recommend another provider"

    status, candidates = request(
        "/api/request-suggestions",
        {"action": "candidates", "requestId": request_id},
        recommender_token,
    )
    expect(status, candidates, {200}, "Provider recommendation candidates failed")
    candidate_rows = candidates.get("providers", [])
    assert any(item["id"] == provider_id for item in candidate_rows), "Eligible provider was not suggested"
    for candidate in candidate_rows:
        service_keys = {
            f"{item.get('catId', '')}|{item.get('serviceId', '')}"
            for item in candidate.get("services", [])
            if isinstance(item, dict)
        }
        assert candidate.get("service") == "homecare|electrician" or "homecare|electrician" in service_keys, (
            f"Unrelated provider leaked into recommendation candidates: {candidate.get('id')}"
        )
        assert candidate.get("active") and candidate.get("verified") and candidate.get("status") == "available", (
            f"Inactive or incomplete provider was suggested: {candidate.get('id')}"
        )

    status, suggested = request(
        "/api/request-suggestions",
        {
            "action": "create",
            "requestId": request_id,
            "providerId": provider_id,
            "presetKey": "worked_before",
            "comment": "ملتزم بالمواعيد ويشرح العمل بوضوح",
        },
        recommender_token,
    )
    expect(status, suggested, {201}, "Provider recommendation creation failed")
    suggestion_id = suggested["suggestion"]["id"]

    status, duplicate_suggestion = request(
        "/api/request-suggestions",
        {
            "action": "create",
            "requestId": request_id,
            "providerId": provider_id,
            "presetKey": "excellent_work",
        },
        recommender_token,
    )
    assert status == 409 and duplicate_suggestion.get("error") == "suggestion_already_exists", (
        "Duplicate provider recommendation was accepted"
    )

    status, owner_with_suggestion = request("/api/bootstrap", token=user_token)
    expect(status, owner_with_suggestion, {200}, "Owner recommendation bootstrap failed")
    owner_request = next(item for item in owner_with_suggestion["customerRequests"] if item["id"] == request_id)
    assert any(item["id"] == suggestion_id for item in owner_request.get("providerSuggestions", [])), (
        "Recommendation did not reach the request owner"
    )
    owner_notification = next(
        (item for item in owner_with_suggestion["notifications"] if item.get("relatedId") == suggestion_id),
        None,
    )
    assert owner_notification and suggestion_id in owner_notification.get("actionRoute", ""), (
        "Recommendation notification does not open the exact recommendation"
    )

    status, provider_before_selection = request("/api/bootstrap", token=provider_token)
    expect(status, provider_before_selection, {200}, "Provider pre-selection bootstrap failed")
    assert not any(
        item.get("relatedId") == suggestion_id for item in provider_before_selection.get("notifications", [])
    ), "Provider was notified before the request owner selected the recommendation"

    status, selected_suggestion = request(
        "/api/request-suggestions",
        {"action": "select", "suggestionId": suggestion_id},
        user_token,
    )
    expect(status, selected_suggestion, {200}, "Selecting a provider recommendation failed")
    assert selected_suggestion["suggestion"]["status"] == "selected"

    status, provider_after_selection = request("/api/bootstrap", token=provider_token)
    expect(status, provider_after_selection, {200}, "Provider post-selection bootstrap failed")
    assert any(
        item.get("relatedId") == request_id and "اختارك" in item.get("title", "")
        for item in provider_after_selection.get("notifications", [])
    ), "Provider was not notified after the request owner selected the recommendation"

    status, provider_state = request("/api/bootstrap", token=provider_token)
    expect(status, provider_state, {200}, "Provider bootstrap failed")
    assigned = next(item for item in provider_state["customerRequests"] if item["id"] == request_id)
    assert not assigned.get("phone"), "Customer phone leaked before contact consent"

    status, offered = request(
        "/api/request/collaboration",
        {
            "id": request_id,
            "action": "offer",
            "price": 12,
            "duration": "خلال ساعتين",
            "note": "يشمل الفحص والتنفيذ بعد الموافقة",
        },
        provider_token,
    )
    expect(status, offered, {200}, "Provider offer failed")
    offer = offered["request"]["offers"][0]

    status, selected = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "choose_offer", "offerId": offer["id"]},
        user_token,
    )
    expect(status, selected, {200}, "Offer selection failed")
    assert selected["request"].get("acceptedProviderId") == provider_id, (
        f"Selected the wrong provider: {selected['request'].get('acceptedProviderId')} != {provider_id}"
    )
    consent = selected["request"].get("contactConsent", {})
    assert not any(consent.get(channel) for channel in ("chat", "whatsapp", "call")), "Contact consent must start disabled"

    status, blocked_message = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "message", "text": "رسالة قبل الموافقة"},
        provider_token,
    )
    assert status == 403 and blocked_message.get("error") == "chat_consent_required", (
        f"Chat opened before consent: HTTP {status} {blocked_message}"
    )

    status, chat_consent = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "contact_consent", "chat": True, "whatsapp": False, "call": False},
        user_token,
    )
    expect(status, chat_consent, {200}, "Chat consent failed")

    status, message = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "message", "text": "تم الاتفاق داخل المحادثة"},
        provider_token,
    )
    expect(status, message, {200}, "Request chat failed")
    assert not message["request"].get("phone"), "Chat consent exposed the phone"

    status, contact = request(
        "/api/request/collaboration",
        {"id": request_id, "action": "contact_consent", "chat": True, "whatsapp": True, "call": False},
        user_token,
    )
    expect(status, contact, {200}, "WhatsApp consent failed")

    status, provider_allowed = request("/api/bootstrap", token=provider_token)
    allowed = next(item for item in provider_allowed["customerRequests"] if item["id"] == request_id)
    assert allowed.get("phone") == "96895550992", "Approved customer contact was not exposed"

    status, user_allowed = request("/api/bootstrap", token=user_token)
    own_request = next(item for item in user_allowed["customerRequests"] if item["id"] == request_id)
    assert own_request.get("providerContact", {}).get("phone") == provider_phone, "Approved provider contact was not exposed"

    status, completed = request(
        "/api/user/requests", {"id": request_id, "action": "complete"}, user_token
    )
    expect(status, completed, {200}, "Request completion failed")

    status, review = request(
        "/api/reviews",
        {"providerId": provider_id, "requestId": request_id, "rating": 5, "comment": "خدمة ممتازة"},
        user_token,
    )
    expect(status, review, {201}, "Verified review failed")
    status, duplicate = request(
        "/api/reviews",
        {"providerId": provider_id, "requestId": request_id, "rating": 5, "comment": "مكرر"},
        user_token,
    )
    assert status == 409 and duplicate.get("error") == "request_already_reviewed", "Duplicate review was accepted"

    print(
        json.dumps(
            {
                "ok": True,
                "public_privacy": True,
                "provider_registration": True,
                "exact_matching": True,
                "request_marketplace": True,
                "active_request_visibility": True,
                "provider_recommendations": True,
                "recommendation_abuse_controls": True,
                "subscription_entitlements": True,
                "payment_integrity": True,
                "contact_consent": True,
                "request_chat": True,
                "verified_review": True,
                "visitor_isolation": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        raise
