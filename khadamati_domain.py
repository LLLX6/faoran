from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import json
import os
import secrets
from typing import Any, Callable, Iterable


SUPPORT_EMAIL = os.environ.get("KHADAMATI_SUPPORT_EMAIL", "om.khadamati@gmail.com").strip()
POLICY_VERSION = "2026-07-18.1"
MIGRATION_KEY = "KHADAMATI_SUBSCRIPTION_MIGRATION_V1"
RANKING_VERSION = "khadamati-ranking-v1"
OMR = "OMR"


PLAN_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "foundation_12m",
        "ar": "فترة التأسيس",
        "en": "Foundation - 12 months",
        "price": 0,
        "currency": OMR,
        "duration_days": 365,
        "max_services": 3,
        "max_categories": 1,
        "max_images": 5,
        "max_wilayats": 5,
        "max_governorates": 1,
        "monthly_response_limit": 30,
        "lead_delay_minutes": 15,
        "max_team_members": 1,
        "max_branches": 1,
        "shared_inbox": 0,
        "advanced_reports": 0,
        "badge_ar": "",
        "badge_en": "",
        "foundation_once": 1,
        "verified_required": 1,
    },
    {
        "id": "basic_6m",
        "ar": "الأساسي - 6 أشهر",
        "en": "Basic - 6 months",
        "price": 6,
        "currency": OMR,
        "duration_days": 183,
        "max_services": 5,
        "max_categories": 2,
        "max_images": 5,
        "max_wilayats": 10,
        "max_governorates": 1,
        "monthly_response_limit": 60,
        "lead_delay_minutes": 10,
        "max_team_members": 1,
        "max_branches": 1,
        "shared_inbox": 0,
        "advanced_reports": 0,
        "badge_ar": "",
        "badge_en": "",
        "foundation_once": 0,
        "verified_required": 0,
    },
    {
        "id": "basic_12m",
        "ar": "الأساسي - سنوي",
        "en": "Basic - annual",
        "price": 10,
        "currency": OMR,
        "duration_days": 365,
        "max_services": 5,
        "max_categories": 2,
        "max_images": 5,
        "max_wilayats": 10,
        "max_governorates": 1,
        "monthly_response_limit": 60,
        "lead_delay_minutes": 10,
        "max_team_members": 1,
        "max_branches": 1,
        "shared_inbox": 0,
        "advanced_reports": 0,
        "badge_ar": "",
        "badge_en": "",
        "foundation_once": 0,
        "verified_required": 0,
    },
    {
        "id": "professional_12m",
        "ar": "الاحترافي - سنوي",
        "en": "Professional - annual",
        "price": 20,
        "currency": OMR,
        "duration_days": 365,
        "max_services": 10,
        "max_categories": 3,
        "max_images": 10,
        "max_wilayats": 25,
        "max_governorates": 2,
        "monthly_response_limit": 150,
        "lead_delay_minutes": 0,
        "max_team_members": 1,
        "max_branches": 1,
        "shared_inbox": 0,
        "advanced_reports": 1,
        "badge_ar": "الأكثر اختيارًا",
        "badge_en": "Most selected",
        "foundation_once": 0,
        "verified_required": 0,
    },
    {
        "id": "business_12m",
        "ar": "الأعمال - سنوي",
        "en": "Business - annual",
        "price": 40,
        "currency": OMR,
        "duration_days": 365,
        "max_services": 20,
        "max_categories": 5,
        "max_images": 15,
        "max_wilayats": 0,
        "max_governorates": 5,
        "monthly_response_limit": 0,
        "lead_delay_minutes": 0,
        "max_team_members": 3,
        "max_branches": 3,
        "shared_inbox": 1,
        "advanced_reports": 1,
        "badge_ar": "فريق أعمال",
        "badge_en": "Business team",
        "foundation_once": 0,
        "verified_required": 0,
    },
)

PLAN_IDS = tuple(plan["id"] for plan in PLAN_DEFINITIONS)
LEGACY_PLAN_MAP = {
    "intro": "foundation_12m",
    "intro_90": "foundation_12m",
    "basic_90": "basic_6m",
    "individual_6m": "basic_6m",
    "active_90": "basic_6m",
    "individual_year": "basic_12m",
    "featured_90": "professional_12m",
    "local_visibility": "professional_12m",
    "service_priority": "professional_12m",
    "company_90": "business_12m",
    "company_year": "business_12m",
    "company_growth": "business_12m",
    "plus": "professional_12m",
    "growth": "business_12m",
    "basic": "basic_6m",
}

SUBSCRIPTION_STATES = {
    "foundation",
    "pending_payment",
    "active",
    "expiring",
    "grace",
    "expired",
    "suspended",
    "cancelled",
    "refunded",
}


class DomainError(ValueError):
    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def as_money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    except Exception as exc:
        raise DomainError("invalid_amount") from exc


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def public_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def normalized_phone(value: Any) -> str:
    phone = "".join(ch for ch in str(value or "") if ch.isdigit())
    if phone.startswith("0"):
        phone = "968" + phone[1:]
    if len(phone) == 8:
        phone = "968" + phone
    return phone


def row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


class PlanCatalog:
    @staticmethod
    def seed(con) -> None:
        for plan in PLAN_DEFINITIONS:
            entitlements = {
                "maxServices": plan["max_services"],
                "maxCategories": plan["max_categories"],
                "maxImages": plan["max_images"],
                "maxWilayats": plan["max_wilayats"],
                "maxGovernorates": plan["max_governorates"],
                "monthlyResponses": plan["monthly_response_limit"],
                "leadDelayMinutes": plan["lead_delay_minutes"],
                "teamMembers": plan["max_team_members"],
                "branches": plan["max_branches"],
                "sharedInbox": bool(plan["shared_inbox"]),
                "advancedReports": bool(plan["advanced_reports"]),
            }
            con.execute(
                """INSERT INTO packages(
                id,ar,en,price,duration_days,featured_boost,max_services,max_images,active,
                currency,max_categories,max_wilayats,max_governorates,monthly_response_limit,
                lead_delay_minutes,max_team_members,max_branches,shared_inbox,
                advanced_reports,badge_ar,badge_en,foundation_once,verified_required,
                legacy,entitlements)
                VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
                ON CONFLICT(id) DO UPDATE SET ar=excluded.ar,en=excluded.en,
                price=excluded.price,duration_days=excluded.duration_days,
                max_services=excluded.max_services,max_images=excluded.max_images,
                currency=excluded.currency,max_categories=excluded.max_categories,
                max_wilayats=excluded.max_wilayats,
                max_governorates=excluded.max_governorates,
                monthly_response_limit=excluded.monthly_response_limit,
                lead_delay_minutes=excluded.lead_delay_minutes,
                max_team_members=excluded.max_team_members,max_branches=excluded.max_branches,
                shared_inbox=excluded.shared_inbox,advanced_reports=excluded.advanced_reports,
                badge_ar=excluded.badge_ar,badge_en=excluded.badge_en,
                foundation_once=excluded.foundation_once,verified_required=excluded.verified_required,
                active=1,legacy=0,entitlements=excluded.entitlements""",
                (
                    plan["id"], plan["ar"], plan["en"], plan["price"],
                    plan["duration_days"], 0, plan["max_services"], plan["max_images"],
                    plan["currency"], plan["max_categories"], plan["max_wilayats"], plan["max_governorates"],
                    plan["monthly_response_limit"], plan["lead_delay_minutes"],
                    plan["max_team_members"], plan["max_branches"], plan["shared_inbox"],
                    plan["advanced_reports"], plan["badge_ar"], plan["badge_en"],
                    plan["foundation_once"], plan["verified_required"], dump(entitlements),
                ),
            )
        con.execute(
            "UPDATE packages SET active=0,legacy=1 WHERE id NOT IN (?,?,?,?,?)",
            PLAN_IDS,
        )

    @staticmethod
    def get(con, plan_id: str, active_only: bool = True) -> dict[str, Any] | None:
        if active_only:
            row = con.execute(
                "SELECT * FROM packages WHERE id=? AND active=1 AND legacy=0", (plan_id,)
            ).fetchone()
        else:
            row = con.execute("SELECT * FROM packages WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        result = row_dict(row)
        result["entitlements"] = load(result.get("entitlements"), {})
        return result

    @staticmethod
    def active(con) -> list[dict[str, Any]]:
        return [PlanCatalog.get(con, row["id"], False) for row in con.execute(
            "SELECT id FROM packages WHERE active=1 AND legacy=0 ORDER BY price,duration_days"
        )]


class SubscriptionService:
    ACTIVE_ACCESS_STATES = {"foundation", "active", "expiring", "grace"}
    HARD_STOP_STATES = {"expired", "suspended", "cancelled", "refunded", "pending_payment"}

    def __init__(self, con, *, now: datetime | None = None, grace_days: int = 14):
        self.con = con
        self.now = (now or utcnow()).astimezone(UTC)
        self.grace_days = max(0, int(grace_days))

    def latest(self, provider_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            """SELECT * FROM subscriptions WHERE provider_id=?
            ORDER BY CASE status
              WHEN 'active' THEN 1 WHEN 'foundation' THEN 1 WHEN 'expiring' THEN 1
              WHEN 'grace' THEN 2 WHEN 'pending_payment' THEN 3 ELSE 4 END,
              COALESCE(activated_at,created_at) DESC LIMIT 1""",
            (provider_id,),
        ).fetchone()
        return row_dict(row) or None

    def computed_state(self, subscription: dict[str, Any] | Any) -> str:
        item = row_dict(subscription)
        stored = str(item.get("status") or "pending_payment")
        if stored in {"suspended", "cancelled", "refunded", "pending_payment"}:
            return stored
        end = parse_datetime(item.get("end_date"))
        if not end:
            return "pending_payment" if stored not in {"foundation", "active"} else stored
        remaining = (end.date() - self.now.date()).days
        if remaining < -self.grace_days:
            return "expired"
        if remaining < 0:
            return "grace"
        if remaining <= 30:
            return "expiring"
        if stored == "foundation":
            return "foundation"
        return "active"

    def synchronize_provider(self, provider_id: str) -> dict[str, Any]:
        subscription = self.latest(provider_id)
        if not subscription:
            self.con.execute(
                """UPDATE providers SET subscription_state='expired',listing_enabled=0,
                request_enabled=0 WHERE id=?""",
                (provider_id,),
            )
            return {"state": "expired", "changed": False, "subscription": None}
        old_state = subscription.get("status") or "pending_payment"
        state = self.computed_state(subscription)
        if state != old_state and old_state not in {"suspended", "cancelled", "refunded", "pending_payment"}:
            self.con.execute(
                "UPDATE subscriptions SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (state, subscription["id"]),
            )
            self._event(subscription["id"], "state_changed", old_state, state, "system")
            subscription["status"] = state
        allowed = state in self.ACTIVE_ACCESS_STATES
        self.con.execute(
            """UPDATE providers SET package_id=?,subscription_state=?,listing_enabled=?,
            request_enabled=?,subscription_start=?,subscription_until=? WHERE id=?""",
            (
                subscription["package_id"], state, int(allowed), int(allowed),
                subscription.get("start_date") or "", subscription.get("end_date") or "", provider_id,
            ),
        )
        return {"state": state, "changed": state != old_state, "subscription": subscription}

    def synchronize_all(self) -> list[dict[str, Any]]:
        changes = []
        for row in self.con.execute("SELECT id FROM providers"):
            result = self.synchronize_provider(row["id"])
            if result["changed"]:
                changes.append({"providerId": row["id"], **result})
        return changes

    def foundation_eligible(self, provider_id: str) -> tuple[bool, str]:
        provider = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not provider:
            return False, "provider_not_found"
        if not int(provider["verified"] or 0):
            return False, "foundation_requires_verification"
        phone = normalized_phone(provider["phone"])
        commercial = str(provider["commercial_no"] or "").strip().casefold()
        fingerprint = hashlib.sha256(f"{phone}|{commercial}".encode("utf-8")).hexdigest()
        used = self.con.execute(
            """SELECT id FROM foundation_claims WHERE provider_id=? OR phone=?
            OR (commercial_no!='' AND commercial_no=?) OR fingerprint=? LIMIT 1""",
            (provider_id, phone, commercial, fingerprint),
        ).fetchone()
        return (not bool(used), "foundation_already_used" if used else "")

    def request_plan(
        self,
        provider_id: str,
        plan_id: str,
        *,
        coupon_code: str = "",
        payment_required: bool = True,
        actor: str = "provider",
    ) -> dict[str, Any]:
        plan = PlanCatalog.get(self.con, plan_id)
        provider = self.con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not plan:
            raise DomainError("package_not_found", 404)
        if not provider:
            raise DomainError("provider_not_found", 404)
        if plan_id == "business_12m" and str(provider["provider_type"] or "individual") != "company":
            raise DomainError("business_plan_requires_company", 409)
        if plan_id == "foundation_12m":
            eligible, reason = self.foundation_eligible(provider_id)
            if not eligible:
                raise DomainError(reason, 409)
        current = self.latest(provider_id)
        current_state = self.computed_state(current) if current else "expired"
        current_plan = PlanCatalog.get(self.con, current.get("package_id", ""), False) if current else None
        is_upgrade = bool(
            current and current_state in self.ACTIVE_ACCESS_STATES
            and as_money(plan["price"]) > as_money((current_plan or {}).get("price"))
        )
        is_downgrade = bool(
            current and current_state in self.ACTIVE_ACCESS_STATES
            and as_money(plan["price"]) < as_money((current_plan or {}).get("price"))
            and plan_id != "foundation_12m"
        )
        is_renewal = bool(
            current and current_state in self.ACTIVE_ACCESS_STATES
            and plan_id == current.get("package_id")
        )
        if is_downgrade:
            self.con.execute(
                "UPDATE subscriptions SET renewal_package_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (plan_id, current["id"]),
            )
            self._event(current["id"], "downgrade_scheduled", current["package_id"], plan_id, actor)
            return {
                "subscriptionId": current["id"],
                "status": current_state,
                "renewalPackageId": plan_id,
                "effective": "next_renewal",
                "amount": float(as_money(plan["price"])),
                "currency": plan["currency"],
            }
        quote = self.upgrade_quote(current, plan) if is_upgrade else {
            "amountDue": as_money(plan["price"]), "credit": Decimal("0.000")
        }
        discount = self._coupon_discount(coupon_code, provider_id, plan_id, quote["amountDue"])
        amount_due = max(Decimal("0.000"), quote["amountDue"] - discount)
        subscription_id = public_id("sub")
        status = "foundation" if plan_id == "foundation_12m" else "pending_payment"
        start = self.now
        end = start + timedelta(days=int(plan["duration_days"]))
        self.con.execute(
            """INSERT INTO subscriptions(
            id,provider_id,package_id,amount,status,start_date,end_date,note,currency,
            grace_days,previous_package_id,proration_amount,credit_amount,activated_at,
            grace_until,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                subscription_id, provider_id, plan_id, float(amount_due), status,
                start.date().isoformat() if status == "foundation" else "",
                end.date().isoformat() if status == "foundation" else "",
                "", plan["currency"], self.grace_days,
                current.get("package_id", "") if current else "",
                float(quote["amountDue"]), float(quote["credit"]),
                iso(start) if status == "foundation" else "",
                (end + timedelta(days=self.grace_days)).date().isoformat() if status == "foundation" else "",
                dump({
                    "couponCode": coupon_code,
                    "discount": float(discount),
                    "upgrade": is_upgrade,
                    "renewal": is_renewal,
                }),
            ),
        )
        if discount > 0 and coupon_code:
            coupon = self.con.execute(
                "SELECT id FROM coupons WHERE UPPER(code)=?",
                (str(coupon_code).strip().upper(),),
            ).fetchone()
            if coupon:
                self.con.execute(
                    """INSERT INTO coupon_redemptions(
                    id,coupon_id,provider_id,subscription_id,amount)
                    VALUES(?,?,?,?,?)""",
                    (
                        public_id("cred"), coupon["id"], provider_id,
                        subscription_id, float(discount),
                    ),
                )
                self.con.execute(
                    "UPDATE coupons SET uses_count=uses_count+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (coupon["id"],),
                )
        if status == "foundation":
            self._claim_foundation(provider_id, subscription_id)
            if current and current.get("id") != subscription_id:
                self.con.execute(
                    "UPDATE subscriptions SET status='cancelled',cancelled_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (iso(self.now), current["id"]),
                )
            self.synchronize_provider(provider_id)
        elif not payment_required:
            self.activate(subscription_id, actor=actor)
            status = "active"
        self._event(subscription_id, "requested", "", status, actor)
        return {
            "subscriptionId": subscription_id,
            "status": status,
            "amount": float(amount_due),
            "currency": plan["currency"],
            "durationDays": plan["duration_days"],
            "proration": float(quote["amountDue"]),
            "credit": float(quote["credit"]),
            "discount": float(discount),
            "requiresPayment": status == "pending_payment",
        }

    def upgrade_quote(self, current: dict[str, Any] | None, target_plan: dict[str, Any]) -> dict[str, Decimal]:
        if not current:
            return {"amountDue": as_money(target_plan["price"]), "credit": Decimal("0.000")}
        old_plan = PlanCatalog.get(self.con, current.get("package_id", ""), False) or {}
        end = parse_datetime(current.get("end_date"))
        remaining = max(0, (end.date() - self.now.date()).days) if end else 0
        old_duration = max(1, int(old_plan.get("duration_days") or 1))
        credit = (as_money(old_plan.get("price")) * Decimal(remaining) / Decimal(old_duration)).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        return {
            "amountDue": max(Decimal("0.000"), as_money(target_plan["price"]) - credit),
            "credit": credit,
        }

    def activate(self, subscription_id: str, *, payment_id: str = "", actor: str = "admin") -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        subscription = row_dict(row)
        if subscription["status"] in {"refunded", "cancelled"}:
            raise DomainError("subscription_cannot_be_activated", 409)
        plan = PlanCatalog.get(self.con, subscription["package_id"], False)
        if not plan:
            raise DomainError("package_not_found", 404)
        metadata = load(subscription.get("metadata"), {})
        old = self.latest(subscription["provider_id"])
        old_end = parse_datetime(old.get("end_date")) if old and old.get("id") != subscription_id else None
        start = max(self.now, old_end) if metadata.get("renewal") and old_end else self.now
        end = start + timedelta(days=int(plan["duration_days"]))
        if old and old["id"] != subscription_id and self.computed_state(old) in self.ACTIVE_ACCESS_STATES:
            self.con.execute(
                "UPDATE subscriptions SET status='cancelled',cancelled_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (iso(start), old["id"]),
            )
            self._event(old["id"], "superseded", old["status"], "cancelled", actor)
        state = "foundation" if plan["id"] == "foundation_12m" else "active"
        self.con.execute(
            """UPDATE subscriptions SET status=?,start_date=?,end_date=?,activated_at=?,
            grace_until=?,payment_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                state, start.date().isoformat(), end.date().isoformat(), iso(start),
                (end + timedelta(days=self.grace_days)).date().isoformat(), payment_id, subscription_id,
            ),
        )
        if state == "foundation":
            self._claim_foundation(subscription["provider_id"], subscription_id)
        self.synchronize_provider(subscription["provider_id"])
        self._event(subscription_id, "activated", subscription["status"], state, actor)
        return row_dict(self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone())

    def extend(self, subscription_id: str, *, days: int | None = None, actor: str = "admin") -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        subscription = row_dict(row)
        plan = PlanCatalog.get(self.con, subscription["package_id"], False)
        if not plan:
            raise DomainError("package_not_found", 404)
        extension_days = max(1, int(days or plan["duration_days"]))
        current_end = parse_datetime(subscription.get("end_date"))
        base = max(self.now, current_end) if current_end else self.now
        new_end = base + timedelta(days=extension_days)
        old_state = subscription.get("status", "")
        state = "foundation" if subscription["package_id"] == "foundation_12m" else "active"
        self.con.execute(
            """UPDATE subscriptions SET status=?,end_date=?,grace_until=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                state, new_end.date().isoformat(),
                (new_end + timedelta(days=self.grace_days)).date().isoformat(), subscription_id,
            ),
        )
        self._event(subscription_id, "extended", old_state, state, actor, f"{extension_days} days")
        self.synchronize_provider(subscription["provider_id"])
        return row_dict(self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone())

    def suspend(self, subscription_id: str, *, actor: str = "admin", reason: str = "") -> None:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        old = row["status"]
        self.con.execute(
            "UPDATE subscriptions SET status='suspended',note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (reason[:500], subscription_id),
        )
        self._event(subscription_id, "suspended", old, "suspended", actor, reason)
        self.synchronize_provider(row["provider_id"])

    def cancel(self, subscription_id: str, *, actor: str = "admin", reason: str = "") -> None:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        if row["status"] == "refunded":
            raise DomainError("refunded_subscription_cannot_be_cancelled", 409)
        self.con.execute(
            """UPDATE subscriptions SET status='cancelled',cancelled_at=?,note=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (iso(self.now), reason[:500], subscription_id),
        )
        self._event(subscription_id, "cancelled", row["status"], "cancelled", actor, reason)
        self.synchronize_provider(row["provider_id"])

    def refund(self, subscription_id: str, *, actor: str = "admin", reason: str = "") -> None:
        row = self.con.execute("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise DomainError("subscription_not_found", 404)
        self.con.execute(
            """UPDATE subscriptions SET status='refunded',refunded_at=?,note=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (iso(self.now), reason[:500], subscription_id),
        )
        self._event(subscription_id, "refunded", row["status"], "refunded", actor, reason)
        self.synchronize_provider(row["provider_id"])

    def _event(self, subscription_id: str, event_type: str, before: str, after: str, actor: str, detail: str = "") -> None:
        self.con.execute(
            """INSERT INTO subscription_events(
            id,subscription_id,event_type,from_state,to_state,actor,detail)
            VALUES(?,?,?,?,?,?,?)""",
            (public_id("sevt"), subscription_id, event_type, before, after, actor, detail[:900]),
        )

    def _claim_foundation(self, provider_id: str, subscription_id: str) -> None:
        provider = self.con.execute("SELECT phone,commercial_no FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not provider:
            raise DomainError("provider_not_found", 404)
        phone = normalized_phone(provider["phone"])
        commercial = str(provider["commercial_no"] or "").strip().casefold()
        fingerprint = hashlib.sha256(f"{phone}|{commercial}".encode("utf-8")).hexdigest()
        try:
            self.con.execute(
                """INSERT INTO foundation_claims(
                id,provider_id,phone,commercial_no,fingerprint,subscription_id)
                VALUES(?,?,?,?,?,?)""",
                (public_id("fnd"), provider_id, phone, commercial, fingerprint, subscription_id),
            )
        except Exception as exc:
            if "UNIQUE" not in str(exc).upper():
                raise

    def _coupon_discount(self, code: str, provider_id: str, plan_id: str, amount: Decimal) -> Decimal:
        code = str(code or "").strip().upper()
        if not code:
            return Decimal("0.000")
        row = self.con.execute("SELECT * FROM coupons WHERE UPPER(code)=? AND active=1", (code,)).fetchone()
        if not row:
            raise DomainError("coupon_invalid", 400)
        now = self.now
        starts = parse_datetime(row["starts_at"])
        ends = parse_datetime(row["ends_at"])
        if starts and starts > now or ends and ends < now:
            raise DomainError("coupon_expired", 409)
        if int(row["max_uses"] or 0) and int(row["uses_count"] or 0) >= int(row["max_uses"]):
            raise DomainError("coupon_limit_reached", 409)
        allowed = load(row["applies_to"], [])
        if allowed and plan_id not in allowed:
            raise DomainError("coupon_not_for_plan", 409)
        used = self.con.execute(
            "SELECT id FROM coupon_redemptions WHERE coupon_id=? AND provider_id=?",
            (row["id"], provider_id),
        ).fetchone()
        if used:
            raise DomainError("coupon_already_used", 409)
        value = as_money(row["discount_value"])
        discount = amount * value / Decimal("100") if row["discount_type"] == "percent" else value
        return min(amount, discount.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


class EntitlementService:
    def __init__(self, con, *, now: datetime | None = None):
        self.con = con
        self.now = now or utcnow()

    def for_provider(self, provider_id: str) -> dict[str, Any]:
        subscription_service = SubscriptionService(self.con, now=self.now)
        sync = subscription_service.synchronize_provider(provider_id)
        subscription = sync.get("subscription")
        plan = PlanCatalog.get(self.con, subscription.get("package_id", ""), False) if subscription else None
        return {
            "providerId": provider_id,
            "state": sync["state"],
            "planId": plan.get("id", "") if plan else "",
            "allowed": sync["state"] in SubscriptionService.ACTIVE_ACCESS_STATES,
            "maxServices": int((plan or {}).get("max_services") or 0),
            "maxCategories": int((plan or {}).get("max_categories") or 0),
            "maxImages": int((plan or {}).get("max_images") or 0),
            "maxWilayats": int((plan or {}).get("max_wilayats") or 0),
            "maxGovernorates": int((plan or {}).get("max_governorates") or 0),
            "monthlyResponses": int((plan or {}).get("monthly_response_limit") or 0),
            "leadDelayMinutes": int((plan or {}).get("lead_delay_minutes") or 0),
            "teamMembers": int((plan or {}).get("max_team_members") or 1),
            "branches": int((plan or {}).get("max_branches") or 1),
            "sharedInbox": bool((plan or {}).get("shared_inbox")),
            "advancedReports": bool((plan or {}).get("advanced_reports")),
            "badgeAr": (plan or {}).get("badge_ar", ""),
            "badgeEn": (plan or {}).get("badge_en", ""),
        }

    def profile_limits(self, provider_id: str, *, preserve_existing: bool = True) -> dict[str, Any]:
        """Return enforceable limits without deleting data retained from an older plan."""
        entitlements = self.for_provider(provider_id)
        if not preserve_existing:
            return entitlements
        provider = self.con.execute(
            "SELECT provider_type,services,areas FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not provider:
            return entitlements
        existing_services = load(provider["services"], [])
        existing_categories = {
            str(item.get("catId", "")).strip()
            for item in existing_services
            if isinstance(item, dict) and str(item.get("catId", "")).strip()
        }
        existing_service_count = len({
            f"{item.get('catId')}|{item.get('serviceId')}"
            for item in existing_services
            if isinstance(item, dict) and item.get("catId") and item.get("serviceId")
        })
        existing_area_count = len({
            str(area).strip() for area in load(provider["areas"], []) if str(area).strip()
        })
        is_company = str(provider["provider_type"] or "individual") == "company"
        service_limit = int(entitlements.get("maxServices") or 0) if is_company else 1
        return {
            **entitlements,
            "accountType": "company" if is_company else "individual",
            "maxServices": max(service_limit, existing_service_count),
            "maxCategories": max(int(entitlements.get("maxCategories") or 0), len(existing_categories)),
            "maxWilayats": max(int(entitlements.get("maxWilayats") or 0), existing_area_count),
            "grandfathered": bool(
                existing_service_count > service_limit
                or len(existing_categories) > int(entitlements.get("maxCategories") or 0)
                or existing_area_count > int(entitlements.get("maxWilayats") or 0)
            ),
        }

    def validate_profile(self, provider_id: str, *, services: Iterable[Any], areas: Iterable[Any]) -> dict[str, Any]:
        entitlements = self.profile_limits(provider_id, preserve_existing=True)
        categories = {
            str(item.get("catId", "")).strip()
            for item in services
            if isinstance(item, dict) and str(item.get("catId", "")).strip()
        }
        services_count = len({
            f"{item.get('catId')}|{item.get('serviceId')}"
            for item in services if isinstance(item, dict) and item.get("catId") and item.get("serviceId")
        })
        areas_count = len({str(area).strip() for area in areas if str(area).strip()})
        if entitlements["maxCategories"] and len(categories) > entitlements["maxCategories"]:
            raise DomainError("provider_category_limit", 409)
        if entitlements["maxServices"] and services_count > entitlements["maxServices"]:
            raise DomainError("service_limit_exceeded", 409)
        if entitlements["maxWilayats"] and areas_count > entitlements["maxWilayats"]:
            raise DomainError("wilayah_limit_exceeded", 409)
        return entitlements

    def can_receive(self, provider_id: str) -> tuple[bool, str, dict[str, Any]]:
        entitlements = self.for_provider(provider_id)
        provider = self.con.execute(
            "SELECT active,verified,status,listing_enabled,request_enabled FROM providers WHERE id=?", (provider_id,)
        ).fetchone()
        if not provider or not int(provider["active"] or 0):
            return False, "provider_inactive", entitlements
        if not int(provider["verified"] or 0) or not int(provider["listing_enabled"] or 0):
            return False, "provider_not_approved", entitlements
        if provider["status"] != "available" or not int(provider["request_enabled"] or 0):
            return False, "provider_unavailable", entitlements
        if not entitlements["allowed"]:
            return False, "subscription_inactive", entitlements
        limit = entitlements["monthlyResponses"]
        if limit:
            month = self.now.strftime("%Y-%m")
            count = self.con.execute(
                """SELECT COUNT(*) n FROM request_dispatches
                WHERE provider_id=? AND status IN ('notified','opened','offered','accepted')
                AND substr(COALESCE(notified_at,created_at),1,7)=?""",
                (provider_id, month),
            ).fetchone()["n"]
            if int(count or 0) >= limit:
                return False, "monthly_response_limit", entitlements
        return True, "", entitlements


class ContactConsentService:
    CHANNELS = {"chat", "whatsapp", "call"}

    def __init__(self, con, *, now: datetime | None = None, lifetime_days: int = 90):
        self.con = con
        self.now = now or utcnow()
        self.lifetime_days = max(1, int(lifetime_days))

    def set_channel(
        self,
        request_id: str,
        user_id: str,
        provider_id: str,
        channel: str,
        granted: bool,
    ) -> dict[str, Any]:
        if channel not in self.CHANNELS:
            raise DomainError("invalid_contact_channel")
        request = self.con.execute(
            "SELECT user_id,accepted_provider_id FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not request:
            raise DomainError("request_not_found", 404)
        if request["user_id"] != user_id or request["accepted_provider_id"] != provider_id:
            raise DomainError("contact_consent_not_allowed", 403)
        status = "granted" if granted else "revoked"
        expires = self.now + timedelta(days=self.lifetime_days) if granted else None
        consent_id = public_id("consent")
        self.con.execute(
            """INSERT INTO contact_consents(
            id,request_id,user_id,provider_id,channel,status,granted_at,expires_at,revoked_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(request_id,provider_id,channel) DO UPDATE SET
            user_id=excluded.user_id,status=excluded.status,granted_at=excluded.granted_at,
            expires_at=excluded.expires_at,revoked_at=excluded.revoked_at,updated_at=CURRENT_TIMESTAMP""",
            (
                consent_id, request_id, user_id, provider_id, channel, status,
                iso(self.now) if granted else "", iso(expires) if expires else "",
                "" if granted else iso(self.now),
            ),
        )
        return self.summary(request_id, provider_id)

    def allowed(self, request_id: str, provider_id: str, channel: str) -> bool:
        if channel not in self.CHANNELS:
            return False
        row = self.con.execute(
            """SELECT status,expires_at FROM contact_consents
            WHERE request_id=? AND provider_id=? AND channel=?""",
            (request_id, provider_id, channel),
        ).fetchone()
        if not row or row["status"] != "granted":
            return False
        expires = parse_datetime(row["expires_at"])
        return not expires or expires >= self.now

    def summary(self, request_id: str, provider_id: str) -> dict[str, Any]:
        rows = list(self.con.execute(
            """SELECT channel,status,granted_at,expires_at,revoked_at FROM contact_consents
            WHERE request_id=? AND provider_id=?""",
            (request_id, provider_id),
        ))
        result: dict[str, Any] = {channel: False for channel in self.CHANNELS}
        result["updatedAt"] = ""
        result["expiresAt"] = ""
        for row in rows:
            granted = row["status"] == "granted"
            expires = parse_datetime(row["expires_at"])
            if expires and expires < self.now:
                granted = False
            result[row["channel"]] = granted
            result["updatedAt"] = max(result["updatedAt"], row["granted_at"] or row["revoked_at"] or "")
            if granted and row["expires_at"]:
                result["expiresAt"] = max(result["expiresAt"], row["expires_at"])
        return result


class RankingService:
    WEIGHTS = {
        "match": 0.30,
        "availability": 0.20,
        "quality": 0.20,
        "response": 0.10,
        "profile": 0.08,
        "verification": 0.05,
        "recency": 0.03,
        "plan": 0.04,
    }
    PLAN_PRIORITY = {
        "foundation_12m": 0.10,
        "basic_6m": 0.35,
        "basic_12m": 0.40,
        "professional_12m": 0.85,
        "business_12m": 1.00,
    }

    @classmethod
    def exact_service_match(cls, request: dict[str, Any], provider: dict[str, Any]) -> bool:
        value = str(request.get("service_value") or request.get("serviceValue") or "")
        requested_cat, requested_service = (value.split("|", 1) + [""])[:2] if "|" in value else ("", value)
        for service in load(provider.get("services"), []) if isinstance(provider.get("services"), str) else provider.get("services", []):
            if not service.get("active", True):
                continue
            if requested_service:
                if service.get("serviceId") == requested_service and (
                    not requested_cat or service.get("catId") == requested_cat
                ):
                    return True
            elif requested_cat and service.get("catId") == requested_cat:
                return True
        return False

    @classmethod
    def area_match(cls, request: dict[str, Any], provider: dict[str, Any]) -> bool:
        request_wilayah = str(request.get("wilayah") or "").strip()
        request_gov = str(request.get("gov") or "").strip()
        areas = load(provider.get("areas"), []) if isinstance(provider.get("areas"), str) else provider.get("areas", [])
        provider_areas = {str(value).strip() for value in [*areas, provider.get("wilayah"), provider.get("gov")] if value}
        if request_wilayah:
            return request_wilayah in provider_areas or (request_gov and request_gov in provider_areas)
        return not request_gov or request_gov in provider_areas

    @classmethod
    def availability_score(cls, provider: dict[str, Any], now: datetime) -> float:
        status = provider.get("status")
        if status == "unavailable":
            return 0.0
        score = 1.0 if status == "available" else 0.55
        availability = load(provider.get("availability"), {})
        if not availability:
            return score
        days = availability.get("days") or []
        day_key = str(now.weekday())
        if days and day_key not in {str(day) for day in days}:
            return 0.0
        start, end = availability.get("start"), availability.get("end")
        if start and end:
            current = now.strftime("%H:%M")
            if not (str(start) <= current <= str(end)):
                return 0.25
        return score

    @classmethod
    def score(cls, request: dict[str, Any], provider: dict[str, Any], plan_id: str, now: datetime) -> tuple[float, dict[str, float]]:
        if not cls.exact_service_match(request, provider) or not cls.area_match(request, provider):
            return 0.0, {key: 0.0 for key in cls.WEIGHTS}
        services = load(provider.get("services"), []) if isinstance(provider.get("services"), str) else provider.get("services", [])
        areas = load(provider.get("areas"), []) if isinstance(provider.get("areas"), str) else provider.get("areas", [])
        work_images = load(provider.get("work_images"), []) if isinstance(provider.get("work_images"), str) else provider.get("workImages", [])
        profile_fields = [provider.get("image_path") or provider.get("imagePath"), provider.get("bio"), provider.get("hours"), services, areas, work_images]
        profile = sum(bool(value) for value in profile_fields) / len(profile_fields)
        response_score = float(provider.get("response_score") or provider.get("responseScore") or 70) / 100
        response_minutes = int(provider.get("response_minutes") or provider.get("responseMinutes") or 30)
        response = max(0.0, min(1.0, (response_score + max(0, 1 - response_minutes / 120)) / 2))
        created = parse_datetime(provider.get("created_at") or provider.get("createdAt"))
        age = max(0, (now - created).days) if created else 730
        breakdown = {
            "match": 1.0,
            "availability": cls.availability_score(provider, now),
            "quality": max(0.0, min(1.0, float(provider.get("quality_score") or provider.get("qualityScore") or 0) / 100)),
            "response": response,
            "profile": profile,
            "verification": 1.0 if int(provider.get("verified") or 0) else 0.0,
            "recency": max(0.0, 1 - age / 730),
            "plan": cls.PLAN_PRIORITY.get(plan_id, 0.0),
        }
        total = sum(breakdown[key] * cls.WEIGHTS[key] for key in cls.WEIGHTS)
        return round(total * 100, 3), {key: round(value, 4) for key, value in breakdown.items()}


class RequestMarketplace:
    def __init__(self, con, *, now: datetime | None = None, expansion_minutes: int = 20, min_offers: int = 2):
        self.con = con
        self.now = now or utcnow()
        self.expansion_minutes = max(5, int(expansion_minutes))
        self.min_offers = max(1, int(min_offers))

    def schedule(self, request_id: str) -> list[dict[str, Any]]:
        request_row = self.con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
        if not request_row:
            raise DomainError("request_not_found", 404)
        request = row_dict(request_row)
        entitlements = EntitlementService(self.con, now=self.now)
        ranked: list[dict[str, Any]] = []
        for provider_row in self.con.execute(
            """SELECT * FROM providers WHERE active=1 AND status!='unavailable'
            AND COALESCE(listing_enabled,1)=1 AND COALESCE(request_enabled,1)=1"""
        ):
            provider = row_dict(provider_row)
            allowed, reason, grants = entitlements.can_receive(provider["id"])
            if not allowed:
                continue
            score, breakdown = RankingService.score(request, provider, grants["planId"], self.now)
            if score <= 0:
                continue
            ranked.append({
                "providerId": provider["id"],
                "score": score,
                "breakdown": breakdown,
                "delay": grants["leadDelayMinutes"],
                "planId": grants["planId"],
            })
        ranked.sort(key=lambda item: (-item["score"], item["providerId"]))
        ranked = ranked[:10]
        self.con.execute(
            "DELETE FROM request_dispatches WHERE request_id=? AND status='scheduled'", (request_id,)
        )
        expansion_at = self.now + timedelta(minutes=self.expansion_minutes)
        for index, item in enumerate(ranked):
            wave = 1 if index < 5 else 2
            base = self.now if wave == 1 else expansion_at
            release = base + timedelta(minutes=item["delay"])
            self.con.execute(
                """INSERT INTO request_dispatches(
                id,request_id,provider_id,rank,score,score_breakdown,wave,release_at,status)
                VALUES(?,?,?,?,?,?,?,?, 'scheduled')
                ON CONFLICT(request_id,provider_id) DO UPDATE SET rank=excluded.rank,
                score=excluded.score,score_breakdown=excluded.score_breakdown,wave=excluded.wave,
                release_at=excluded.release_at,status=CASE
                WHEN request_dispatches.status IN ('notified','opened','offered','accepted')
                THEN request_dispatches.status ELSE 'scheduled' END""",
                (
                    public_id("dispatch"), request_id, item["providerId"], index + 1,
                    item["score"], dump(item["breakdown"]), wave, iso(release),
                ),
            )
        if ranked:
            self.con.execute(
                """UPDATE customer_requests SET status='matching',marketplace_status='scheduled',
                dispatch_started_at=?,expansion_at=?,ranking_version=?,waitlisted=0,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (iso(self.now), iso(expansion_at), RANKING_VERSION, request_id),
            )
        else:
            self.con.execute(
                """UPDATE customer_requests SET status='unavailable',marketplace_status='unavailable',
                matching_provider_ids='[]',waitlisted=1,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (request_id,),
            )
        return ranked

    def release_due(self, request_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [iso(self.now)]
        if request_id:
            params.append(request_id)
            rows = list(self.con.execute(
                """SELECT d.*,r.service_name,r.service_value,r.gov,r.wilayah,r.offers,
                r.expansion_at,r.status request_status FROM request_dispatches d
                JOIN customer_requests r ON r.id=d.request_id
                WHERE d.status='scheduled' AND d.release_at<=? AND d.request_id=?
                ORDER BY d.request_id,d.rank""",
                params,
            ))
        else:
            rows = list(self.con.execute(
                """SELECT d.*,r.service_name,r.service_value,r.gov,r.wilayah,r.offers,
                r.expansion_at,r.status request_status FROM request_dispatches d
                JOIN customer_requests r ON r.id=d.request_id
                WHERE d.status='scheduled' AND d.release_at<=?
                ORDER BY d.request_id,d.rank""",
                params,
            ))
        released = []
        by_request: dict[str, list[Any]] = {}
        for row in rows:
            by_request.setdefault(row["request_id"], []).append(row)
        for rid, candidates in by_request.items():
            request_row = candidates[0]
            if request_row["request_status"] in {"accepted", "in_progress", "completed", "cancelled", "deleted", "expired"}:
                continue
            offers = load(request_row["offers"], [])
            current_ids = load(
                self.con.execute("SELECT matching_provider_ids FROM customer_requests WHERE id=?", (rid,)).fetchone()[0],
                [],
            )
            for row in candidates:
                if int(row["wave"] or 1) == 2:
                    expansion = parse_datetime(row["expansion_at"])
                    if not expansion or self.now < expansion or len(offers) >= self.min_offers:
                        continue
                self.con.execute(
                    """UPDATE request_dispatches SET status='notified',notified_at=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='scheduled'""",
                    (iso(self.now), row["id"]),
                )
                if self.con.execute("SELECT changes()").fetchone()[0] != 1:
                    continue
                if row["provider_id"] not in current_ids:
                    current_ids.append(row["provider_id"])
                released.append({
                    "requestId": rid,
                    "providerId": row["provider_id"],
                    "rank": row["rank"],
                    "score": row["score"],
                    "serviceName": row["service_name"] or row["service_value"],
                    "area": row["wilayah"] or row["gov"],
                })
            self.con.execute(
                """UPDATE customer_requests SET matching_provider_ids=?,marketplace_status=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (dump(current_ids), "notified" if current_ids else "scheduled", rid),
            )
        return released


class PaymentAdapter:
    def __init__(self, con, *, environment: dict[str, str] | None = None, now: datetime | None = None):
        self.con = con
        self.environment = environment or os.environ
        self.now = now or utcnow()
        self.gateway = self.environment.get("KHADAMATI_PAYMENT_GATEWAY", "manual").strip().lower() or "manual"
        self.checkout_url = self.environment.get("KHADAMATI_PAYMENT_CHECKOUT_URL", "").strip()
        self.webhook_secret = self.environment.get("KHADAMATI_PAYMENT_WEBHOOK_SECRET", "").strip()

    @property
    def configured(self) -> bool:
        return self.gateway not in {"", "manual", "disabled"} and bool(self.checkout_url and self.webhook_secret)

    def create_intent(self, subscription_id: str, provider_id: str, *, client_amount: Any = None) -> dict[str, Any]:
        subscription = self.con.execute(
            "SELECT * FROM subscriptions WHERE id=? AND provider_id=?", (subscription_id, provider_id)
        ).fetchone()
        if not subscription:
            raise DomainError("subscription_not_found", 404)
        expected = as_money(subscription["amount"])
        if client_amount not in (None, "") and as_money(client_amount) != expected:
            raise DomainError("payment_amount_mismatch", 409)
        if subscription["status"] != "pending_payment":
            raise DomainError("subscription_not_waiting_for_payment", 409)
        payment_id = public_id("pay")
        external_id = public_id("checkout")
        self.con.execute(
            """INSERT INTO payments(
            id,provider_id,subscription_id,kind,amount,method,status,note,currency,
            external_id,gateway,metadata,updated_at)
            VALUES(?,?,?,'subscription',?,?, 'pending','',?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                payment_id, provider_id, subscription_id, float(expected),
                self.gateway if self.configured else "manual",
                subscription["currency"] or OMR, external_id,
                self.gateway if self.configured else "manual",
                dump({"serverPriced": True}),
            ),
        )
        result = {
            "paymentId": payment_id,
            "reference": external_id,
            "amount": float(expected),
            "currency": subscription["currency"] or OMR,
            "status": "pending",
            "requiresAdminApproval": not self.configured,
            "gatewayConfigured": self.configured,
        }
        if self.configured:
            separator = "&" if "?" in self.checkout_url else "?"
            result["checkoutUrl"] = f"{self.checkout_url}{separator}reference={external_id}"
        return result

    def verify_webhook(self, raw_body: bytes, signature: str) -> dict[str, Any]:
        if not self.webhook_secret:
            raise DomainError("payment_webhook_not_configured", 503)
        expected = hmac.new(self.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        supplied = str(signature or "").removeprefix("sha256=")
        if not hmac.compare_digest(expected, supplied):
            raise DomainError("invalid_webhook_signature", 401)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("invalid_webhook_payload") from exc
        event_id = str(payload.get("eventId") or "").strip()
        reference = str(payload.get("reference") or "").strip()
        if not event_id or not reference:
            raise DomainError("webhook_reference_required")
        old_event = self.con.execute("SELECT id FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        if old_event:
            return {"ok": True, "duplicate": True}
        payment = self.con.execute("SELECT * FROM payments WHERE external_id=?", (reference,)).fetchone()
        if not payment:
            raise DomainError("payment_not_found", 404)
        amount = as_money(payload.get("amount"))
        currency = str(payload.get("currency") or "").upper()
        if amount != as_money(payment["amount"]) or currency != str(payment["currency"] or OMR).upper():
            raise DomainError("payment_amount_mismatch", 409)
        status = str(payload.get("status") or "").lower()
        if status not in {"paid", "failed", "cancelled", "refunded"}:
            raise DomainError("invalid_payment_status")
        current_status = str(payment["status"] or "pending").lower()
        if current_status == "refunded" and status != "refunded":
            raise DomainError("invalid_payment_transition", 409)
        if status == "refunded" and current_status != "paid":
            raise DomainError("invalid_payment_transition", 409)
        if current_status == "paid" and status in {"failed", "cancelled"}:
            raise DomainError("invalid_payment_transition", 409)
        self.con.execute(
            """INSERT INTO webhook_events(
            id,provider,event_id,signature_valid,payload_hash,processed)
            VALUES(?,?,?,?,?,1)""",
            (
                public_id("wh"), self.gateway, event_id, 1,
                hashlib.sha256(raw_body).hexdigest(),
            ),
        )
        verified_at = iso(self.now) if status == "paid" else ""
        self.con.execute(
            """UPDATE payments SET status=?,verified_at=?,failure_code=?,metadata=?,
            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (
                status, verified_at, str(payload.get("failureCode") or "")[:120],
                dump({"eventId": event_id}), payment["id"],
            ),
        )
        if status == "paid":
            subscription = SubscriptionService(self.con, now=self.now).activate(
                payment["subscription_id"], payment_id=payment["id"], actor=f"webhook:{self.gateway}"
            )
            self._invoice(payment, subscription)
        elif status == "refunded":
            SubscriptionService(self.con, now=self.now).refund(
                payment["subscription_id"], actor=f"webhook:{self.gateway}"
            )
        return {"ok": True, "paymentId": payment["id"], "status": status}

    def confirm_manual(self, payment_id: str, *, actor: str = "admin") -> dict[str, Any]:
        payment = self.con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if not payment:
            raise DomainError("payment_not_found", 404)
        if payment["status"] == "paid":
            return row_dict(payment)
        if payment["status"] != "pending":
            raise DomainError("invalid_payment_transition", 409)
        self.con.execute(
            "UPDATE payments SET status='paid',verified_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (iso(self.now), payment_id),
        )
        subscription = SubscriptionService(self.con, now=self.now).activate(
            payment["subscription_id"], payment_id=payment_id, actor=actor
        )
        self._invoice(payment, subscription)
        return row_dict(self.con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone())

    def _invoice(self, payment: Any, subscription: dict[str, Any]) -> None:
        number = f"KHA-{self.now.strftime('%Y%m')}-{secrets.token_hex(3).upper()}"
        self.con.execute(
            """INSERT OR IGNORE INTO invoices(
            id,payment_id,subscription_id,provider_id,number,currency,subtotal,total,status,issued_at,paid_at,metadata)
            VALUES(?,?,?,?,?,?,?,?, 'paid',?,?,?)""",
            (
                public_id("inv"), payment["id"], subscription["id"], subscription["provider_id"],
                number, payment["currency"] or OMR, payment["amount"], payment["amount"],
                iso(self.now), iso(self.now), dump({"source": payment["method"]}),
            ),
        )


class OTPService:
    def __init__(
        self,
        con,
        *,
        environment: dict[str, str] | None = None,
        now: datetime | None = None,
        deliver: Callable[[str, str], bool] | None = None,
    ):
        self.con = con
        self.environment = environment or os.environ
        self.now = now or utcnow()
        self.deliver = deliver
        self.app_env = self.environment.get("KHADAMATI_ENV", "development").lower()
        self.pepper = self.environment.get("KHADAMATI_OTP_PEPPER", "")
        self.ttl_minutes = max(2, int(self.environment.get("KHADAMATI_OTP_TTL_MINUTES", "5")))
        self.max_attempts = max(3, int(self.environment.get("KHADAMATI_OTP_MAX_ATTEMPTS", "5")))
        self.hourly_limit = max(1, int(self.environment.get("KHADAMATI_OTP_HOURLY_LIMIT", "5")))

    def request(self, phone: str, purpose: str, target_kind: str = "user") -> dict[str, Any]:
        phone = normalized_phone(phone)
        if len(phone) < 11:
            raise DomainError("valid_phone_required")
        since = iso(self.now - timedelta(hours=1))
        count = self.con.execute(
            "SELECT COUNT(*) n FROM otp_challenges WHERE phone=? AND created_at>=?",
            (phone, since),
        ).fetchone()["n"]
        if int(count or 0) >= self.hourly_limit:
            raise DomainError("otp_rate_limited", 429)
        challenge_id = public_id("otp")
        development_code = self.environment.get("KHADAMATI_DEV_OTP_CODE", "").strip()
        code = development_code if self.app_env != "production" and development_code else f"{secrets.randbelow(1_000_000):06d}"
        expires = self.now + timedelta(minutes=self.ttl_minutes)
        delivery_status = "pending"
        delivered = False
        if self.deliver:
            delivered = bool(self.deliver(phone, code))
            delivery_status = "sent" if delivered else "failed"
        elif self.app_env != "production" and development_code:
            delivered = True
            delivery_status = "development"
        self.con.execute(
            """INSERT INTO otp_challenges(
            id,phone,purpose,target_kind,code_hash,attempts,max_attempts,expires_at,delivery_status)
            VALUES(?,?,?,?,?,0,?,?,?)""",
            (
                challenge_id, phone, purpose[:80], target_kind[:40],
                self._hash(challenge_id, code), self.max_attempts, iso(expires), delivery_status,
            ),
        )
        if not delivered:
            raise DomainError("otp_delivery_unavailable", 503)
        result = {
            "challengeId": challenge_id,
            "expiresInSeconds": self.ttl_minutes * 60,
            "maxAttempts": self.max_attempts,
            "delivery": delivery_status,
        }
        if self.app_env != "production" and development_code:
            result["developmentCode"] = code
        return result

    def verify(self, challenge_id: str, code: str) -> dict[str, Any]:
        row = self.con.execute("SELECT * FROM otp_challenges WHERE id=?", (challenge_id,)).fetchone()
        if not row:
            raise DomainError("otp_not_found", 404)
        if row["verified_at"]:
            raise DomainError("otp_already_used", 409)
        if int(row["attempts"] or 0) >= int(row["max_attempts"] or self.max_attempts):
            raise DomainError("otp_attempts_exceeded", 429)
        expires = parse_datetime(row["expires_at"])
        if not expires or expires < self.now:
            raise DomainError("otp_expired", 410)
        if not hmac.compare_digest(self._hash(challenge_id, str(code)), row["code_hash"]):
            self.con.execute("UPDATE otp_challenges SET attempts=attempts+1 WHERE id=?", (challenge_id,))
            raise DomainError("otp_invalid", 403)
        self.con.execute("UPDATE otp_challenges SET verified_at=? WHERE id=?", (iso(self.now), challenge_id))
        return {"ok": True, "phone": row["phone"], "purpose": row["purpose"], "targetKind": row["target_kind"]}

    def _hash(self, challenge_id: str, code: str) -> str:
        return hashlib.sha256(f"{challenge_id}|{code}|{self.pepper}".encode("utf-8")).hexdigest()


def run_subscription_migration_v1(con) -> dict[str, Any]:
    existing = con.execute("SELECT value FROM settings WHERE key=?", (MIGRATION_KEY,)).fetchone()
    if existing:
        return load(existing["value"], {"alreadyApplied": True})
    PlanCatalog.seed(con)
    mapped_subscriptions = 0
    for row in list(con.execute("SELECT id,package_id,status FROM subscriptions")):
        old_plan = row["package_id"]
        new_plan = LEGACY_PLAN_MAP.get(old_plan, old_plan if old_plan in PLAN_IDS else "basic_6m")
        old_status = str(row["status"] or "pending")
        status_map = {
            "pending": "pending_payment",
            "near_expiry": "expiring",
            "near-end": "expiring",
            "stopped": "suspended",
            "inactive": "expired",
        }
        new_status = status_map.get(old_status, old_status if old_status in SUBSCRIPTION_STATES else "active")
        if new_plan == "foundation_12m" and new_status in {"active", "foundation"}:
            new_status = "foundation"
        con.execute(
            """UPDATE subscriptions SET package_id=?,legacy_package_id=CASE
            WHEN legacy_package_id='' THEN ? ELSE legacy_package_id END,status=?,currency='OMR',
            grace_days=COALESCE(grace_days,14),updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (new_plan, old_plan if old_plan != new_plan else "", new_status, row["id"]),
        )
        mapped_subscriptions += 1
    created_subscriptions = 0
    today = utcnow()
    for provider in list(con.execute("SELECT * FROM providers")):
        old_plan = str(provider["package_id"] or "")
        plan_id = LEGACY_PLAN_MAP.get(old_plan, old_plan if old_plan in PLAN_IDS else "basic_6m")
        con.execute("UPDATE providers SET package_id=? WHERE id=?", (plan_id, provider["id"]))
        subscription = con.execute(
            "SELECT id FROM subscriptions WHERE provider_id=? ORDER BY created_at DESC LIMIT 1",
            (provider["id"],),
        ).fetchone()
        if subscription:
            continue
        plan = PlanCatalog.get(con, plan_id, False)
        start = parse_datetime(provider["subscription_start"]) or parse_datetime(provider["created_at"]) or today
        end = parse_datetime(provider["subscription_until"]) or (start + timedelta(days=int(plan["duration_days"])))
        state = "foundation" if plan_id == "foundation_12m" else "active"
        if end < today - timedelta(days=14):
            state = "expired"
        elif end < today:
            state = "grace"
        elif (end.date() - today.date()).days <= 30:
            state = "expiring"
        subscription_id = public_id("subm")
        con.execute(
            """INSERT INTO subscriptions(
            id,provider_id,package_id,amount,status,start_date,end_date,note,currency,
            grace_days,legacy_package_id,activated_at,grace_until,metadata,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                subscription_id, provider["id"], plan_id, plan["price"], state,
                start.date().isoformat(), end.date().isoformat(), "Migrated from provider profile",
                OMR, 14, old_plan if old_plan != plan_id else "", iso(start),
                (end + timedelta(days=14)).date().isoformat(), dump({"migration": MIGRATION_KEY}),
            ),
        )
        if plan_id == "foundation_12m" and int(provider["verified"] or 0):
            phone = normalized_phone(provider["phone"])
            commercial = str(provider["commercial_no"] or "").strip().casefold()
            fingerprint = hashlib.sha256(f"{phone}|{commercial}".encode("utf-8")).hexdigest()
            con.execute(
                """INSERT OR IGNORE INTO foundation_claims(
                id,provider_id,phone,commercial_no,fingerprint,subscription_id)
                VALUES(?,?,?,?,?,?)""",
                (public_id("fndm"), provider["id"], phone, commercial, fingerprint, subscription_id),
            )
        created_subscriptions += 1
    migrated_consents = 0
    consent_service = ContactConsentService(con)
    for request in list(con.execute(
        """SELECT id,user_id,accepted_provider_id,contact_consent FROM customer_requests
        WHERE COALESCE(accepted_provider_id,'')!=''"""
    )):
        legacy = load(request["contact_consent"], {})
        for channel in ContactConsentService.CHANNELS:
            if legacy.get(channel):
                try:
                    consent_service.set_channel(
                        request["id"], request["user_id"], request["accepted_provider_id"], channel, True
                    )
                    migrated_consents += 1
                except DomainError:
                    pass
    changes = SubscriptionService(con).synchronize_all()
    summary = {
        "version": 1,
        "completedAt": iso(),
        "mappedSubscriptions": mapped_subscriptions,
        "createdSubscriptions": created_subscriptions,
        "migratedConsents": migrated_consents,
        "accessChanges": len(changes),
        "activePlans": list(PLAN_IDS),
    }
    con.execute("INSERT INTO settings(key,value) VALUES(?,?)", (MIGRATION_KEY, dump(summary)))
    return summary
