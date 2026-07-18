import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory(prefix="khadamati-domain-")
os.environ["KHADAMATI_DB_PATH"] = str(Path(TEMP.name) / "domain.sqlite3")
os.environ["KHADAMATI_UPLOAD_DIR"] = str(Path(TEMP.name) / "uploads")
os.environ["KHADAMATI_ENV"] = "test"
os.environ["KHADAMATI_ADMIN_CODE"] = "839174"

import server  # noqa: E402
from khadamati_domain import (  # noqa: E402
    ContactConsentService,
    DomainError,
    EntitlementService,
    OTPService,
    PLAN_IDS,
    PaymentAdapter,
    RequestMarketplace,
    SubscriptionService,
)


class KhadamatiDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()

    def setUp(self):
        self.con = sqlite3.connect(server.DB_PATH)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.rollback()
        self.con.close()

    def provider(self, suffix, *, cat="homecare", service="electrician", gov="مسقط", wilayah="السيب"):
        provider_id = f"test-provider-{suffix}"
        server.upsert_provider(
            self.con,
            {
                "id": provider_id,
                "name": f"مزود اختبار {suffix}",
                "phone": f"96891{int(suffix):06d}" if str(suffix).isdigit() else f"96892{abs(hash(suffix)) % 1_000_000:06d}",
                "pin": "7349",
                "gov": gov,
                "wilayah": wilayah,
                "areas": [gov, wilayah],
                "bio": "مزود مهني لخدمة العملاء",
                "hours": "الأحد 8:00 ص - 8:00 م",
                "status": "available",
                "active": True,
                "verified": True,
                "commercialNo": f"CR-{suffix}",
                "services": [
                    {
                        "id": f"service-{suffix}",
                        "catId": cat,
                        "serviceId": service,
                        "priceFrom": 5,
                        "active": True,
                        "areas": [wilayah],
                    }
                ],
                "workImages": [],
                "documents": [],
            },
        )
        return provider_id

    def activate(self, provider_id, plan_id="professional_12m", now=None):
        return SubscriptionService(self.con, now=now).request_plan(
            provider_id, plan_id, payment_required=False, actor="test"
        )

    def test_exactly_five_active_plans(self):
        rows = self.con.execute("SELECT id FROM packages WHERE active=1 ORDER BY id").fetchall()
        self.assertEqual(sorted(PLAN_IDS), sorted(row["id"] for row in rows))
        self.assertEqual(5, len(rows))

    def test_seed_profiles_have_no_predictable_pin(self):
        seed_ids = [item["id"] for item in server.SEED_PROVIDERS]
        placeholders = ",".join("?" for _ in seed_ids)
        rows = self.con.execute(
            f"SELECT id,pin_hash FROM providers WHERE id IN ({placeholders})", seed_ids
        ).fetchall()
        for row in rows:
            self.assertFalse(server.verify_secret("1234", row["pin_hash"]))
            phone = next(item["phone"] for item in server.SEED_PROVIDERS if item["id"] == row["id"])
            self.assertFalse(server.verify_secret(str(phone)[-4:], row["pin_hash"]))

    def test_foundation_is_granted_once(self):
        provider_id = self.provider("101")
        first = self.activate(provider_id, "foundation_12m")
        self.assertEqual("foundation", first["status"])
        with self.assertRaises(DomainError) as caught:
            self.activate(provider_id, "foundation_12m")
        self.assertEqual("foundation_already_used", caught.exception.code)

    def test_expiry_grace_renewal_upgrade_and_downgrade(self):
        now = datetime(2026, 7, 18, 12, tzinfo=UTC)
        provider_id = self.provider("102")
        active = self.activate(provider_id, "basic_6m", now)
        subscription_id = active["subscriptionId"]
        service = SubscriptionService(self.con, now=now)

        self.con.execute(
            "UPDATE subscriptions SET end_date=?,status='active' WHERE id=?",
            ((now - timedelta(days=1)).date().isoformat(), subscription_id),
        )
        self.assertEqual("grace", service.synchronize_provider(provider_id)["state"])
        self.con.execute(
            "UPDATE subscriptions SET end_date=?,status='active' WHERE id=?",
            ((now - timedelta(days=15)).date().isoformat(), subscription_id),
        )
        self.assertEqual("expired", service.synchronize_provider(provider_id)["state"])

        self.con.execute(
            "UPDATE subscriptions SET end_date=?,status='active' WHERE id=?",
            ((now + timedelta(days=90)).date().isoformat(), subscription_id),
        )
        service.synchronize_provider(provider_id)
        upgrade = service.request_plan(provider_id, "professional_12m", actor="test")
        self.assertEqual("pending_payment", upgrade["status"])
        self.assertGreater(upgrade["amount"], 0)
        payment = PaymentAdapter(self.con).create_intent(upgrade["subscriptionId"], provider_id)
        PaymentAdapter(self.con, now=now).confirm_manual(payment["paymentId"], actor="test-admin")
        current = SubscriptionService(self.con, now=now).latest(provider_id)
        self.assertEqual("professional_12m", current["package_id"])
        downgrade = SubscriptionService(self.con, now=now).request_plan(
            provider_id, "basic_6m", actor="test"
        )
        self.assertEqual("next_renewal", downgrade["effective"])
        self.assertEqual("basic_6m", downgrade["renewalPackageId"])

    def test_payment_amount_and_webhook_are_server_verified(self):
        provider_id = self.provider("103")
        pending = SubscriptionService(self.con).request_plan(provider_id, "basic_12m")
        adapter = PaymentAdapter(self.con)
        with self.assertRaises(DomainError) as caught:
            adapter.create_intent(pending["subscriptionId"], provider_id, client_amount=0.1)
        self.assertEqual("payment_amount_mismatch", caught.exception.code)

        environment = {
            "KHADAMATI_PAYMENT_GATEWAY": "test-gateway",
            "KHADAMATI_PAYMENT_CHECKOUT_URL": "https://payments.invalid/checkout",
            "KHADAMATI_PAYMENT_WEBHOOK_SECRET": "unit-secret",
        }
        adapter = PaymentAdapter(self.con, environment=environment)
        intent = adapter.create_intent(pending["subscriptionId"], provider_id)
        payload = json.dumps(
            {
                "eventId": "event-domain-103",
                "reference": intent["reference"],
                "amount": intent["amount"],
                "currency": "OMR",
                "status": "paid",
            },
            separators=(",", ":"),
        ).encode()
        with self.assertRaises(DomainError) as caught:
            adapter.verify_webhook(payload, "bad-signature")
        self.assertEqual("invalid_webhook_signature", caught.exception.code)
        signature = hmac.new(b"unit-secret", payload, hashlib.sha256).hexdigest()
        result = adapter.verify_webhook(payload, signature)
        self.assertEqual("paid", result["status"])
        self.assertTrue(adapter.verify_webhook(payload, signature)["duplicate"])

    def test_matching_uses_exact_service_and_two_waves(self):
        now = datetime(2026, 7, 18, 12, tzinfo=UTC)
        exact_ids = []
        for index in range(201, 212):
            provider_id = self.provider(str(index))
            self.activate(provider_id, "professional_12m", now)
            exact_ids.append(provider_id)
        wrong_id = self.provider("299", cat="technology", service="computer_repair")
        self.activate(wrong_id, "professional_12m", now)
        user_id = "domain-user-1"
        request_id = "domain-request-1"
        self.con.execute(
            "INSERT OR REPLACE INTO app_users(id,phone,name,pin_hash) VALUES(?,?,?,?)",
            (user_id, "96895550101", "مستخدم المطابقة", server.hash_pin("2468")),
        )
        self.con.execute(
            """INSERT OR REPLACE INTO customer_requests(
            id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,status)
            VALUES(?,?,?,?,?,?,?,?, 'matching')""",
            (request_id, user_id, "مستخدم المطابقة", "96895550101", "homecare|electrician", "كهربائي", "مسقط", "السيب"),
        )
        marketplace = RequestMarketplace(self.con, now=now, expansion_minutes=20, min_offers=2)
        ranked = marketplace.schedule(request_id)
        self.assertEqual(10, len(ranked))
        self.assertNotIn(wrong_id, [item["providerId"] for item in ranked])
        first = marketplace.release_due(request_id)
        self.assertEqual(5, len(first))
        expanded = RequestMarketplace(
            self.con, now=now + timedelta(minutes=21), expansion_minutes=20, min_offers=2
        ).release_due(request_id)
        self.assertEqual(5, len(expanded))

    def test_contact_consent_is_scoped_and_revocable(self):
        provider_id = self.provider("104")
        user_id = "domain-user-2"
        request_id = "domain-request-2"
        self.con.execute(
            "INSERT OR REPLACE INTO app_users(id,phone,name,pin_hash) VALUES(?,?,?,?)",
            (user_id, "96895550102", "مستخدم الموافقة", server.hash_pin("2468")),
        )
        self.con.execute(
            """INSERT OR REPLACE INTO customer_requests(
            id,user_id,customer_name,phone,service_value,accepted_provider_id,status)
            VALUES(?,?,?,?,?,?,'accepted')""",
            (request_id, user_id, "مستخدم الموافقة", "96895550102", "homecare|electrician", provider_id),
        )
        consent = ContactConsentService(self.con)
        self.assertFalse(consent.allowed(request_id, provider_id, "whatsapp"))
        consent.set_channel(request_id, user_id, provider_id, "whatsapp", True)
        self.assertTrue(consent.allowed(request_id, provider_id, "whatsapp"))
        self.assertFalse(consent.allowed(request_id, provider_id, "call"))
        consent.set_channel(request_id, user_id, provider_id, "whatsapp", False)
        self.assertFalse(consent.allowed(request_id, provider_id, "whatsapp"))

    def test_individual_provider_is_limited_to_one_primary_service(self):
        provider_id = self.provider("105")
        self.activate(provider_id, "basic_12m")
        entitlement = EntitlementService(self.con)
        valid = entitlement.validate_profile(
            provider_id,
            services=[{"catId": "cleaning", "serviceId": "home_cleaning"}],
            areas=["السيب", "بوشر"],
        )
        self.assertEqual(1, valid["maxServices"])
        self.assertEqual(2, valid["maxCategories"])
        with self.assertRaises(DomainError) as caught:
            entitlement.validate_profile(
                provider_id,
                services=[
                    {"catId": "cleaning", "serviceId": "home_cleaning"},
                    {"catId": "technology", "serviceId": "computer_repair"},
                ],
                areas=["السيب"],
            )
        self.assertEqual("service_limit_exceeded", caught.exception.code)

    def test_business_plan_allows_multiple_services_and_categories_within_limit(self):
        provider_id = self.provider("106")
        self.con.execute("UPDATE providers SET provider_type='company' WHERE id=?", (provider_id,))
        self.activate(provider_id, "business_12m")
        services = [
            {"catId": f"category-{index % 5}", "serviceId": f"service-{index}"}
            for index in range(20)
        ]
        limits = EntitlementService(self.con).validate_profile(
            provider_id, services=services, areas=["السيب", "بوشر"]
        )
        self.assertEqual(20, limits["maxServices"])
        self.assertEqual(5, limits["maxCategories"])
        with self.assertRaises(DomainError) as caught:
            EntitlementService(self.con).validate_profile(
                provider_id,
                services=services + [{"catId": "category-6", "serviceId": "service-21"}],
                areas=["السيب"],
            )
        self.assertEqual("provider_category_limit", caught.exception.code)

    def test_request_eligibility_requires_approved_available_provider(self):
        provider_id = self.provider("107")
        self.activate(provider_id, "professional_12m")
        service = EntitlementService(self.con)
        self.assertTrue(service.can_receive(provider_id)[0])
        self.con.execute("UPDATE providers SET verified=0 WHERE id=?", (provider_id,))
        self.assertEqual("provider_not_approved", service.can_receive(provider_id)[1])
        self.con.execute("UPDATE providers SET verified=1,status='busy' WHERE id=?", (provider_id,))
        self.assertEqual("provider_unavailable", service.can_receive(provider_id)[1])

    def test_service_limits_cannot_be_bypassed_through_normalizer(self):
        rows = self.con.execute(
            "SELECT category_id,id FROM services WHERE active=1 ORDER BY category_id,id"
        ).fetchall()
        first = rows[0]
        same_category = next(
            row for row in rows if row["category_id"] == first["category_id"] and row["id"] != first["id"]
        )
        other_category = next(row for row in rows if row["category_id"] != first["category_id"])
        with self.assertRaises(DomainError) as caught:
            server.normalized_provider_services(
                self.con,
                [
                    {"catId": first["category_id"], "serviceId": first["id"]},
                    {"catId": same_category["category_id"], "serviceId": same_category["id"]},
                ],
                limit=1,
                category_limit=2,
            )
        self.assertEqual("service_limit_exceeded", caught.exception.code)
        with self.assertRaises(DomainError) as caught:
            server.normalized_provider_services(
                self.con,
                [
                    {"catId": first["category_id"], "serviceId": first["id"]},
                    {"catId": other_category["category_id"], "serviceId": other_category["id"]},
                ],
                limit=2,
                category_limit=1,
            )
        self.assertEqual("provider_category_limit", caught.exception.code)

    def test_map_location_is_exact_only_when_provider_allows_visibility(self):
        provider_id = self.provider("108")
        self.activate(provider_id, "professional_12m")
        self.con.execute(
            "UPDATE providers SET latitude=?,longitude=?,map_visible=1 WHERE id=?",
            (23.612345, 58.241234, provider_id),
        )
        row = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        public = server.row_provider(row, private=False)
        self.assertEqual(23.612345, public["location"]["lat"])
        self.assertEqual(58.241234, public["location"]["lng"])
        self.con.execute("UPDATE providers SET map_visible=0 WHERE id=?", (provider_id,))
        row = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        self.assertIsNone(server.row_provider(row, private=False)["location"])

    def test_service_availability_excludes_unapproved_and_busy_providers(self):
        provider_id = self.provider("109")
        self.activate(provider_id, "professional_12m")
        snapshot = server.service_availability_snapshot(self.con)
        self.assertGreater(snapshot["services"].get("homecare|electrician", 0), 0)
        self.con.execute("UPDATE providers SET status='busy' WHERE id=?", (provider_id,))
        busy = server.service_availability_snapshot(self.con)
        self.assertLess(
            busy["services"].get("homecare|electrician", 0),
            snapshot["services"].get("homecare|electrician", 0),
        )
        self.con.execute("UPDATE providers SET status='available',verified=0 WHERE id=?", (provider_id,))
        unapproved = server.service_availability_snapshot(self.con)
        self.assertEqual(
            busy["services"].get("homecare|electrician", 0),
            unapproved["services"].get("homecare|electrician", 0),
        )
    def test_production_otp_never_exposes_development_code(self):
        environment = {
            "KHADAMATI_ENV": "production",
            "KHADAMATI_DEV_OTP_CODE": "111111",
            "KHADAMATI_OTP_PEPPER": "pepper",
        }
        with self.assertRaises(DomainError) as caught:
            OTPService(self.con, environment=environment).request("96895550103", "login")
        self.assertEqual("otp_delivery_unavailable", caught.exception.code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
