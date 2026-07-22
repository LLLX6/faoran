from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import http.client
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from datetime import datetime, timedelta, UTC
from contextlib import contextmanager
import base64
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import zipfile

from khadamati_domain import (
    MIGRATION_KEY,
    OMR,
    PLAN_IDS,
    POLICY_VERSION,
    SUPPORT_EMAIL,
    ContactConsentService,
    DomainError,
    EntitlementService,
    OTPService,
    PaymentAdapter,
    PlanCatalog,
    RankingService,
    RequestMarketplace,
    SubscriptionService,
    run_subscription_migration_v1,
)

try:
    from pywebpush import WebPushException, webpush
except ImportError:
    WebPushException = Exception
    webpush = None

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
UPLOAD_DIR = Path(os.environ.get("KHADAMATI_UPLOAD_DIR") or os.environ.get("FORAN_UPLOAD_DIR") or (PUBLIC_DIR / "uploads"))
_legacy_db = BASE_DIR / "foran.sqlite3"
DB_PATH = Path(os.environ.get("KHADAMATI_DB_PATH") or os.environ.get("FORAN_DB_PATH") or (_legacy_db if _legacy_db.exists() else BASE_DIR / "khadamati.sqlite3"))
APP_ENV = os.environ.get("KHADAMATI_ENV", "development").strip().lower() or "development"
INITIAL_ADMIN_CODE = (
    os.environ.get("KHADAMATI_ADMIN_CODE")
    or os.environ.get("FORAN_ADMIN_CODE")
    or (os.environ.get("KHADAMATI_DEV_ADMIN_CODE") if APP_ENV != "production" else "")
    or ""
)
DEFAULT_ALLOWED_ORIGINS = {
    "https://lllx6.github.io",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
}
ALLOWED_ORIGINS = {
    item.strip().rstrip("/")
    for item in os.environ.get("KHADAMATI_ALLOWED_ORIGINS", ",".join(sorted(DEFAULT_ALLOWED_ORIGINS))).split(",")
    if item.strip()
}
SESSION_DAYS = int(os.environ.get("KHADAMATI_SESSION_DAYS", "30"))
PUBLIC_APP_URL = os.environ.get("KHADAMATI_PUBLIC_URL", "https://lllx6.github.io/Khadamati/").rstrip("/") + "/"
LOGIN_MAX_ATTEMPTS = max(3, int(os.environ.get("KHADAMATI_LOGIN_MAX_ATTEMPTS", "5")))
LOGIN_LOCK_MINUTES = max(1, int(os.environ.get("KHADAMATI_LOGIN_LOCK_MINUTES", "15")))
MEDIA_URL_TTL_SECONDS = max(60, int(os.environ.get("KHADAMATI_MEDIA_URL_TTL_SECONDS", "900")))
MEDIA_SIGNING_KEY = (
    os.environ.get("KHADAMATI_MEDIA_SIGNING_KEY")
    or os.environ.get("KHADAMATI_OTP_PEPPER")
    or secrets.token_urlsafe(32)
)
DEFAULT_JSON_LIMIT = max(65_536, int(os.environ.get("KHADAMATI_MAX_JSON_BYTES", "1000000")))
JSON_LIMITS = {
    "/api/provider-requests": 60_000_000,
    "/api/provider/profile": 60_000_000,
    "/api/provider/work-images": 50_000_000,
    "/api/provider/documents": 22_000_000,
    "/api/provider/image": 4_000_000,
    "/api/user/profile": 4_000_000,
    "/api/user/requests": 20_000_000,
    "/api/request/collaboration": 10_000_000,
    "/api/admin/ads": 6_000_000,
}

ALL_PERMISSIONS = [
    "view_reports",
    "manage_providers",
    "review_requests",
    "manage_quality",
    "manage_subscriptions",
    "manage_finance",
    "manage_settings",
    "manage_admins",
    "manage_team",
    "manage_consent",
    "manage_campaigns",
    "manage_audit",
    "backup",
]
ROLE_PERMISSIONS = {
    "super_admin": ALL_PERMISSIONS,
    "admin": [
        "view_reports", "manage_providers", "review_requests", "manage_quality",
        "manage_subscriptions", "manage_finance", "manage_settings", "manage_team",
        "manage_consent", "manage_campaigns", "manage_audit", "backup",
    ],
    "owner": ALL_PERMISSIONS,
    "manager": ["view_reports", "manage_providers", "review_requests", "manage_quality", "manage_subscriptions", "manage_finance", "manage_team", "manage_consent", "backup"],
    "support": ["view_reports", "review_requests", "manage_quality"],
    "finance": ["view_reports", "manage_subscriptions", "manage_finance", "backup"],
    "user": [],
    "provider": [],
    "provider_owner": [],
    "provider_manager": [],
    "provider_staff": [],
}
PROVIDER_ROLE_PERMISSIONS = {
    "provider_owner": {"profile", "media", "documents", "subscription", "team", "branches", "requests"},
    "provider_manager": {"profile", "media", "documents", "team", "branches", "requests"},
    "provider_staff": {"requests"},
}

IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DOCUMENT_MIMES = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
CHAT_MIMES = {
    **IMAGE_MIMES,
    "audio/webm": "webm",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
}
VIDEO_MIMES = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov"}


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=12)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def slug(prefix):
    return f"{prefix}_{secrets.token_hex(16)}"


def hash_secret(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def hash_pin(value):
    salt = secrets.token_hex(16)
    rounds = 160_000
    digest = hashlib.pbkdf2_hmac("sha256", str(value).encode("utf-8"), salt.encode("ascii"), rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"


def verify_secret(value, encoded):
    encoded = str(encoded or "")
    if encoded.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt, digest = encoded.split("$", 3)
            actual = hashlib.pbkdf2_hmac(
                "sha256", str(value).encode("utf-8"), salt.encode("ascii"), int(rounds)
            ).hex()
            return hmac.compare_digest(actual, digest)
        except (TypeError, ValueError):
            return False
    return hmac.compare_digest(hash_secret(value), encoded)


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


def safe_text(value, limit=240):
    return str(value or "").strip()[:limit]


def finite_number(value, default=0.0, *, minimum=None, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        raise DomainError("invalid_number", 400)
    if minimum is not None and number < minimum:
        raise DomainError("number_out_of_range", 400)
    if maximum is not None and number > maximum:
        raise DomainError("number_out_of_range", 400)
    return number


def bounded_int(value, default=0, *, minimum=None, maximum=None):
    number = finite_number(value, default, minimum=minimum, maximum=maximum)
    if not number.is_integer():
        raise DomainError("invalid_integer", 400)
    return int(number)


def strict_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise DomainError("invalid_boolean", 400)


def normalized_location(value):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise DomainError("invalid_location", 400)
    if value.get("lat") in (None, "") or value.get("lng") in (None, ""):
        return {}
    lat = finite_number(value.get("lat"), minimum=-90, maximum=90)
    lng = finite_number(value.get("lng"), minimum=-180, maximum=180)
    result = {"lat": lat, "lng": lng}
    if value.get("accuracy") not in (None, ""):
        result["accuracy"] = finite_number(value.get("accuracy"), minimum=0, maximum=100_000)
    if value.get("updatedAt"):
        result["updatedAt"] = safe_text(value.get("updatedAt"), 50)
    return result


def normalized_provider_services(
    con, value, *, limit, category_limit=1, fallback_price=0, default_areas=None
):
    if not isinstance(value, list):
        raise DomainError("services_must_be_list", 400)
    services = []
    seen = set()
    categories = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        cat_id = safe_text(item.get("catId"), 80)
        service_id = safe_text(item.get("serviceId"), 80)
        key = (cat_id, service_id)
        if not cat_id or not service_id or key in seen:
            continue
        exists = con.execute(
            """SELECT s.id FROM services s JOIN categories c ON c.id=s.category_id
            WHERE s.id=? AND s.category_id=? AND s.active=1 AND c.active=1""",
            (service_id, cat_id),
        ).fetchone()
        if not exists:
            raise DomainError("service_not_found", 400, f"{cat_id}|{service_id}")
        item_areas = item.get("areas", default_areas or [])
        if not isinstance(item_areas, list):
            item_areas = default_areas or []
        areas = list(dict.fromkeys(safe_text(area, 80) for area in item_areas if safe_text(area, 80)))[:50]
        services.append(
            {
                "id": safe_text(item.get("id"), 100) or slug("ps"),
                "catId": cat_id,
                "serviceId": service_id,
                "priceFrom": finite_number(
                    item.get("priceFrom", fallback_price), minimum=0, maximum=1_000_000
                ),
                "active": bool(item.get("active", True)),
                "areas": areas,
            }
        )
        seen.add(key)
        categories.add(cat_id)
    if len(services) > max(1, int(limit)):
        raise DomainError("service_limit_exceeded", 409)
    if len(categories) > max(1, int(category_limit)):
        raise DomainError("provider_category_limit", 409)
    return services


def phone_matches(stored, normalized):
    """Compare current and legacy phone formats without forcing a data reset."""
    return bool(normalized) and normalize_phone(stored) == normalized


def iso_date(days=0):
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")


def iso_datetime(minutes=0, days=0):
    return (datetime.now(UTC) + timedelta(minutes=minutes, days=days)).isoformat()


def seed_service(service_id, icon, ar, en):
    return {"id": service_id, "icon": icon, "ar": ar, "en": en, "active": 1}


def seed_category(cat_id, icon, ar, en, services):
    return {"id": cat_id, "icon": icon, "ar": ar, "en": en, "active": 1, "services": services}


SEED_CATEGORIES = [
    seed_category("homecare", "🏠", "صيانة المنزل", "Home maintenance", [
        seed_service("electrician", "⚡", "كهربائي", "Electrician"), seed_service("plumber", "🚿", "سباك", "Plumber"),
        seed_service("ac", "❄️", "صيانة مكيفات", "AC maintenance"), seed_service("appliances", "🔧", "صيانة أجهزة منزلية", "Home appliances"),
        seed_service("curtains", "🪟", "تركيب ستائر", "Curtain installation"), seed_service("furniture", "🪑", "تركيب أثاث", "Furniture assembly"),
        seed_service("paint", "🎨", "دهان", "Painting"), seed_service("gypsum", "◻️", "جبس وديكور", "Gypsum and decor"),
        seed_service("pest", "🐜", "مكافحة حشرات", "Pest control"), seed_service("tanks", "💧", "تنظيف خزانات", "Tank cleaning"),
        seed_service("doors", "🚪", "تصليح أبواب وأقفال", "Doors and locks"), seed_service("gardens", "🌿", "تنسيق حدائق", "Garden care"),
        seed_service("pools", "🏊", "صيانة مسابح", "Pool maintenance"), seed_service("satellite", "📡", "تركيب دش وستلايت", "Satellite installation"),
        seed_service("smart_home", "🏡", "أنظمة منزل ذكي", "Smart home systems"), seed_service("water_heater", "♨️", "صيانة سخانات", "Water heater repair"),
    ]),
    seed_category("cleaning", "🧼", "التنظيف", "Cleaning", [
        seed_service("home_clean", "🏡", "تنظيف منازل", "Home cleaning"), seed_service("apt_clean", "🏢", "تنظيف شقق", "Apartment cleaning"),
        seed_service("majlis", "🛋️", "تنظيف مجالس", "Majlis cleaning"), seed_service("sofa", "🛋️", "تنظيف كنب", "Sofa cleaning"),
        seed_service("carpet", "🧽", "تنظيف سجاد", "Carpet cleaning"), seed_service("post_build", "🏗️", "تنظيف بعد البناء", "Post-construction cleaning"),
        seed_service("office_clean", "🏬", "تنظيف مكاتب", "Office cleaning"), seed_service("facade", "🪟", "تنظيف واجهات", "Facade cleaning"),
        seed_service("deep_clean", "🧴", "تنظيف عميق", "Deep cleaning"), seed_service("kitchen_clean", "🍽️", "تنظيف مطابخ", "Kitchen cleaning"),
        seed_service("bath_clean", "🚿", "تنظيف دورات مياه", "Bathroom cleaning"), seed_service("mattress", "🛏️", "تنظيف مراتب", "Mattress cleaning"),
        seed_service("sterilize", "🛡️", "تعقيم", "Sanitization"), seed_service("maid_hourly", "⏱️", "عاملة بالساعة", "Hourly cleaner"),
    ]),
    seed_category("transport", "🚚", "النقل والتوصيل", "Moving and delivery", [
        seed_service("furniture_move", "🚚", "نقل أثاث", "Furniture moving"), seed_service("items_delivery", "📦", "توصيل أغراض", "Item delivery"),
        seed_service("within_wilayah", "🛻", "نقل داخل الولاية", "Within-wilayah moving"), seed_service("between_gov", "🛣️", "نقل بين المحافظات", "Inter-governorate moving"),
        seed_service("loading", "📦", "تحميل وتنزيل", "Loading and unloading"), seed_service("private_driver", "🚗", "سائق خاص", "Private driver"),
        seed_service("small_truck", "🚛", "شاحنة صغيرة", "Small truck"), seed_service("large_truck", "🚛", "شاحنة كبيرة", "Large truck"),
        seed_service("cold_delivery", "❄️", "توصيل مبرد", "Cold delivery"), seed_service("airport", "✈️", "توصيل مطار", "Airport transfer"),
        seed_service("school_bus", "🚌", "نقل مدارس", "School transport"), seed_service("heavy_equipment", "🏗️", "نقل معدات", "Equipment transport"),
        seed_service("parcel", "📮", "طرود ومستندات", "Parcels and documents"),
    ]),
    seed_category("construction", "🏗️", "البناء والمقاولات", "Construction", [
        seed_service("building", "🧱", "بناء", "Building"), seed_service("renovation", "🛠️", "ترميم", "Renovation"),
        seed_service("tiles", "⬜", "بلاط", "Tiles"), seed_service("marble", "▫️", "رخام", "Marble"),
        seed_service("aluminium", "🪟", "ألمنيوم", "Aluminium"), seed_service("metal", "⚒️", "حدادة", "Metalwork"),
        seed_service("carpentry", "🪚", "نجارة", "Carpentry"), seed_service("insulation", "🧱", "عزل", "Insulation"),
        seed_service("roof", "🏠", "صيانة أسطح", "Roof maintenance"), seed_service("glass", "🪟", "زجاج ومرايا", "Glass and mirrors"),
        seed_service("plaster", "📐", "لياسة", "Plastering"), seed_service("blocks", "🧱", "طابوق", "Block work"),
        seed_service("survey", "📏", "مساحة وتخطيط", "Surveying"), seed_service("engineering", "📋", "استشارة هندسية", "Engineering consultation"),
        seed_service("demolition", "🚧", "إزالة وهدم", "Demolition"),
    ]),
    seed_category("tech", "💻", "التقنية", "Technology", [
        seed_service("pc", "💻", "صيانة كمبيوتر", "Computer repair"), seed_service("phone_repair", "📱", "صيانة هواتف", "Phone repair"),
        seed_service("cameras", "📹", "كاميرات مراقبة", "Security cameras"), seed_service("networks", "🌐", "شبكات", "Networks"),
        seed_service("websites", "🧩", "برمجة مواقع", "Web development"), seed_service("design", "✏️", "تصميم", "Design"),
        seed_service("tech_support", "🧑‍💻", "دعم تقني", "Technical support"), seed_service("pos", "🧾", "أنظمة نقاط بيع", "Point-of-sale systems"),
        seed_service("printer", "🖨️", "طابعات", "Printers"), seed_service("data_recovery", "💾", "استرجاع بيانات", "Data recovery"),
        seed_service("apps", "📲", "تطبيقات", "Mobile apps"), seed_service("marketing", "📣", "تسويق رقمي", "Digital marketing"),
        seed_service("cyber", "🔐", "أمن معلومات", "Cybersecurity"), seed_service("apple", "🍏", "أجهزة أبل", "Apple devices"),
    ]),
    seed_category("cars", "🚘", "السيارات", "Cars", [
        seed_service("car_electric", "🔌", "كهرباء سيارات", "Car electrical"), seed_service("mechanic", "🔧", "ميكانيكي", "Mechanic"),
        seed_service("car_wash", "🧽", "غسيل سيارات", "Car wash"), seed_service("battery", "🔋", "تبديل بطارية", "Battery replacement"),
        seed_service("tires", "🛞", "تبديل إطارات", "Tire replacement"), seed_service("inspection", "🔍", "فحص سيارة", "Car inspection"),
        seed_service("tow", "🚨", "ونش", "Tow truck"), seed_service("polish", "✨", "تلميع", "Polishing"),
        seed_service("oil", "🛢️", "تبديل زيت", "Oil change"), seed_service("ac_car", "❄️", "مكيف سيارات", "Car AC"),
        seed_service("keys", "🗝️", "مفاتيح سيارات", "Car keys"), seed_service("tint", "🌗", "تظليل", "Window tinting"),
        seed_service("paintless", "🧲", "شفط صدمات", "Dent repair"), seed_service("diagnostics", "🧪", "فحص كمبيوتر", "Diagnostics"),
        seed_service("detailing", "🧼", "تنظيف داخلي", "Interior detailing"),
    ]),
    seed_category("events", "🎉", "المناسبات", "Events", [
        seed_service("photo", "📸", "تصوير", "Photography"), seed_service("party", "🎈", "تنسيق حفلات", "Event coordination"),
        seed_service("hospitality", "☕", "ضيافة", "Hospitality"), seed_service("coffee", "☕", "قهوة ومشروبات", "Coffee and drinks"),
        seed_service("wedding", "💐", "كوش أفراح", "Wedding stage"), seed_service("dj", "🎧", "دي جي", "DJ"),
        seed_service("flowers", "🌹", "ورود", "Flowers"), seed_service("equip", "🎪", "تجهيزات", "Event equipment"),
        seed_service("video", "🎥", "تصوير فيديو", "Videography"), seed_service("sound", "🔊", "صوتيات وإضاءة", "Sound and lighting"),
        seed_service("catering", "🍽️", "بوفيه وضيافة", "Catering"), seed_service("kids_party", "🎁", "حفلات أطفال", "Kids parties"),
        seed_service("chairs", "🪑", "كراسي وطاولات", "Chairs and tables"), seed_service("makeup", "💄", "مكياج مناسبات", "Event makeup"),
    ]),
    seed_category("education", "📚", "التعليم", "Education", [
        seed_service("english", "🇬🇧", "مدرس لغة إنجليزية", "English tutor"), seed_service("math", "➗", "مدرس رياضيات", "Math tutor"),
        seed_service("arabic", "✍️", "مدرس عربي", "Arabic tutor"), seed_service("private_tutor", "👨‍🏫", "مدرس خصوصي", "Private tutor"),
        seed_service("quran", "📖", "تحفيظ قرآن", "Quran memorization"), seed_service("computer_train", "💻", "تدريب حاسوب", "Computer training"),
        seed_service("vocational", "🧰", "تدريب مهني", "Vocational training"), seed_service("physics", "🧲", "مدرس فيزياء", "Physics tutor"),
        seed_service("chemistry", "⚗️", "مدرس كيمياء", "Chemistry tutor"), seed_service("ielts", "📝", "IELTS وTOEFL", "IELTS and TOEFL"),
        seed_service("kids_learning", "🧒", "تأسيس أطفال", "Kids foundation"), seed_service("university", "🎓", "دروس جامعية", "University tutoring"),
    ]),
    seed_category("personal", "🧍", "خدمات شخصية", "Personal services", [
        seed_service("barber", "💈", "حلاقة", "Barber"), seed_service("men_care", "🧴", "عناية رجالية", "Men care"),
        seed_service("tailor", "🧵", "خياطة", "Tailoring"), seed_service("ironing", "👔", "كوي", "Ironing"),
        seed_service("laundry", "🧺", "غسيل ملابس", "Laundry"), seed_service("perfume", "🪔", "عطور وبخور", "Perfume and bukhoor"),
        seed_service("home_help", "🤝", "مساعدة منزلية", "Home assistance"), seed_service("beauty", "💅", "تجميل منزلي", "Home beauty"),
        seed_service("massage", "🧘", "مساج واسترخاء", "Massage"), seed_service("elder_care", "🧓", "رعاية كبار السن", "Elder care"),
        seed_service("pet_care", "🐾", "رعاية حيوانات أليفة", "Pet care"), seed_service("documents", "📄", "تخليص معاملات", "Document services"),
    ]),
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
    {
        "id": "p3", "name": "ناصر للمكيفات", "phone": "93456789", "gov": "الداخلية", "wilayah": "نزوى",
        "areas": ["نزوى", "بهلاء", "منح"], "bio": "صيانة مكيفات وتنظيف وفحص أعطال وتركيب.",
        "hours": "9:00 ص - 8:00 م", "status": "available", "active": 1, "verified": 0, "featured": 0,
        "package_id": "individual_6m", "rating": 4.4, "reviews": 22,
        "services": [{"catId": "homecare", "serviceId": "ac", "priceFrom": 6, "active": True, "areas": ["نزوى", "بهلاء"]}],
    },
    {
        "id": "p4", "name": "بركاء للنقل", "phone": "94567890", "gov": "جنوب الباطنة", "wilayah": "بركاء",
        "areas": ["بركاء", "المصنعة", "مسقط"], "bio": "نقل أثاث وتحميل وتنزيل داخل الولاية وبين المحافظات.",
        "hours": "6:00 ص - 11:00 م", "status": "available", "active": 1, "verified": 1, "featured": 0,
        "package_id": "individual_6m", "rating": 4.6, "reviews": 18,
        "services": [{"catId": "transport", "serviceId": "furniture_move", "priceFrom": 18, "active": True, "areas": ["بركاء", "مسقط"]}, {"catId": "transport", "serviceId": "loading", "priceFrom": 10, "active": True, "areas": ["بركاء"]}],
    },
    {
        "id": "p5", "name": "تقنية الوادي", "phone": "95678901", "gov": "مسقط", "wilayah": "مطرح",
        "areas": ["مطرح", "بوشر", "السيب"], "bio": "صيانة كمبيوتر وشبكات وكاميرات وأنظمة نقاط بيع.",
        "hours": "10:00 ص - 9:00 م", "status": "unavailable", "active": 1, "verified": 1, "featured": 0,
        "package_id": "individual_year", "rating": 4.9, "reviews": 14,
        "services": [{"catId": "tech", "serviceId": "pc", "priceFrom": 8, "active": True, "areas": ["مسقط"]}, {"catId": "tech", "serviceId": "cameras", "priceFrom": 25, "active": True, "areas": ["مسقط"]}],
    },
    {
        "id": "p6", "name": "ظفار للمناسبات", "phone": "96789012", "gov": "ظفار", "wilayah": "صلالة",
        "areas": ["صلالة", "طاقة"], "bio": "تصوير وتنسيق مناسبات وضيافة بتجهيزات مرتبة.",
        "hours": "حسب الموعد", "status": "available", "active": 1, "verified": 0, "featured": 0,
        "package_id": "intro", "rating": 4.3, "reviews": 11,
        "services": [{"catId": "events", "serviceId": "photo", "priceFrom": 35, "active": True, "areas": ["صلالة"]}, {"catId": "events", "serviceId": "hospitality", "priceFrom": 20, "active": True, "areas": ["صلالة"]}],
    },
    {
        "id": "p7", "name": "عُمان للمقاولات الخفيفة", "phone": "97890123", "provider_type": "company",
        "company_name": "عُمان للمقاولات الخفيفة", "gov": "مسقط", "wilayah": "العامرات",
        "areas": ["العامرات", "بوشر", "قريات"], "bio": "شركة صغيرة لأعمال الترميم والبلاط والألمنيوم مع فريق عمل منظم.",
        "hours": "كل أيام الأسبوع 8:00 - 18:00", "status": "available", "active": 1, "verified": 1, "featured": 1,
        "package_id": "company_year", "rating": 4.8, "reviews": 27, "subscription_start": "2026-06-01", "subscription_until": "2027-06-01",
        "services": [{"catId": "construction", "serviceId": "renovation", "priceFrom": 25, "active": True, "areas": ["مسقط"]}, {"catId": "construction", "serviceId": "tiles", "priceFrom": 18, "active": True, "areas": ["مسقط"]}, {"catId": "construction", "serviceId": "aluminium", "priceFrom": 30, "active": True, "areas": ["مسقط"]}],
    },
    {
        "id": "p8", "name": "مركز الطريق للسيارات", "phone": "98901234", "provider_type": "company",
        "company_name": "مركز الطريق للسيارات", "gov": "شمال الباطنة", "wilayah": "صحار",
        "areas": ["صحار", "صحم", "السويق"], "bio": "خدمات فحص وميكانيكا وكهرباء سيارات مع مواعيد واضحة وتواصل سريع.",
        "hours": "السبت - الخميس 8:00 - 20:00", "status": "available", "active": 1, "verified": 1, "featured": 1,
        "package_id": "company_year", "rating": 4.7, "reviews": 34, "subscription_start": "2026-06-10", "subscription_until": "2027-06-10",
        "services": [{"catId": "cars", "serviceId": "mechanic", "priceFrom": 10, "active": True, "areas": ["صحار"]}, {"catId": "cars", "serviceId": "inspection", "priceFrom": 8, "active": True, "areas": ["صحار", "صحم"]}, {"catId": "cars", "serviceId": "battery", "priceFrom": 12, "active": True, "areas": ["شمال الباطنة"]}],
    },
    {
        "id": "p9", "name": "أسماء للتعليم المنزلي", "phone": "99012345", "gov": "الداخلية", "wilayah": "بهلاء",
        "areas": ["بهلاء", "نزوى", "منح"], "bio": "دروس تأسيس ورياضيات وإنجليزي للطلاب مع متابعة أسبوعية مختصرة.",
        "hours": "أيام محددة 16:00 - 21:00", "status": "busy", "active": 1, "verified": 1, "featured": 0,
        "package_id": "individual_year", "rating": 4.9, "reviews": 19, "subscription_start": "2026-05-20", "subscription_until": "2027-05-20",
        "services": [{"catId": "education", "serviceId": "math", "priceFrom": 6, "active": True, "areas": ["بهلاء", "نزوى"]}, {"catId": "education", "serviceId": "english", "priceFrom": 7, "active": True, "areas": ["الداخلية"]}, {"catId": "education", "serviceId": "kids_learning", "priceFrom": 5, "active": True, "areas": ["بهلاء"]}],
    },
    {
        "id": "p10", "name": "دار العناية المنزلية", "phone": "90123456", "provider_type": "company",
        "company_name": "دار العناية المنزلية", "gov": "مسقط", "wilayah": "مطرح",
        "areas": ["مطرح", "بوشر", "السيب"], "bio": "شركة خدمات شخصية منزلية تشمل رعاية كبار السن والمساعدة المنزلية والغسيل.",
        "hours": "كل أيام الأسبوع 7:00 - 22:00", "status": "available", "active": 1, "verified": 1, "featured": 0,
        "package_id": "company_year", "rating": 4.6, "reviews": 23, "subscription_start": "2026-06-15", "subscription_until": "2027-06-15",
        "services": [{"catId": "personal", "serviceId": "elder_care", "priceFrom": 15, "active": True, "areas": ["مسقط"]}, {"catId": "personal", "serviceId": "home_help", "priceFrom": 8, "active": True, "areas": ["مسقط"]}, {"catId": "personal", "serviceId": "laundry", "priceFrom": 4, "active": True, "areas": ["مطرح", "بوشر"]}],
    },
    {
        "id": "p11", "name": "مريم للخياطة والتجهيز", "phone": "91230001", "gov": "جنوب الباطنة", "wilayah": "الرستاق",
        "areas": ["الرستاق", "بركاء"], "bio": "خياطة وتعديل ملابس وكوي وتجهيز بسيط للمناسبات.",
        "hours": "نهاية الأسبوع 10:00 - 20:00", "status": "available", "active": 1, "verified": 0, "featured": 0,
        "package_id": "individual_6m", "rating": 4.5, "reviews": 12, "subscription_start": "2026-06-01", "subscription_until": "2026-12-01",
        "services": [{"catId": "personal", "serviceId": "tailor", "priceFrom": 3, "active": True, "areas": ["الرستاق"]}, {"catId": "personal", "serviceId": "ironing", "priceFrom": 2, "active": True, "areas": ["جنوب الباطنة"]}],
    },
    {
        "id": "p12", "name": "المهارة للتقنية والتصميم", "phone": "92340002", "provider_type": "company",
        "company_name": "المهارة للتقنية والتصميم", "gov": "مسقط", "wilayah": "بوشر",
        "areas": ["بوشر", "السيب", "مطرح"], "bio": "شركة تقنية لتصميم المواقع والدعم التقني والشبكات للمحلات والشركات.",
        "hours": "الأحد - الخميس 9:00 - 18:00", "status": "available", "active": 1, "verified": 1, "featured": 1,
        "package_id": "company_year", "rating": 4.8, "reviews": 29, "subscription_start": "2026-06-05", "subscription_until": "2027-06-05",
        "services": [{"catId": "tech", "serviceId": "websites", "priceFrom": 80, "active": True, "areas": ["مسقط"]}, {"catId": "tech", "serviceId": "design", "priceFrom": 20, "active": True, "areas": ["مسقط"]}, {"catId": "tech", "serviceId": "networks", "priceFrom": 25, "active": True, "areas": ["مسقط"]}],
    },
]


SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQL_COLUMN_DEFINITION_RE = re.compile(
    r"^(?:TEXT|INTEGER|REAL)(?:\s+NOT\s+NULL)?(?:\s+DEFAULT\s+(?:CURRENT_TIMESTAMP|'[^']*'|-?\d+(?:\.\d+)?))?$",
    re.IGNORECASE,
)


def trusted_sql_identifier(value):
    value = str(value or "")
    if not SQL_IDENTIFIER_RE.fullmatch(value):
        raise ValueError("invalid_sql_identifier")
    return f'"{value}"'


def ensure_column(con, table, column, definition):
    table_sql = trusted_sql_identifier(table)
    column_sql = trusted_sql_identifier(column)
    definition = str(definition or "TEXT").strip()
    if not SQL_COLUMN_DEFINITION_RE.fullmatch(definition):
        raise ValueError("invalid_sql_column_definition")
    # SQLite does not parameterize identifiers; both identifiers are regex-validated above.
    columns = [r["name"] for r in con.execute(f"PRAGMA table_info({table_sql})")]  # nosec B608
    if column not in columns:
        original_definition = definition or "TEXT"
        effective_definition = original_definition
        if "current_timestamp" in original_definition.lower() or "datetime('now')" in original_definition.lower():
            effective_definition = re.sub(
                r"\bDEFAULT\s+CURRENT_TIMESTAMP\b|\bDEFAULT\s+DATETIME\('now'\)",
                "DEFAULT ''",
                original_definition,
                flags=re.IGNORECASE,
            )
            if "DEFAULT" not in effective_definition.upper():
                effective_definition = effective_definition.strip() + " DEFAULT ''"
        # The identifiers and the complete column definition use strict allowlists.
        con.execute(f"ALTER TABLE {table_sql} ADD COLUMN {column_sql} {effective_definition}")  # nosec B608
        if "current_timestamp" in original_definition.lower() or "datetime('now')" in original_definition.lower():
            con.execute(
                f"UPDATE {table_sql} SET {column_sql}=CURRENT_TIMESTAMP WHERE {column_sql} IS NULL OR {column_sql}=''"  # nosec B608
            )


def create_pre_migration_backup():
    """Create one SQLite snapshot immediately before the subscription migration."""
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return None
    source = sqlite3.connect(DB_PATH, timeout=12)
    source.row_factory = sqlite3.Row
    try:
        try:
            migrated = source.execute(
                "SELECT 1 FROM settings WHERE key=? LIMIT 1", (MIGRATION_KEY,)
            ).fetchone()
        except sqlite3.OperationalError:
            migrated = None
        if migrated:
            return None
        backup_dir = Path(os.environ.get("KHADAMATI_BACKUP_DIR") or (DB_PATH.parent / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"khadamati-pre-{MIGRATION_KEY.lower()}-{stamp}.sqlite3"
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
        return target
    finally:
        source.close()


def init_db():
    backup_path = create_pre_migration_backup()
    if backup_path:
        print(f"Pre-migration database backup: {backup_path}", flush=True)
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
              package_id TEXT, rating REAL, reviews INTEGER, admin_note TEXT DEFAULT '', image_path TEXT DEFAULT '', card_image TEXT DEFAULT '',
              pin_hash TEXT DEFAULT '', services TEXT NOT NULL, work_images TEXT DEFAULT '[]', documents TEXT DEFAULT '[]',
              quality_score INTEGER DEFAULT 60, response_score INTEGER DEFAULT 70, subscription_until TEXT DEFAULT '',
              subscription_start TEXT DEFAULT '', provider_type TEXT DEFAULT 'individual', company_name TEXT DEFAULT '', company_id TEXT DEFAULT '',
              commercial_no TEXT DEFAULT '', verification_expiry TEXT DEFAULT '', commercial_expiry TEXT DEFAULT '', license_expiry TEXT DEFAULT '',
              latitude REAL, longitude REAL, location_updated_at TEXT DEFAULT '',
              map_visible INTEGER NOT NULL DEFAULT 1, primary_service_id TEXT DEFAULT '',
              before_after TEXT DEFAULT '[]', intro_video_url TEXT DEFAULT '',
              stats TEXT NOT NULL DEFAULT '{"views":0,"whatsapp":0,"calls":0}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_requests(
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS leads(
              id TEXT PRIMARY KEY, provider_id TEXT, kind TEXT, customer_name TEXT, phone TEXT, note TEXT,
              service_value TEXT DEFAULT '', service_name TEXT DEFAULT '', gov TEXT DEFAULT '', status TEXT DEFAULT 'open',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS finance(
              id TEXT PRIMARY KEY, kind TEXT, amount REAL, source TEXT, note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS whatsapp_logs(
              id TEXT PRIMARY KEY, target TEXT, status TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reviews(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, rating INTEGER NOT NULL, customer_name TEXT,
              phone TEXT, comment TEXT, approved INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS complaints(
              id TEXT PRIMARY KEY, provider_id TEXT, customer_name TEXT, phone TEXT, reason TEXT, detail TEXT,
              status TEXT NOT NULL DEFAULT 'open', priority TEXT NOT NULL DEFAULT 'normal',
              resolution TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS packages(
              id TEXT PRIMARY KEY, ar TEXT NOT NULL, en TEXT NOT NULL, price REAL NOT NULL DEFAULT 0,
              duration_days INTEGER NOT NULL DEFAULT 30, featured_boost INTEGER NOT NULL DEFAULT 0,
              max_services INTEGER NOT NULL DEFAULT 3, max_categories INTEGER NOT NULL DEFAULT 1,
              max_images INTEGER NOT NULL DEFAULT 5, active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS subscriptions(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, package_id TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending', start_date TEXT, end_date TEXT, note TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payments(
              id TEXT PRIMARY KEY, provider_id TEXT, subscription_id TEXT, kind TEXT NOT NULL DEFAULT 'revenue',
              amount REAL NOT NULL DEFAULT 0, method TEXT DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'paid',
              note TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS audit_logs(
              id TEXT PRIMARY KEY, actor_kind TEXT, actor_id TEXT, action TEXT NOT NULL, target TEXT,
              detail TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS app_users(
              id TEXT PRIMARY KEY, phone TEXT NOT NULL UNIQUE, name TEXT DEFAULT '', pin_hash TEXT DEFAULT '',
              gov TEXT DEFAULT '', wilayah TEXT DEFAULT '', avatar TEXT DEFAULT '', latitude REAL, longitude REAL,
              status TEXT NOT NULL DEFAULT 'active', failed_attempts INTEGER NOT NULL DEFAULT 0,
              locked_until TEXT DEFAULT '', first_login TEXT DEFAULT CURRENT_TIMESTAMP,
              last_login TEXT DEFAULT CURRENT_TIMESTAMP, login_count INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS auth_sessions(
              id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, session_json TEXT NOT NULL,
              expires_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS customer_requests(
              id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', customer_name TEXT DEFAULT '', phone TEXT DEFAULT '',
              service_value TEXT NOT NULL, service_name TEXT DEFAULT '', gov TEXT DEFAULT '', wilayah TEXT DEFAULT '',
              latitude REAL, longitude REAL, urgency TEXT DEFAULT 'normal', schedule_type TEXT DEFAULT 'flexible',
              requested_at TEXT DEFAULT '', budget_min REAL DEFAULT 0, budget_max REAL DEFAULT 0,
              location_text TEXT DEFAULT '', note TEXT DEFAULT '', images TEXT DEFAULT '[]',
              status TEXT NOT NULL DEFAULT 'matching', accepted_provider_id TEXT DEFAULT '',
              matching_provider_ids TEXT DEFAULT '[]', declined_provider_ids TEXT DEFAULT '[]',
              offers TEXT DEFAULT '[]', messages TEXT DEFAULT '[]', arrival TEXT DEFAULT '{}',
              contact_consent TEXT DEFAULT '{}',
              waitlisted INTEGER NOT NULL DEFAULT 0,
              offers_open INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS request_provider_suggestions(
              id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider_id TEXT NOT NULL,
              suggested_by_user_id TEXT NOT NULL, preset_key TEXT NOT NULL DEFAULT '',
              comment TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
              report_reason TEXT DEFAULT '', selected_at TEXT DEFAULT '', reported_at TEXT DEFAULT '',
              deleted_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(request_id,provider_id)
            );
            CREATE TABLE IF NOT EXISTS app_notifications(
              id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT DEFAULT '', type TEXT DEFAULT 'general',
              title TEXT NOT NULL, message TEXT DEFAULT '', related_id TEXT DEFAULT '',
              priority TEXT DEFAULT 'normal', action_text TEXT DEFAULT '', action_route TEXT DEFAULT '',
              is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS advertisements(
              id TEXT PRIMARY KEY, image_path TEXT NOT NULL, advertiser TEXT DEFAULT '', phone TEXT DEFAULT '',
              amount REAL DEFAULT 0, title TEXT DEFAULT '', body TEXT DEFAULT '', starts_at TEXT DEFAULT '',
              ends_at TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1, deleted_at TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS password_recoveries(
              id TEXT PRIMARY KEY, account_kind TEXT NOT NULL, account_id TEXT DEFAULT '', phone TEXT NOT NULL,
              code_hash TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, expires_at TEXT NOT NULL,
              used_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions(
              id TEXT PRIMARY KEY, target_kind TEXT NOT NULL, target_id TEXT DEFAULT '', endpoint TEXT NOT NULL UNIQUE,
              subscription_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
              last_success_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS policy_acceptances(
              id TEXT PRIMARY KEY, user_id TEXT DEFAULT '', phone TEXT DEFAULT '', policy_version TEXT NOT NULL,
              accepted_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS login_failures(
              account_kind TEXT NOT NULL, account_id TEXT NOT NULL, phone TEXT DEFAULT '',
              attempts INTEGER NOT NULL DEFAULT 0, last_attempt TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(account_kind, account_id)
            );
            CREATE TABLE IF NOT EXISTS subscription_events(
              id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, event_type TEXT NOT NULL,
              from_state TEXT DEFAULT '', to_state TEXT DEFAULT '', actor TEXT DEFAULT 'system',
              detail TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS foundation_claims(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL UNIQUE, phone TEXT DEFAULT '',
              commercial_no TEXT DEFAULT '', fingerprint TEXT NOT NULL UNIQUE,
              subscription_id TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS contact_consents(
              id TEXT PRIMARY KEY, request_id TEXT NOT NULL, user_id TEXT NOT NULL,
              provider_id TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'revoked',
              granted_at TEXT DEFAULT '', expires_at TEXT DEFAULT '', revoked_at TEXT DEFAULT '',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(request_id,provider_id,channel)
            );
            CREATE TABLE IF NOT EXISTS request_dispatches(
              id TEXT PRIMARY KEY, request_id TEXT NOT NULL, provider_id TEXT NOT NULL,
              rank INTEGER NOT NULL DEFAULT 0, score REAL NOT NULL DEFAULT 0,
              score_breakdown TEXT DEFAULT '{}', wave INTEGER NOT NULL DEFAULT 1,
              release_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
              notified_at TEXT DEFAULT '', opened_at TEXT DEFAULT '', offered_at TEXT DEFAULT '',
              accepted_at TEXT DEFAULT '', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(request_id,provider_id)
            );
            CREATE TABLE IF NOT EXISTS invoices(
              id TEXT PRIMARY KEY, payment_id TEXT NOT NULL UNIQUE, subscription_id TEXT NOT NULL,
              provider_id TEXT NOT NULL, number TEXT NOT NULL UNIQUE, currency TEXT NOT NULL DEFAULT 'OMR',
              subtotal REAL NOT NULL DEFAULT 0, total REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'issued', issued_at TEXT NOT NULL, paid_at TEXT DEFAULT '',
              metadata TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coupons(
              id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name_ar TEXT DEFAULT '', name_en TEXT DEFAULT '',
              discount_type TEXT NOT NULL DEFAULT 'fixed', discount_value REAL NOT NULL DEFAULT 0,
              applies_to TEXT DEFAULT '[]', starts_at TEXT DEFAULT '', ends_at TEXT DEFAULT '',
              max_uses INTEGER NOT NULL DEFAULT 0, uses_count INTEGER NOT NULL DEFAULT 0,
              active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS coupon_redemptions(
              id TEXT PRIMARY KEY, coupon_id TEXT NOT NULL, provider_id TEXT NOT NULL,
              subscription_id TEXT DEFAULT '', amount REAL NOT NULL DEFAULT 0,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(coupon_id,provider_id,subscription_id)
            );
            CREATE TABLE IF NOT EXISTS campaigns(
              id TEXT PRIMARY KEY, name_ar TEXT NOT NULL, name_en TEXT DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'subscription', starts_at TEXT DEFAULT '', ends_at TEXT DEFAULT '',
              budget REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
              rules TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_promotions(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, campaign_id TEXT DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'featured', area TEXT DEFAULT '', service_value TEXT DEFAULT '',
              starts_at TEXT DEFAULT '', ends_at TEXT DEFAULT '', amount REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending_payment', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS provider_team_members(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL, phone TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'provider_staff', pin_hash TEXT NOT NULL DEFAULT '',
              permissions TEXT DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(provider_id,phone)
            );
            CREATE TABLE IF NOT EXISTS provider_branches(
              id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, name TEXT NOT NULL,
              gov TEXT DEFAULT '', wilayah TEXT DEFAULT '', address TEXT DEFAULT '',
              latitude REAL, longitude REAL, phone TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS otp_challenges(
              id TEXT PRIMARY KEY, phone TEXT NOT NULL, purpose TEXT NOT NULL,
              target_kind TEXT NOT NULL DEFAULT 'user', code_hash TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
              expires_at TEXT NOT NULL, verified_at TEXT DEFAULT '', delivery_status TEXT DEFAULT 'pending',
              created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS webhook_events(
              id TEXT PRIMARY KEY, provider TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
              signature_valid INTEGER NOT NULL DEFAULT 0, payload_hash TEXT NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_requests_status ON customer_requests(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_request_suggestions_request ON request_provider_suggestions(request_id,status,created_at);
            CREATE INDEX IF NOT EXISTS idx_request_suggestions_user ON request_provider_suggestions(suggested_by_user_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_notifications_target ON app_notifications(target_kind, target_id, is_read);
            CREATE INDEX IF NOT EXISTS idx_sessions_hash ON auth_sessions(token_hash, expires_at);
            CREATE INDEX IF NOT EXISTS idx_dispatch_release ON request_dispatches(status,release_at,wave);
            CREATE INDEX IF NOT EXISTS idx_dispatch_provider ON request_dispatches(provider_id,status,notified_at);
            CREATE INDEX IF NOT EXISTS idx_consent_lookup ON contact_consents(request_id,provider_id,channel,status);
            CREATE INDEX IF NOT EXISTS idx_subscription_provider ON subscriptions(provider_id,status,end_date);
            CREATE INDEX IF NOT EXISTS idx_payment_subscription ON payments(subscription_id,status);
            CREATE INDEX IF NOT EXISTS idx_otp_phone ON otp_challenges(phone,purpose,created_at);
            """
        )
        ensure_column(con, "providers", "image_path", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "card_image", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "pin_hash", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "work_images", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "documents", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "quality_score", "INTEGER DEFAULT 60")
        ensure_column(con, "providers", "response_score", "INTEGER DEFAULT 70")
        ensure_column(con, "providers", "subscription_until", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "subscription_start", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "provider_type", "TEXT DEFAULT 'individual'")
        ensure_column(con, "providers", "company_name", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "company_id", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "commercial_no", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "verification_expiry", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "commercial_expiry", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "license_expiry", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "latitude", "REAL")
        ensure_column(con, "providers", "longitude", "REAL")
        ensure_column(con, "providers", "location_updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "map_visible", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "providers", "primary_service_id", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "before_after", "TEXT DEFAULT '[]'")
        ensure_column(con, "providers", "intro_video_url", "TEXT DEFAULT ''")
        ensure_column(con, "providers", "listing_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "providers", "request_enabled", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "providers", "subscription_state", "TEXT DEFAULT 'active'")
        ensure_column(con, "providers", "availability", "TEXT DEFAULT '{}'")
        ensure_column(con, "providers", "response_minutes", "INTEGER NOT NULL DEFAULT 30")
        ensure_column(con, "providers", "completed_jobs", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "providers", "updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "app_users", "location_updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "app_users", "updated_at", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "offers", "TEXT DEFAULT '[]'")
        ensure_column(con, "customer_requests", "messages", "TEXT DEFAULT '[]'")
        ensure_column(con, "customer_requests", "arrival", "TEXT DEFAULT '{}'")
        ensure_column(con, "customer_requests", "contact_consent", "TEXT DEFAULT '{}'")
        ensure_column(con, "customer_requests", "waitlisted", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "customer_requests", "marketplace_status", "TEXT DEFAULT 'pending'")
        ensure_column(con, "customer_requests", "dispatch_started_at", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "expansion_at", "TEXT DEFAULT ''")
        ensure_column(con, "customer_requests", "ranking_version", "TEXT DEFAULT ''")
        ensure_column(con, "packages", "currency", "TEXT NOT NULL DEFAULT 'OMR'")
        ensure_column(con, "packages", "max_categories", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "max_wilayats", "INTEGER NOT NULL DEFAULT 5")
        ensure_column(con, "packages", "max_governorates", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "monthly_response_limit", "INTEGER NOT NULL DEFAULT 30")
        ensure_column(con, "packages", "lead_delay_minutes", "INTEGER NOT NULL DEFAULT 15")
        ensure_column(con, "packages", "max_team_members", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "max_branches", "INTEGER NOT NULL DEFAULT 1")
        ensure_column(con, "packages", "shared_inbox", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "advanced_reports", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "badge_ar", "TEXT DEFAULT ''")
        ensure_column(con, "packages", "badge_en", "TEXT DEFAULT ''")
        ensure_column(con, "packages", "foundation_once", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "verified_required", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "legacy", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "packages", "entitlements", "TEXT DEFAULT '{}'")
        ensure_column(con, "subscriptions", "currency", "TEXT NOT NULL DEFAULT 'OMR'")
        ensure_column(con, "subscriptions", "grace_days", "INTEGER NOT NULL DEFAULT 14")
        ensure_column(con, "subscriptions", "renewal_package_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "previous_package_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "proration_amount", "REAL NOT NULL DEFAULT 0")
        ensure_column(con, "subscriptions", "credit_amount", "REAL NOT NULL DEFAULT 0")
        ensure_column(con, "subscriptions", "activated_at", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "grace_until", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "cancelled_at", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "refunded_at", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "payment_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "auto_renew", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(con, "subscriptions", "legacy_package_id", "TEXT DEFAULT ''")
        ensure_column(con, "subscriptions", "metadata", "TEXT DEFAULT '{}'")
        ensure_column(con, "subscriptions", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP")
        ensure_column(con, "payments", "currency", "TEXT NOT NULL DEFAULT 'OMR'")
        ensure_column(con, "payments", "external_id", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "gateway", "TEXT DEFAULT 'manual'")
        ensure_column(con, "payments", "failure_code", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "verified_at", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "refunded_at", "TEXT DEFAULT ''")
        ensure_column(con, "payments", "metadata", "TEXT DEFAULT '{}'")
        ensure_column(con, "payments", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP")
        ensure_column(con, "policy_acceptances", "document_types", "TEXT DEFAULT '[]'")
        ensure_column(con, "policy_acceptances", "language", "TEXT DEFAULT 'ar'")
        ensure_column(con, "policy_acceptances", "withdrawn_at", "TEXT DEFAULT ''")
        ensure_column(con, "policy_acceptances", "metadata", "TEXT DEFAULT '{}'")
        ensure_column(con, "leads", "service_value", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "service_name", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "gov", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "status", "TEXT DEFAULT 'open'")
        ensure_column(con, "reviews", "request_id", "TEXT DEFAULT ''")
        ensure_column(con, "reviews", "user_id", "TEXT DEFAULT ''")
        ensure_column(con, "complaints", "request_id", "TEXT DEFAULT ''")
        ensure_column(con, "complaints", "user_id", "TEXT DEFAULT ''")
        for c in SEED_CATEGORIES:
            con.execute(
                "INSERT OR IGNORE INTO categories(id,icon,ar,en,active) VALUES(?,?,?,?,?)",
                (c["id"], c["icon"], c["ar"], c["en"], c["active"]),
            )
            con.execute(
                "UPDATE categories SET icon=?, ar=?, en=? WHERE id=?",
                (c["icon"], c["ar"], c["en"], c["id"]),
            )
            for s in c["services"]:
                con.execute(
                    "INSERT OR IGNORE INTO services(id,category_id,icon,ar,en,active) VALUES(?,?,?,?,?,?)",
                    (s["id"], c["id"], s["icon"], s["ar"], s["en"], s["active"]),
                )
                con.execute(
                    "UPDATE services SET icon=?, ar=?, en=? WHERE id=? AND category_id=?",
                    (s["icon"], s["ar"], s["en"], s["id"], c["id"]),
                )
        for p in SEED_PROVIDERS:
            con.execute(
                """INSERT OR IGNORE INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
                package_id,rating,reviews,services,subscription_until,subscription_start,provider_type,company_name,stats,pin_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    p["id"], p["name"], p["phone"], p["gov"], p["wilayah"], jdump(p["areas"]), p["bio"], p["hours"],
                    p.get("status", "available"), p.get("active", 1), p.get("verified", 0), p.get("featured", 0),
                    p.get("package_id", "intro"), p.get("rating", 0), p.get("reviews", 0), jdump(p.get("services", [])),
                    p.get("subscription_until", ""), p.get("subscription_start", ""),
                    p.get("provider_type", "individual"), p.get("company_name", ""),
                    jdump(p.get("stats", {"views": 0, "whatsapp": 0, "calls": 0})),
                    "",
                ),
            )
        for p in SEED_PROVIDERS:
            con.execute(
                "UPDATE providers SET pin_hash='' WHERE id=? AND pin_hash IN (?,?)",
                (p["id"], hash_secret("1234"), hash_secret(str(p.get("phone", ""))[-4:])),
            )
        for pkg in [
            ("intro", "مجانية", "Free launch", 0, 365, 0, 1, 5, 1),
            ("individual_6m", "مزود 6 أشهر", "Provider 6 months", 10, 183, 10, 4, 5, 1),
            ("individual_year", "مزود سنة", "Provider yearly", 15, 365, 20, 7, 5, 1),
            ("company_year", "شركة سنوية", "Company yearly", 50, 365, 45, 5, 15, 1),
            ("intro_90", "تعريفية", "Introductory", 0, 90, 0, 1, 5, 1),
            ("basic_90", "أساسية", "Basic", 6, 90, 0, 1, 5, 1),
            ("active_90", "نشطة", "Active", 12, 90, 12, 1, 5, 1),
            ("featured_90", "بارزة", "Featured", 20, 90, 40, 1, 10, 1),
            ("company_90", "شركة", "Company", 30, 90, 25, 5, 15, 1),
        ]:
            con.execute(
                """INSERT OR IGNORE INTO packages(
                id,ar,en,price,duration_days,featured_boost,max_services,max_images,active
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                pkg,
            )
        con.execute("UPDATE packages SET max_services=5,max_images=15 WHERE id='company_year' AND max_services>5")
        migration_summary = run_subscription_migration_v1(con)
        print(f"Subscription migration: {jdump(migration_summary)}", flush=True)
        if con.execute("SELECT COUNT(*) n FROM reviews").fetchone()["n"] == 0:
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved,created_at)
                VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)""",
                ("rev_seed_1", "p1", 5, "عميل موثق", "", "خدمة سريعة ومرتبة"),
            )
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved,created_at)
                VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)""",
                ("rev_seed_2", "p2", 5, "عميلة", "", "التنظيف ممتاز والموعد واضح"),
            )
        con.execute(
            "INSERT OR IGNORE INTO settings VALUES('platform', ?)",
            (jdump({
                "nameAr": "خدماتي",
                "nameEn": "Khadamati App",
                "supportEmail": SUPPORT_EMAIL,
                "policyVersion": POLICY_VERSION,
                "currency": OMR,
                "adminWhatsapp": "96890000000",
                "monthlyGoal": 500,
                "acceptProviders": True,
                "subscriptionsEnabled": False,
                "paymentGatewayEnabled": False,
                "uiMode": "simple",
                "showHeroImage": True,
                "showQuickActions": True,
                "showCategories": True,
                "showPopularServices": True,
                "showTopProviders": True,
                "showProviderShortcut": True,
                "showAdminShortcut": False,
                "showQualityBadge": True,
                "maxHomeCategories": 6,
                "maxPopularServices": 4,
                "maxHomeProviders": 2,
            }),),
        )
        platform_row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
        platform_settings = jload(platform_row["value"], {}) if platform_row else {}
        platform_settings["nameAr"] = "خدماتي"
        platform_settings["nameEn"] = "Khadamati App"
        platform_settings["supportEmail"] = SUPPORT_EMAIL
        platform_settings["policyVersion"] = POLICY_VERSION
        platform_settings["currency"] = OMR
        platform_settings.setdefault("subscriptionGraceDays", 14)
        platform_settings.setdefault("expiryThresholds", [30, 14, 7, 1, 0])
        con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(platform_settings),))
        if con.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"] == 0 and INITIAL_ADMIN_CODE:
            con.execute(
                "INSERT INTO admin_users VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP)",
                ("admin_owner", "المالك", hash_pin(INITIAL_ADMIN_CODE), "super_admin", jdump(ALL_PERMISSIONS)),
            )
        elif con.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"] == 0:
            print(
                "Admin account not seeded. Set KHADAMATI_ADMIN_CODE once, then rotate it from administration settings.",
                flush=True,
            )
        con.execute(
            "UPDATE admin_users SET role='super_admin',permissions=? WHERE role='owner'",
            (jdump(ALL_PERMISSIONS),),
        )


def image_url(path):
    value = str(path or "")
    if not value or value.startswith(("data:", "http://", "https://", "/")):
        return value
    return f"/{value.replace(os.sep, '/')}"


def upload_filename(path):
    value = urlparse(str(path or "")).path
    if value.startswith("/uploads/") or value.startswith("/media/"):
        return value.rsplit("/", 1)[-1]
    if value.startswith("uploads/"):
        return value.split("/", 1)[-1]
    return ""


def is_private_upload(path):
    filename = upload_filename(path)
    if not filename:
        return False
    lowered = filename.lower()
    return bool(
        "-doc" in lowered
        or "-problem" in lowered
        or ("-msg_" in lowered and ("-image" in lowered or "-audio" in lowered))
        or (lowered.startswith("usr") and "-avatar" in lowered)
    )


def secure_media_url(path, ttl_seconds=MEDIA_URL_TTL_SECONDS):
    value = image_url(path)
    if not value or not is_private_upload(value):
        return value
    filename = upload_filename(value)
    expires = int(time.time()) + int(ttl_seconds)
    payload = f"{filename}:{expires}".encode("utf-8")
    signature = hmac.new(MEDIA_SIGNING_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"/media/{filename}?exp={expires}&sig={signature}"


def valid_media_signature(filename, expires, signature):
    if not filename or not re.fullmatch(r"[A-Za-z0-9_.-]{1,220}", filename):
        return False
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_at < int(time.time()) or expires_at > int(time.time()) + 86_400:
        return False
    expected = hmac.new(
        MEDIA_SIGNING_KEY.encode("utf-8"),
        f"{filename}:{expires_at}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(signature or ""))


def upload_signature_matches(mime, blob):
    """Validate the declared data-URL type against a small, deterministic magic-byte set."""
    if not blob:
        return False
    if mime == "image/jpeg":
        return blob.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return blob.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
    if mime == "application/pdf":
        return blob.startswith(b"%PDF-")
    if mime in {"audio/webm", "video/webm"}:
        return blob.startswith(b"\x1aE\xdf\xa3")
    if mime in {"audio/mp4", "video/mp4", "video/quicktime"}:
        return len(blob) >= 12 and blob[4:8] == b"ftyp"
    if mime == "audio/mpeg":
        return blob.startswith(b"ID3") or (len(blob) > 1 and blob[0] == 0xFF and blob[1] & 0xE0 == 0xE0)
    if mime == "audio/ogg":
        return blob.startswith(b"OggS")
    return False


def urls(paths):
    return [image_url(p) for p in paths if p]


def row_provider(r, private=False, sign_private=False):
    d = dict(r)
    d["areas"] = jload(d["areas"], [])
    d["services"] = jload(d["services"], [])
    d["stats"] = jload(d["stats"], {"views": 0, "whatsapp": 0, "calls": 0})
    d["workImages"] = jload(d.pop("work_images", "[]"), [])
    d["workImageUrls"] = urls(d["workImages"])
    d["documents"] = jload(d.pop("documents", "[]"), [])
    if private and sign_private:
        d["documents"] = [secure_media_url(item) for item in d["documents"] if item]
    before_after = jload(d.pop("before_after", "[]"), [])
    d["beforeAfter"] = [
        {
            **item,
            "before": image_url(item.get("before", "")),
            "after": image_url(item.get("after", "")),
        }
        for item in before_after
        if isinstance(item, dict)
    ]
    d["introVideoUrl"] = image_url(d.pop("intro_video_url", ""))
    for k in ("active", "verified", "featured"):
        d[k] = bool(d[k])
    d["listingEnabled"] = bool(d.pop("listing_enabled", True))
    d["requestEnabled"] = bool(d.pop("request_enabled", True))
    d["mapVisible"] = bool(d.pop("map_visible", True))
    d["primaryServiceId"] = d.pop("primary_service_id", "")
    d["subscriptionState"] = d.pop("subscription_state", "active") or "active"
    d["availability"] = jload(d.pop("availability", "{}"), {})
    d["responseMinutes"] = int(d.pop("response_minutes", 30) or 30)
    d["completedJobs"] = int(d.pop("completed_jobs", 0) or 0)
    d["packageId"] = d.pop("package_id", "")
    d["adminNote"] = d.pop("admin_note", "")
    d["imagePath"] = d.pop("image_path", "")
    d["imageUrl"] = image_url(d["imagePath"])
    d["cardImage"] = d.pop("card_image", "") or d["imageUrl"]
    d["qualityScore"] = int(d.pop("quality_score", 0) or 0)
    d["responseScore"] = int(d.pop("response_score", 0) or 0)
    d["subscriptionUntil"] = d.pop("subscription_until", "")
    d["subscriptionStart"] = d.pop("subscription_start", "")
    d["providerType"] = d.pop("provider_type", "individual") or "individual"
    d["companyName"] = d.pop("company_name", "")
    d["companyId"] = d.pop("company_id", "")
    d["commercialNo"] = d.pop("commercial_no", "")
    d["verificationExpiry"] = d.pop("verification_expiry", "")
    d["commercialExpiry"] = d.pop("commercial_expiry", "")
    d["licenseExpiry"] = d.pop("license_expiry", "")
    latitude = d.pop("latitude", None)
    longitude = d.pop("longitude", None)
    location_updated_at = d.pop("location_updated_at", "")
    if not private and not d["mapVisible"]:
        latitude, longitude = None, None
    d["location"] = (
        {"lat": latitude, "lng": longitude, "updatedAt": location_updated_at}
        if latitude is not None and longitude is not None
        else None
    )
    d["pinConfigured"] = bool(d.pop("pin_hash", ""))
    if not private:
        for key in (
            "phone", "adminNote", "documents", "commercialNo", "companyId",
            "verificationExpiry", "commercialExpiry", "licenseExpiry", "pinConfigured",
        ):
            d.pop(key, None)
    return d


def provider_request_view(payload, created_at=""):
    """Return one pending provider request with stable frontend field names."""
    item = dict(payload or {})
    description = safe_text(item.get("bio") or item.get("note"), 600)
    item["bio"] = description
    item["note"] = description
    item["pending"] = True
    item["active"] = False
    item["status"] = "pending"
    item["services"] = item.get("services") if isinstance(item.get("services"), list) else []
    item["documents"] = item.get("documents") if isinstance(item.get("documents"), list) else []
    item["workImages"] = item.get("workImages") if isinstance(item.get("workImages"), list) else []
    if created_at:
        item["createdAt"] = created_at
    item.pop("pinHash", None)
    return item


def row_review(r, private=False):
    d = dict(r)
    d["approved"] = bool(d["approved"])
    if not private:
        for key in ("phone", "user_id", "request_id"):
            d.pop(key, None)
    return d


def row_complaint(r, private=False):
    d = dict(r)
    if not private:
        for key in ("phone", "user_id"):
            d.pop(key, None)
    return d


def row_package(r):
    d = dict(r)
    d["active"] = bool(d["active"])
    d["legacy"] = bool(d.get("legacy", 0))
    d["durationDays"] = d.pop("duration_days")
    d["featuredBoost"] = d.pop("featured_boost")
    d["maxServices"] = d.pop("max_services")
    d["maxCategories"] = d.pop("max_categories", 1)
    d["maxImages"] = d.pop("max_images")
    d["maxWilayats"] = d.pop("max_wilayats", 0)
    d["maxGovernorates"] = d.pop("max_governorates", 0)
    d["monthlyResponses"] = d.pop("monthly_response_limit", 0)
    d["leadDelayMinutes"] = d.pop("lead_delay_minutes", 0)
    d["teamMembers"] = d.pop("max_team_members", 1)
    d["branches"] = d.pop("max_branches", 1)
    d["sharedInbox"] = bool(d.pop("shared_inbox", 0))
    d["advancedReports"] = bool(d.pop("advanced_reports", 0))
    d["badgeAr"] = d.pop("badge_ar", "")
    d["badgeEn"] = d.pop("badge_en", "")
    d["foundationOnce"] = bool(d.pop("foundation_once", 0))
    d["verifiedRequired"] = bool(d.pop("verified_required", 0))
    d["entitlements"] = jload(d.get("entitlements"), {})
    return d


def row_subscription(r):
    d = dict(r)
    d["packageId"] = d.pop("package_id")
    d["providerId"] = d.pop("provider_id")
    d["startDate"] = d.pop("start_date")
    d["endDate"] = d.pop("end_date")
    d["renewalPackageId"] = d.pop("renewal_package_id", "")
    d["previousPackageId"] = d.pop("previous_package_id", "")
    d["prorationAmount"] = d.pop("proration_amount", 0)
    d["creditAmount"] = d.pop("credit_amount", 0)
    d["graceDays"] = d.pop("grace_days", 14)
    d["graceUntil"] = d.pop("grace_until", "")
    d["activatedAt"] = d.pop("activated_at", "")
    d["cancelledAt"] = d.pop("cancelled_at", "")
    d["refundedAt"] = d.pop("refunded_at", "")
    d["paymentId"] = d.pop("payment_id", "")
    d["legacyPackageId"] = d.pop("legacy_package_id", "")
    d["autoRenew"] = bool(d.pop("auto_renew", 0))
    d["metadata"] = jload(d.get("metadata"), {})
    return d


def row_payment(r):
    d = dict(r)
    d["providerId"] = d.pop("provider_id")
    d["subscriptionId"] = d.pop("subscription_id")
    d["externalId"] = d.pop("external_id", "")
    d["failureCode"] = d.pop("failure_code", "")
    d["verifiedAt"] = d.pop("verified_at", "")
    d["refundedAt"] = d.pop("refunded_at", "")
    d["metadata"] = jload(d.get("metadata"), {})
    return d


def row_audit(r):
    return dict(r)


def row_lead(r):
    return dict(r)


def lead_matches_provider(lead, provider):
    if lead.get("kind") != "request" or lead.get("status") in ("cancelled", "deleted", "closed"):
        return False
    service_value = (lead.get("service_value") or "").strip()
    requested_cat = ""
    requested_service = ""
    if "|" in service_value:
        requested_cat, requested_service = service_value.split("|", 1)
    elif service_value:
        requested_service = service_value
    provider_services = provider.get("services") or []
    service_ok = not requested_service or any(
        svc.get("active", True)
        and svc.get("serviceId") == requested_service
        and (not requested_cat or svc.get("catId") == requested_cat)
        for svc in provider_services
    )
    gov = (lead.get("gov") or "").strip()
    areas = set(provider.get("areas") or [])
    areas.update([provider.get("gov"), provider.get("wilayah")])
    area_ok = not gov or gov in areas
    return bool(service_ok and area_ok)


def log_audit(con, session, action, target="", detail=""):
    actor_kind = (session or {}).get("kind", "system")
    actor_id = (session or {}).get("id") or (session or {}).get("providerId") or "system"
    con.execute("INSERT INTO audit_logs VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)", (slug("audit"), actor_kind, actor_id, action, target, detail[:900]))


def recompute_provider_quality(con, provider_id):
    r = con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not r:
        return
    provider = row_provider(r, private=True)
    approved = con.execute("SELECT COUNT(*) n, COALESCE(AVG(rating),0) avg_rating FROM reviews WHERE provider_id=? AND approved=1", (provider_id,)).fetchone()
    open_complaints = con.execute("SELECT COUNT(*) n FROM complaints WHERE provider_id=? AND status!='closed'", (provider_id,)).fetchone()["n"]
    profile_score = 0
    profile_score += 15 if provider.get("imagePath") else 0
    profile_score += 15 if provider.get("bio") else 0
    profile_score += 10 if provider.get("hours") else 0
    profile_score += 10 if provider.get("areas") else 0
    profile_score += 10 if provider.get("services") else 0
    profile_score += min(len(provider.get("workImages") or []) * 5, 20)
    rating_score = int(float(approved["avg_rating"] or 0) * 12)
    trust_score = 10 if provider.get("verified") else 0
    complaint_penalty = min(open_complaints * 12, 40)
    quality = max(0, min(100, profile_score + rating_score + trust_score - complaint_penalty))
    con.execute("UPDATE providers SET quality_score=?, rating=?, reviews=? WHERE id=?", (quality, round(float(approved["avg_rating"] or provider.get("rating") or 0), 2), int(approved["n"] or 0), provider_id))


def admin_public(r):
    d = dict(r)
    d.pop("code_hash", None)
    d["permissions"] = jload(d["permissions"], [])
    d["active"] = bool(d["active"])
    return d


def issue_token(session):
    token = secrets.token_urlsafe(32)
    with db() as con:
        con.execute(
            "INSERT INTO auth_sessions(id,token_hash,session_json,expires_at) VALUES(?,?,?,?)",
            (slug("ses"), hash_secret(token), jdump(session), iso_datetime(days=SESSION_DAYS)),
        )
    return token


def validated_session(con, session):
    if not isinstance(session, dict):
        return None
    kind = session.get("kind")
    if kind == "admin":
        row = con.execute(
            "SELECT * FROM admin_users WHERE id=? AND active=1", (session.get("id", ""),)
        ).fetchone()
        return {"kind": "admin", **admin_public(row)} if row else None
    if kind == "user":
        row = con.execute(
            "SELECT id,name,phone,status FROM app_users WHERE id=? AND status='active'",
            (session.get("userId", ""),),
        ).fetchone()
        if not row:
            return None
        return {
            "kind": "user", "userId": row["id"], "name": row["name"], "phone": row["phone"]
        }
    if kind == "provider":
        provider = con.execute(
            "SELECT id,name,active,status FROM providers WHERE id=?",
            (session.get("providerId", ""),),
        ).fetchone()
        if not provider or not bool(provider["active"]) or provider["status"] == "deleted":
            return None
        member_id = safe_text(session.get("memberId", ""), 100)
        if member_id:
            member = con.execute(
                """SELECT id,role,permissions FROM provider_team_members
                WHERE id=? AND provider_id=? AND active=1""",
                (member_id, provider["id"]),
            ).fetchone()
            if not member:
                return None
            role = member["role"]
            if role not in {"provider_manager", "provider_staff"}:
                return None
            provider_permissions = [
                permission for permission in jload(member["permissions"], [])
                if permission in PROVIDER_ROLE_PERMISSIONS.get(role, set())
            ]
        else:
            role = "provider_owner"
            provider_permissions = list(PROVIDER_ROLE_PERMISSIONS["provider_owner"])
        return {
            "kind": "provider", "providerId": provider["id"], "name": provider["name"],
            "role": role, "memberId": member_id, "providerPermissions": provider_permissions,
        }
    if kind == "provider_pending":
        request_id = safe_text(session.get("requestId", ""), 120)
        row = con.execute(
            "SELECT payload FROM provider_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not row:
            return None
        payload = jload(row["payload"], {})
        phone = normalize_phone(payload.get("phone", ""))
        if not phone or not phone_matches(session.get("phone", ""), phone):
            return None
        return {
            "kind": "provider_pending", "requestId": request_id,
            "name": payload.get("name", ""), "phone": phone,
        }
    return None


def token_session(headers):
    authorization = str(headers.get("Authorization", "") or "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    with db() as con:
        row = con.execute(
            "SELECT id,session_json,expires_at FROM auth_sessions WHERE token_hash=? AND revoked=0",
            (hash_secret(token),),
        ).fetchone()
        if not row:
            return None
        try:
            expires_at = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
                return None
        except ValueError:
            con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
            return None
        session = validated_session(con, jload(row["session_json"], None))
        if not session:
            con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
            return None
        return session


def revoke_session(headers):
    authorization = str(headers.get("Authorization", "") or "")
    if not authorization.startswith("Bearer "):
        return False
    token = authorization[7:].strip()
    if not token:
        return False
    with db() as con:
        result = con.execute(
            "UPDATE auth_sessions SET revoked=1 WHERE token_hash=?", (hash_secret(token),)
        )
    return result.rowcount > 0


def revoke_account_sessions(con, kind, account_id, exclude_token_hash=None):
    """Revoke sessions for one exact account without relying on JSON substring matching."""
    id_key = {"user": "userId", "provider": "providerId", "admin": "id"}.get(kind)
    if not id_key or not account_id:
        return 0
    revoked = 0
    rows = con.execute(
        "SELECT id,token_hash,session_json FROM auth_sessions WHERE revoked=0"
    ).fetchall()
    for row in rows:
        session = jload(row["session_json"], {})
        if (
            isinstance(session, dict)
            and session.get("kind") == kind
            and str(session.get(id_key, "")) == str(account_id)
            and row["token_hash"] != (exclude_token_hash or "")
        ):
            con.execute("UPDATE auth_sessions SET revoked=1 WHERE id=?", (row["id"],))
            revoked += 1
    return revoked


def row_app_user(r, private=False, sign_private=False):
    d = dict(r)
    d["firstLogin"] = d.pop("first_login", "")
    d["lastLogin"] = d.pop("last_login", "")
    d["loginCount"] = int(d.pop("login_count", 0) or 0)
    d["failedAttempts"] = int(d.pop("failed_attempts", 0) or 0)
    d["lockedUntil"] = d.pop("locked_until", "")
    d["pinConfigured"] = bool(d.pop("pin_hash", ""))
    if private and sign_private and d.get("avatar"):
        d["avatar"] = secure_media_url(d["avatar"])
    if not private:
        d.pop("phone", None)
    return d


def row_customer_request(r, sign_private=False):
    d = dict(r)
    d["userId"] = d.pop("user_id", "")
    d["customerName"] = d.pop("customer_name", "")
    d["serviceValue"] = d.pop("service_value", "")
    d["serviceName"] = d.pop("service_name", "")
    d["scheduleType"] = d.pop("schedule_type", "")
    d["requestedAt"] = d.pop("requested_at", "")
    d["budgetMin"] = d.pop("budget_min", 0)
    d["budgetMax"] = d.pop("budget_max", 0)
    d["locationText"] = d.pop("location_text", "")
    d["images"] = jload(d["images"], [])
    if sign_private:
        d["images"] = [secure_media_url(item) for item in d["images"] if item]
    d["acceptedProviderId"] = d.pop("accepted_provider_id", "")
    d["matchingProviderIds"] = jload(d.pop("matching_provider_ids", "[]"), [])
    d["declinedProviderIds"] = jload(d.pop("declined_provider_ids", "[]"), [])
    d["offers"] = jload(d.pop("offers", "[]"), [])
    messages = jload(d.pop("messages", "[]"), [])
    d["messages"] = [
        {
            **message,
            "image": secure_media_url(message.get("image", "")) if sign_private else image_url(message.get("image", "")),
            "audio": secure_media_url(message.get("audio", "")) if sign_private else image_url(message.get("audio", "")),
        }
        for message in messages
        if isinstance(message, dict)
    ]
    d["arrival"] = jload(d.pop("arrival", "{}"), {})
    d["contactConsent"] = jload(d.pop("contact_consent", "{}"), {})
    d["waitlisted"] = bool(d.pop("waitlisted", 0))
    d["offersOpen"] = bool(d.pop("offers_open", 0))
    d["marketplaceStatus"] = d.pop("marketplace_status", "pending")
    d["dispatchStartedAt"] = d.pop("dispatch_started_at", "")
    d["expansionAt"] = d.pop("expansion_at", "")
    d["rankingVersion"] = d.pop("ranking_version", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    return d


SUGGESTION_PRESET_KEYS = {
    "excellent_work",
    "fast_execution",
    "fair_price",
    "worked_before",
    "recommended_contact",
}
ACTIVE_REQUEST_STATES = {"matching", "viewed", "unavailable", "paused", "open"}


def row_request_suggestion(r):
    d = dict(r)
    d["requestId"] = d.pop("request_id", "")
    d["providerId"] = d.pop("provider_id", "")
    d["suggestedByUserId"] = d.pop("suggested_by_user_id", "")
    d["presetKey"] = d.pop("preset_key", "")
    d["reportReason"] = d.pop("report_reason", "")
    d["selectedAt"] = d.pop("selected_at", "")
    d["reportedAt"] = d.pop("reported_at", "")
    d["deletedAt"] = d.pop("deleted_at", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    if "provider_name" in d:
        d["providerName"] = d.pop("provider_name", "")
    return d


def request_suggestions(con, request_id, *, include_hidden=False):
    if include_hidden:
        rows = con.execute(
            """SELECT s.*,p.name provider_name FROM request_provider_suggestions s
            LEFT JOIN providers p ON p.id=s.provider_id WHERE s.request_id=?
            ORDER BY s.created_at DESC""",
            (request_id,),
        )
    else:
        rows = con.execute(
            """SELECT s.*,p.name provider_name FROM request_provider_suggestions s
            LEFT JOIN providers p ON p.id=s.provider_id
            WHERE s.request_id=? AND s.status IN ('active','selected')
            ORDER BY s.created_at DESC""",
            (request_id,),
        )
    return [
        row_request_suggestion(row)
        for row in rows
    ]


def request_suggestion_by_id(con, suggestion_id):
    row = con.execute(
        """SELECT s.*,p.name provider_name FROM request_provider_suggestions s
        LEFT JOIN providers p ON p.id=s.provider_id WHERE s.id=?""",
        (suggestion_id,),
    ).fetchone()
    return row_request_suggestion(row) if row else None


def marketplace_request(item, include_note=False):
    """Return only the fields needed by the public request board."""
    allowed = {
        "id", "serviceValue", "serviceName", "gov", "wilayah", "urgency",
        "scheduleType", "requestedAt", "note", "status", "offers",
        "acceptedProviderId", "offersOpen", "createdAt", "updatedAt",
    }
    public_item = {key: value for key, value in item.items() if key in allowed}
    public_item["requesterLabel"] = "مستخدم خدماتي"
    public_item["offerCount"] = len(public_item.get("offers") or [])
    public_item["offers"] = []
    public_item["acceptedProviderId"] = ""
    public_item["note"] = safe_text(public_item.get("note", ""), 280) if include_note else ""
    return public_item


def distance_km(request_row, provider_row):
    values = (
        request_row.get("latitude"), request_row.get("longitude"),
        provider_row.get("latitude"), provider_row.get("longitude"),
    )
    if any(value is None for value in values):
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, map(float, values))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return round(6371 * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0, 1 - value))), 2)


def provider_profile_complete(provider):
    services = jload(provider.get("services"), []) if isinstance(provider.get("services"), str) else provider.get("services", [])
    documents = jload(provider.get("documents"), []) if isinstance(provider.get("documents"), str) else provider.get("documents", [])
    return bool(
        provider.get("verified")
        and provider.get("commercial_no")
        and documents
        and services
        and len(str(provider.get("bio") or "").split()) >= 3
        and provider.get("hours")
    )


def ranked_suggestion_candidates(con, request_row, *, limit=10):
    request = dict(request_row)
    entitlements = EntitlementService(con)
    candidates = []
    for row in con.execute(
        """SELECT * FROM providers WHERE active=1 AND verified=1 AND status='available'
        AND COALESCE(listing_enabled,1)=1 AND COALESCE(request_enabled,1)=1"""
    ):
        provider = dict(row)
        if not provider_profile_complete(provider):
            continue
        try:
            allowed, _, grants = entitlements.can_receive(provider["id"])
        except DomainError:
            allowed, grants = False, {}
        if not allowed:
            continue
        score, breakdown = RankingService.score(request, provider, grants.get("planId", provider.get("package_id", "")), datetime.now(UTC))
        if score <= 0 or not RankingService.exact_service_match(request, provider):
            continue
        distance = distance_km(request, provider)
        area_priority = 0 if request.get("wilayah") and request.get("wilayah") == provider.get("wilayah") else 1
        candidates.append((area_priority, distance if distance is not None else 9999, -score, provider, breakdown))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]["id"]))
    result = []
    for _, distance, negative_score, provider, breakdown in candidates[: max(1, min(int(limit), 20))]:
        public_provider = row_provider(provider, private=False)
        public_provider["suggestionScore"] = round(-negative_score, 1)
        public_provider["distanceKm"] = None if distance == 9999 else distance
        public_provider["matchBreakdown"] = breakdown
        result.append(public_provider)
    return result


def row_notification(r):
    d = dict(r)
    d["targetKind"] = d.pop("target_kind")
    d["targetId"] = d.pop("target_id")
    d["relatedId"] = d.pop("related_id")
    d["actionText"] = d.pop("action_text")
    d["actionRoute"] = d.pop("action_route")
    d["read"] = bool(d.pop("is_read"))
    d["createdAt"] = d.pop("created_at")
    return d


def row_advertisement(r):
    d = dict(r)
    d["imageUrl"] = image_url(d.pop("image_path", ""))
    d["startsAt"] = d.pop("starts_at", "")
    d["endsAt"] = d.pop("ends_at", "")
    d["deletedAt"] = d.pop("deleted_at", "")
    d["createdAt"] = d.pop("created_at", "")
    d["updatedAt"] = d.pop("updated_at", "")
    d["active"] = bool(d["active"])
    return d


def push_ready():
    return bool(webpush and os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_PUBLIC_KEY"))


def deliver_push(target_kind, target_id, payload):
    if not push_ready():
        return
    time.sleep(0.15)
    with db() as con:
        subscriptions = list(
            con.execute(
                """SELECT id,subscription_json FROM push_subscriptions
                WHERE target_kind=? AND target_id=? AND active=1""",
                (target_kind, target_id or ""),
            )
        )
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=jload(subscription["subscription_json"], {}),
                data=jdump(payload),
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", f"mailto:{SUPPORT_EMAIL}")},
                ttl=300,
            )
            with db() as con:
                con.execute(
                    "UPDATE push_subscriptions SET last_success_at=CURRENT_TIMESTAMP WHERE id=?",
                    (subscription["id"],),
                )
        except WebPushException as err:
            status = getattr(getattr(err, "response", None), "status_code", 0)
            if status in (404, 410):
                with db() as con:
                    con.execute("UPDATE push_subscriptions SET active=0 WHERE id=?", (subscription["id"],))
        except Exception as err:
            print(f"Push delivery skipped: {err}", flush=True)


def create_notification(con, target_kind, target_id, title, message="", *, type_="general",
                        related_id="", priority="normal", action_text="", action_route=""):
    notification_id = slug("ntf")
    con.execute(
        """INSERT INTO app_notifications(
        id,target_kind,target_id,type,title,message,related_id,priority,action_text,action_route)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            notification_id, target_kind, target_id or "", type_, title[:160], message[:1200],
            related_id or "", priority, action_text[:80], action_route[:240],
        ),
    )
    if push_ready():
        is_chat = type_ == "chat" and bool(related_id)
        push_tag = (
            f"khadamati-chat-{target_kind}-{target_id or 'account'}-{related_id}"
            if is_chat else f"khadamati-{notification_id}"
        )
        push_route = PUBLIC_APP_URL
        if is_chat:
            push_route += f"#chat={related_id}&target={target_kind}"
        threading.Thread(
            target=deliver_push,
            args=(
                target_kind,
                target_id or "",
                {
                    "id": notification_id,
                    "title": title[:160],
                    "body": message[:1200],
                    "tag": push_tag,
                    "route": push_route,
                },
            ),
            daemon=True,
        ).start()
    return notification_id


def request_matches_provider(request_item, provider):
    service_value = str(request_item.get("serviceValue") or "")
    requested_cat, requested_service = ("", "")
    if "|" in service_value:
        requested_cat, requested_service = service_value.split("|", 1)
    service_ok = any(
        svc.get("active", True)
        and requested_service
        and svc.get("serviceId") == requested_service
        and (not requested_cat or svc.get("catId") == requested_cat)
        for svc in provider.get("services") or []
    )
    if not service_ok:
        return False
    request_area = {str(request_item.get("gov") or ""), str(request_item.get("wilayah") or "")} - {""}
    provider_area = {
        str(provider.get("gov") or ""),
        str(provider.get("wilayah") or ""),
        *(str(a) for a in provider.get("areas") or []),
    } - {""}
    return not request_area or bool(request_area & provider_area)


def provider_eligibility(con, provider, *, receive_requests=False, map_only=False):
    """Single source of truth for public listing, request intake, and map markers."""
    item = dict(provider) if not isinstance(provider, dict) else provider
    if not int(item.get("active") or 0) or not int(item.get("verified") or 0):
        return False, "provider_inactive"
    if item.get("status") in {"unavailable", "under_review", "pending", "suspended", "deleted"}:
        return False, "provider_unavailable"
    if not int(item.get("listing_enabled", item.get("listingEnabled", 1)) or 0):
        return False, "listing_disabled"
    if receive_requests:
        if item.get("status") != "available":
            return False, "provider_not_available"
        if not int(item.get("request_enabled", item.get("requestEnabled", 1)) or 0):
            return False, "requests_disabled"
    if map_only:
        if not int(item.get("map_visible", item.get("mapVisible", 1)) or 0):
            return False, "map_hidden"
        if item.get("latitude") is None or item.get("longitude") is None:
            return False, "location_missing"
    allowed, reason, _ = EntitlementService(con).can_receive(item.get("id", ""))
    if receive_requests and not allowed:
        return False, reason or "subscription_inactive"
    if not receive_requests:
        grants = EntitlementService(con).for_provider(item.get("id", ""))
        if not grants.get("allowed"):
            return False, "subscription_inactive"
    return True, ""


def service_availability_snapshot(con):
    """Return privacy-safe provider counts used by the direct-request UI."""
    services = {}
    categories = {}
    for row in con.execute("SELECT * FROM providers"):
        eligible, _ = provider_eligibility(con, row, receive_requests=True)
        if not eligible:
            continue
        provider_services = jload(row["services"], [])
        provider_categories = set()
        for service in provider_services:
            if not isinstance(service, dict) or service.get("active") is False:
                continue
            cat_id = safe_text(service.get("catId"), 80)
            service_id = safe_text(service.get("serviceId"), 80)
            if not cat_id or not service_id:
                continue
            key = f"{cat_id}|{service_id}"
            services[key] = int(services.get(key, 0)) + 1
            provider_categories.add(cat_id)
        for cat_id in provider_categories:
            categories[cat_id] = int(categories.get(cat_id, 0)) + 1
    return {
        "services": services,
        "categories": categories,
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def login_failure_state(con, account_kind, account_id):
    key = safe_text(account_id, 160) or "unknown"
    row = con.execute(
        "SELECT attempts,last_attempt FROM login_failures WHERE account_kind=? AND account_id=?",
        (account_kind, key),
    ).fetchone()
    if not row:
        return {"locked": False, "attempts": 0, "retryAfter": 0}
    try:
        last_attempt = datetime.fromisoformat(str(row["last_attempt"]).replace("Z", "+00:00"))
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        last_attempt = datetime.now(UTC) - timedelta(minutes=LOGIN_LOCK_MINUTES + 1)
    unlock_at = last_attempt + timedelta(minutes=LOGIN_LOCK_MINUTES)
    attempts = int(row["attempts"] or 0)
    if datetime.now(UTC) >= unlock_at:
        con.execute(
            "DELETE FROM login_failures WHERE account_kind=? AND account_id=?",
            (account_kind, key),
        )
        return {"locked": False, "attempts": 0, "retryAfter": 0}
    retry_after = max(1, math.ceil((unlock_at - datetime.now(UTC)).total_seconds()))
    return {
        "locked": attempts >= LOGIN_MAX_ATTEMPTS,
        "attempts": attempts,
        "retryAfter": retry_after if attempts >= LOGIN_MAX_ATTEMPTS else 0,
    }


def record_login_failure(con, account_kind, account_id, phone=""):
    key = safe_text(account_id or phone, 160) or "unknown"
    login_failure_state(con, account_kind, key)
    con.execute(
        """INSERT INTO login_failures(account_kind,account_id,phone,attempts,last_attempt)
        VALUES(?,?,?,1,CURRENT_TIMESTAMP)
        ON CONFLICT(account_kind,account_id) DO UPDATE SET
        attempts=login_failures.attempts+1,last_attempt=CURRENT_TIMESTAMP""",
        (account_kind, key, safe_text(phone, 32)),
    )
    row = con.execute(
        "SELECT attempts FROM login_failures WHERE account_kind=? AND account_id=?",
        (account_kind, key),
    ).fetchone()
    attempts = int(row["attempts"] or 0)
    if attempts in {3, LOGIN_MAX_ATTEMPTS}:
        create_notification(
            con, "admin", "", "محاولات دخول غير ناجحة",
            f"{account_kind}: {phone or key} - عدد المحاولات {attempts}",
            type_="security", related_id=key, priority="urgent",
            action_text="مراجعة الحساب", action_route=f"admin:{account_kind}:{key}",
        )
    return attempts


def clear_login_failures(con, account_kind, account_id):
    con.execute(
        "DELETE FROM login_failures WHERE account_kind=? AND account_id=?",
        (account_kind, account_id),
    )


def permissions_for(role, selected=None):
    if selected:
        return [p for p in selected if p in ALL_PERMISSIONS]
    return ROLE_PERMISSIONS.get(role, [])


def has_permission(session, permission):
    if not session or session.get("kind") != "admin":
        return False
    role = str(session.get("role") or "")
    raw_permissions = session.get("permissions")
    if isinstance(raw_permissions, str):
        raw_permissions = jload(raw_permissions, [])
    if not isinstance(raw_permissions, list):
        raw_permissions = []
    permissions = raw_permissions if raw_permissions else permissions_for(role)
    return role in {"owner", "super_admin"} or permission in permissions


def scan_expirations(con):
    settings_row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
    settings = jload(settings_row["value"], {}) if settings_row else {}
    thresholds = settings.get("expiryThresholds", [30, 14, 7, 1, 0])
    thresholds = sorted({int(x) for x in thresholds if str(x).lstrip("-").isdigit()}) or [0, 1, 7, 14, 30]
    checks = []
    for row in con.execute(
        """SELECT id,name,subscription_until,verification_expiry,commercial_expiry,license_expiry
        FROM providers"""
    ):
        for field, label in (
            ("subscription_until", "الاشتراك"),
            ("verification_expiry", "التوثيق"),
            ("commercial_expiry", "السجل التجاري"),
            ("license_expiry", "الرخصة"),
        ):
            if row[field]:
                checks.append(("provider", row["id"], row["name"], label, row[field]))
    for row in con.execute("SELECT id,advertiser,ends_at FROM advertisements WHERE active=1"):
        if row["ends_at"]:
            checks.append(("advertisement", row["id"], row["advertiser"] or "إعلان", "الإعلان", row["ends_at"]))
    today = datetime.now(UTC).date()
    for kind, item_id, name, label, raw_date in checks:
        try:
            expiry = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
        except ValueError:
            try:
                expiry = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
        days = (expiry - today).days
        if days < -14:
            stage = "expired"
        elif days < 0:
            stage = "grace"
        else:
            stage = next((str(t) for t in thresholds if days <= t), None)
        if stage is None:
            continue
        dedupe = f"expiry:{kind}:{item_id}:{label}:{stage}"
        if con.execute(
            "SELECT id FROM app_notifications WHERE type='expiry' AND related_id=?",
            (dedupe,),
        ).fetchone():
            continue
        title = (
            f"انتهت صلاحية {label}"
            if stage == "expired"
            else f"{label} في فترة السماح"
            if stage == "grace"
            else f"{label} قريب الانتهاء"
        )
        message = f"{name} - {raw_date}" + (
            f" - متبقٍ {days} يوم" if days >= 0 else f" - مضى {abs(days)} يوم"
        )
        create_notification(
            con, "admin", "", title, message, type_="expiry", related_id=dedupe,
            priority="urgent" if days <= 3 else "high",
            action_text="فتح الملف", action_route=f"admin:{kind}:{item_id}",
        )
        if kind == "provider" and label == "الاشتراك":
            create_notification(
                con, "provider", item_id, title, message, type_="expiry", related_id=dedupe,
                priority="urgent" if days <= 3 else "high",
                action_text="تجديد الباقة", action_route="provider:subscription",
            )


def create_marketplace_notifications(con, released):
    for item in released:
        create_notification(
            con, "provider", item["providerId"], "طلب مناسب لخدمتك",
            f"{item['serviceName']} - {item['area'] or 'الموقع داخل الطلب'}",
            type_="request", related_id=item["requestId"], priority="high",
            action_text="فتح الطلب", action_route=f"provider:request:{item['requestId']}",
        )


def run_domain_maintenance(con):
    """Synchronize access and release due request waves without a separate queue service."""
    state_changes = SubscriptionService(con).synchronize_all()
    for change in state_changes:
        subscription = change.get("subscription") or {}
        provider_id = change.get("providerId", "")
        state = change.get("state", "")
        related = f"subscription-state:{subscription.get('id', provider_id)}:{state}"
        exists = con.execute(
            """SELECT id FROM app_notifications WHERE target_kind='provider'
            AND target_id=? AND type='subscription' AND related_id=? LIMIT 1""",
            (provider_id, related),
        ).fetchone()
        if not exists:
            title = "تحديث حالة الاشتراك"
            message = {
                "expiring": "اشتراكك قريب الانتهاء. راجع التجديد للحفاظ على ظهور بطاقتك.",
                "grace": "اشتراكك في فترة السماح. بياناتك محفوظة ويمكنك التجديد الآن.",
                "expired": "انتهى الاشتراك وتوقف الظهور واستقبال الطلبات فقط. بيانات الحساب محفوظة.",
                "active": "اشتراكك نشط وعاد ظهور البطاقة واستقبال الطلبات.",
                "foundation": "فترة التأسيس نشطة.",
            }.get(state, "تم تحديث حالة اشتراكك.")
            create_notification(
                con, "provider", provider_id, title, message,
                type_="subscription", related_id=related,
                priority="high" if state in {"grace", "expired"} else "normal",
                action_text="إدارة الاشتراك", action_route="provider:subscription",
            )
            create_notification(
                con, "admin", "", title, f"{provider_id}: {message}",
                type_="subscription", related_id=related,
                priority="high" if state in {"grace", "expired"} else "normal",
                action_text="فتح الاشتراك", action_route=f"admin:subscription:{subscription.get('id', '')}",
            )
    released = RequestMarketplace(con).release_due()
    create_marketplace_notifications(con, released)
    scan_expirations(con)
    return {"stateChanges": len(state_changes), "releasedRequests": len(released)}


def get_bootstrap(session=None):
    with db() as con:
        maintenance = run_domain_maintenance(con)
        categories = []
        for c in con.execute("SELECT * FROM categories ORDER BY rowid"):
            cd = dict(c)
            cd["active"] = bool(cd["active"])
            cd["services"] = [dict(s) | {"active": bool(s["active"])} for s in con.execute("SELECT id,icon,ar,en,active FROM services WHERE category_id=? ORDER BY rowid", (c["id"],))]
            categories.append(cd)
        is_admin = bool(session and session.get("kind") == "admin")
        is_provider = bool(session and session.get("kind") == "provider")
        is_pending_provider = bool(session and session.get("kind") == "provider_pending")
        is_user = bool(session and session.get("kind") == "user")
        if is_admin:
            provider_rows = con.execute(
                "SELECT * FROM providers ORDER BY featured DESC,quality_score DESC,rating DESC"
            )
        elif is_provider:
            provider_rows = con.execute(
                """SELECT * FROM providers WHERE id=? OR (
                active=1 AND verified=1 AND status!='unavailable' AND COALESCE(listing_enabled,1)=1)
                ORDER BY featured DESC,quality_score DESC,rating DESC""",
                (session["providerId"],),
            )
        else:
            provider_rows = con.execute(
                """SELECT * FROM providers WHERE active=1 AND verified=1 AND status!='unavailable'
                AND COALESCE(listing_enabled,1)=1
                ORDER BY featured DESC,quality_score DESC,rating DESC"""
            )
        providers = [
            row_provider(
                r,
                private=is_admin or bool(is_provider and r["id"] == session.get("providerId")),
                sign_private=is_admin or bool(is_provider and r["id"] == session.get("providerId")),
            )
            for r in provider_rows
        ]
        requests = []
        if has_permission(session, "review_requests") or is_pending_provider:
            if is_pending_provider:
                request_rows = con.execute(
                    "SELECT * FROM provider_requests WHERE id=?",
                    (session.get("requestId", ""),),
                )
            else:
                request_rows = con.execute(
                    "SELECT * FROM provider_requests ORDER BY created_at DESC"
                )
            for r in request_rows:
                payload = jload(r["payload"], {})
                payload["services"] = payload.get("services", [])
                if not payload["services"] and "|" in payload.get("service", ""):
                    cat_id, service_id = payload["service"].split("|", 1)
                    payload["services"] = [{"id": f"pending-{payload.get('id','')}", "catId": cat_id, "serviceId": service_id, "priceFrom": payload.get("priceFrom", 0), "active": True, "areas": [payload.get("wilayah", "")]}]
                requests.append(provider_request_view(payload, r["created_at"]))
        platform_row = con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()
        platform_settings = jload(platform_row["value"], {}) if platform_row else {}
        public_setting_keys = {
            "nameAr", "nameEn", "defaultGov", "adIntervalSeconds", "displayScale",
            "uiMode", "maxHomeProviders", "maxPopularServices", "maxRequestMatches",
            "loyaltyEnabled", "requestBoardEnabled", "contactApprovalRequired",
            "subscriptionsEnabled", "paymentGatewayEnabled", "serviceAreas",
            "deviceNotifications", "mergeNotifications",
        }
        settings = platform_settings if is_admin else {
            key: value for key, value in platform_settings.items() if key in public_setting_keys
        }
        packages = [
            row_package(r) for r in con.execute(
                "SELECT * FROM packages WHERE active=1 AND COALESCE(legacy,0)=0 ORDER BY price,duration_days"
            )
        ]
        if is_admin:
            reviews = [row_review(r, private=True) for r in con.execute("SELECT * FROM reviews ORDER BY created_at DESC")]
            complaints = [row_complaint(r, private=True) for r in con.execute("SELECT * FROM complaints ORDER BY created_at DESC")]
            subscriptions = [row_subscription(r) for r in con.execute("SELECT * FROM subscriptions ORDER BY created_at DESC")]
            payments = [row_payment(r) for r in con.execute("SELECT * FROM payments ORDER BY created_at DESC")]
            audits = [row_audit(r) for r in con.execute("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 80")]
            leads = [row_lead(r) for r in con.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT 120")]
        elif is_provider:
            pid = session["providerId"]
            reviews = [row_review(r) for r in con.execute("SELECT * FROM reviews WHERE provider_id=? AND approved=1 ORDER BY created_at DESC", (pid,))]
            complaints = [row_complaint(r) for r in con.execute("SELECT * FROM complaints WHERE provider_id=? ORDER BY created_at DESC", (pid,))]
            subscriptions = [row_subscription(r) for r in con.execute("SELECT * FROM subscriptions WHERE provider_id=? ORDER BY created_at DESC", (pid,))]
            payments = [row_payment(r) for r in con.execute("SELECT * FROM payments WHERE provider_id=? ORDER BY created_at DESC", (pid,))]
            leads = [row_lead(r) for r in con.execute("SELECT * FROM leads WHERE provider_id=? ORDER BY created_at DESC LIMIT 80", (pid,))]
            current_provider = next((p for p in providers if p["id"] == pid), None)
            if current_provider:
                open_requests = [row_lead(r) for r in con.execute("SELECT * FROM leads WHERE kind='request' AND COALESCE(provider_id,'')='' AND status NOT IN ('cancelled','deleted','closed') ORDER BY created_at DESC LIMIT 120")]
                matched = [lead for lead in open_requests if lead_matches_provider(lead, current_provider)]
                leads = leads + matched[:40]
            audits = []
        else:
            reviews = [row_review(r) for r in con.execute("SELECT * FROM reviews WHERE approved=1 ORDER BY created_at DESC")]
            complaints, subscriptions, payments, audits, leads = [], [], [], [], []
        all_customer_requests = [
            row_customer_request(r, sign_private=bool(is_admin or is_provider or is_user))
            for r in con.execute("SELECT * FROM customer_requests ORDER BY created_at DESC LIMIT 300")
        ]
        marketplace_requests = [
            marketplace_request(item, include_note=is_user)
            for item in all_customer_requests
            if item.get("status") in ACTIVE_REQUEST_STATES and item.get("offersOpen", True)
        ]
        if is_admin or is_provider:
            marketplace_requests = []
        if is_admin:
            customer_requests = all_customer_requests
            for item in customer_requests:
                item["providerSuggestions"] = request_suggestions(con, item["id"], include_hidden=True)
            notifications = [
                row_notification(r)
                for r in con.execute("SELECT * FROM app_notifications ORDER BY created_at DESC LIMIT 300")
            ]
            users = [
                row_app_user(r, private=True, sign_private=True)
                for r in con.execute("SELECT * FROM app_users ORDER BY last_login DESC LIMIT 300")
            ]
            advertisements = [
                row_advertisement(r)
                for r in con.execute("SELECT * FROM advertisements ORDER BY created_at DESC")
            ]
        elif is_provider:
            pid = session["providerId"]
            customer_requests = [
                item for item in all_customer_requests
                if pid in item["matchingProviderIds"] or item["acceptedProviderId"] == pid
            ]
            consent_service = ContactConsentService(con)
            for item in customer_requests:
                consent = consent_service.summary(item["id"], pid)
                item["contactConsent"] = consent
                if item.get("acceptedProviderId") != pid or not (
                    consent.get("whatsapp") or consent.get("call")
                ):
                    item["phone"] = ""
            request_lookup = {item.get("id"): item for item in customer_requests}
            for lead in leads:
                linked = request_lookup.get(lead.get("request_id") or lead.get("requestId") or lead.get("id"))
                consent = (linked or {}).get("contactConsent") or {}
                if not linked or linked.get("acceptedProviderId") != pid or not (
                    consent.get("whatsapp") or consent.get("call")
                ):
                    lead["phone"] = ""
            notifications = [
                row_notification(r)
                for r in con.execute(
                    """SELECT * FROM app_notifications
                    WHERE target_kind='provider' AND target_id=? ORDER BY created_at DESC LIMIT 160""",
                    (pid,),
                )
            ]
            users = []
            advertisements = [
                row_advertisement(r)
                for r in con.execute(
                    "SELECT * FROM advertisements WHERE active=1 AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"
                )
            ]
        elif is_user:
            uid = session["userId"]
            customer_requests = [item for item in all_customer_requests if item["userId"] == uid]
            for item in customer_requests:
                item["providerSuggestions"] = request_suggestions(con, item["id"])
            marketplace_requests = [item for item in marketplace_requests if item["id"] not in {request["id"] for request in customer_requests}]
            for item in marketplace_requests:
                item["mySuggestedProviderIds"] = [
                    row["provider_id"]
                    for row in con.execute(
                        """SELECT provider_id FROM request_provider_suggestions
                        WHERE request_id=? AND suggested_by_user_id=? AND status IN ('active','selected')""",
                        (item["id"], uid),
                    )
                ]
            consent_service = ContactConsentService(con)
            for item in customer_requests:
                if item.get("acceptedProviderId"):
                    item["contactConsent"] = consent_service.summary(
                        item["id"], item["acceptedProviderId"]
                    )
                    if item["contactConsent"].get("whatsapp") or item["contactConsent"].get("call"):
                        contact_row = con.execute(
                            "SELECT phone FROM providers WHERE id=?",
                            (item["acceptedProviderId"],),
                        ).fetchone()
                        if contact_row:
                            item["providerContact"] = {"phone": contact_row["phone"]}
            notifications = [
                row_notification(r)
                for r in con.execute(
                    """SELECT * FROM app_notifications
                    WHERE target_kind='user' AND target_id=? ORDER BY created_at DESC LIMIT 160""",
                    (uid,),
                )
            ]
            user_row = con.execute("SELECT * FROM app_users WHERE id=?", (uid,)).fetchone()
            users = [row_app_user(user_row, private=True, sign_private=True)] if user_row else []
            advertisements = [
                row_advertisement(r)
                for r in con.execute(
                    "SELECT * FROM advertisements WHERE active=1 AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"
                )
            ]
        else:
            customer_requests, notifications, users = [], [], []
            advertisements = [
                row_advertisement(r)
                for r in con.execute(
                    "SELECT * FROM advertisements WHERE active=1 AND COALESCE(deleted_at,'')='' ORDER BY created_at DESC"
                )
            ]
        payment_revenue = con.execute(
            """SELECT COALESCE(SUM(amount),0) n FROM payments
            WHERE kind IN ('revenue','subscription','promotion') AND status='paid'"""
        ).fetchone()["n"]
        finance_revenue = con.execute("SELECT COALESCE(SUM(amount),0) n FROM finance WHERE kind='revenue'").fetchone()["n"]
        stats = {
            "providers": len(providers),
            "activeProviders": len([p for p in providers if p["active"]]),
            "requests": con.execute("SELECT COUNT(*) n FROM provider_requests").fetchone()["n"],
            "leads": con.execute("SELECT COUNT(*) n FROM leads").fetchone()["n"],
            "revenue": payment_revenue + finance_revenue,
            "reviews": con.execute("SELECT COUNT(*) n FROM reviews WHERE approved=1").fetchone()["n"],
            "openComplaints": con.execute("SELECT COUNT(*) n FROM complaints WHERE status!='closed'").fetchone()["n"],
            "activeSubscriptions": con.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='active'").fetchone()["n"],
            "qualityAverage": round(con.execute("SELECT COALESCE(AVG(quality_score),0) n FROM providers").fetchone()["n"], 1),
            "whatsappLogs": con.execute("SELECT COUNT(*) n FROM whatsapp_logs").fetchone()["n"],
            "users": con.execute("SELECT COUNT(*) n FROM app_users WHERE status='active'").fetchone()["n"],
            "customerRequests": con.execute("SELECT COUNT(*) n FROM customer_requests").fetchone()["n"],
            "unavailableRequests": con.execute(
                "SELECT COUNT(*) n FROM customer_requests WHERE status='unavailable'"
            ).fetchone()["n"],
            "unreadNotifications": con.execute(
                "SELECT COUNT(*) n FROM app_notifications WHERE is_read=0"
            ).fetchone()["n"] if is_admin else len([n for n in notifications if not n["read"]]),
        }
        if not is_admin:
            stats = {
                key: stats[key]
                for key in ("providers", "activeProviders", "reviews", "qualityAverage", "unreadNotifications")
            }
        reports = {
            "topProviders": sorted(
                [{"id": p["id"], "name": p["name"], "rating": p["rating"], "qualityScore": p["qualityScore"], "stats": p["stats"]} for p in providers],
                key=lambda p: (p["qualityScore"], p["rating"], p["stats"].get("whatsapp", 0)),
                reverse=True,
            )[:8],
            "qualityQueue": [
                {"id": p["id"], "name": p["name"], "qualityScore": p["qualityScore"], "rating": p["rating"], "reviews": p["reviews"]}
                for p in providers if p["qualityScore"] < 65 or p["reviews"] == 0
            ][:12],
            "subscriptionRevenue": payment_revenue,
            "complaintsByStatus": {
                row["status"]: row["n"] for row in con.execute("SELECT status, COUNT(*) n FROM complaints GROUP BY status")
            },
        }
        if not is_admin:
            reports = {}
        admin_entities = {}
        financial_metrics = {}
        if is_admin:
            state_counts = {
                row["status"]: int(row["n"])
                for row in con.execute("SELECT status,COUNT(*) n FROM subscriptions GROUP BY status")
            }
            recurring = con.execute(
                """SELECT COALESCE(SUM(p.price * 30.4375 / NULLIF(p.duration_days,0)),0) mrr
                FROM subscriptions s JOIN packages p ON p.id=s.package_id
                WHERE s.status IN ('foundation','active','expiring','grace') AND p.price>0"""
            ).fetchone()["mrr"]
            subscriber_count = con.execute(
                """SELECT COUNT(DISTINCT provider_id) n FROM payments
                WHERE status='paid' AND amount>0"""
            ).fetchone()["n"]
            failed_payments = con.execute(
                "SELECT COUNT(*) n FROM payments WHERE status IN ('failed','cancelled')"
            ).fetchone()["n"]
            paid_requests = con.execute(
                "SELECT COUNT(*) n FROM subscriptions WHERE status='pending_payment'"
            ).fetchone()["n"]
            paid_activated = con.execute(
                """SELECT COUNT(*) n FROM subscriptions WHERE amount>0
                AND status IN ('active','expiring','grace','expired','cancelled','refunded')"""
            ).fetchone()["n"]
            churned = sum(state_counts.get(key, 0) for key in ("expired", "cancelled", "refunded"))
            subscription_total = sum(state_counts.values()) or 1
            financial_metrics = {
                "currency": OMR,
                "mrr": round(float(recurring or 0), 3),
                "arr": round(float(recurring or 0) * 12, 3),
                "averageRevenuePerProvider": round(float(payment_revenue or 0) / max(1, int(subscriber_count or 0)), 3),
                "failedPayments": int(failed_payments or 0),
                "conversionRate": round(100 * int(paid_activated or 0) / max(1, int(paid_activated or 0) + int(paid_requests or 0)), 1),
                "churnRate": round(100 * churned / subscription_total, 1),
                "subscriptionStates": state_counts,
            }
            if has_permission(session, "manage_subscriptions"):
                admin_entities.update({
                    "subscriptionEvents": [dict(r) for r in con.execute(
                        "SELECT * FROM subscription_events ORDER BY created_at DESC LIMIT 300"
                    )],
                    "legacyPackages": [row_package(r) for r in con.execute(
                        "SELECT * FROM packages WHERE COALESCE(legacy,0)=1 ORDER BY rowid DESC"
                    )],
                    "coupons": [dict(r) | {"active": bool(r["active"]), "appliesTo": jload(r["applies_to"], [])} for r in con.execute(
                        "SELECT * FROM coupons ORDER BY created_at DESC"
                    )],
                })
            if has_permission(session, "manage_finance"):
                admin_entities["invoices"] = [dict(r) for r in con.execute(
                    "SELECT * FROM invoices ORDER BY issued_at DESC LIMIT 300"
                )]
            if has_permission(session, "manage_campaigns"):
                admin_entities.update({
                    "campaigns": [dict(r) | {"rules": jload(r["rules"], {})} for r in con.execute(
                        "SELECT * FROM campaigns ORDER BY created_at DESC"
                    )],
                    "promotions": [dict(r) for r in con.execute(
                        "SELECT * FROM provider_promotions ORDER BY created_at DESC"
                    )],
                })
            if has_permission(session, "manage_team"):
                admin_entities.update({
                    "teamMembers": [
                        {k: v for k, v in dict(r).items() if k != "pin_hash"}
                        for r in con.execute("SELECT * FROM provider_team_members ORDER BY created_at DESC")
                    ],
                    "branches": [dict(r) for r in con.execute(
                        "SELECT * FROM provider_branches ORDER BY created_at DESC"
                    )],
                })
            if has_permission(session, "manage_consent"):
                admin_entities["contactConsents"] = [dict(r) for r in con.execute(
                    "SELECT * FROM contact_consents ORDER BY updated_at DESC LIMIT 300"
                )]
        data = {
            "categories": categories,
            "providers": providers,
            "requests": requests,
            "reviews": reviews,
            "complaints": complaints,
            "packages": packages,
            "subscriptions": subscriptions,
            "payments": payments,
            "leads": leads,
            "auditLogs": audits,
            "customerRequests": customer_requests,
            "marketplaceRequests": marketplace_requests,
            "notifications": notifications,
            "users": users,
            "advertisements": advertisements,
            "serviceAvailability": service_availability_snapshot(con),
            "settings": settings,
            "appConfig": {
                "nameAr": "خدماتي",
                "nameEn": "Khadamati App",
                "supportEmail": SUPPORT_EMAIL,
                "policyVersion": POLICY_VERSION,
                "currency": OMR,
            },
            "stats": stats,
            "reports": reports,
            "financialMetrics": financial_metrics,
            "adminEntities": admin_entities,
            "maintenance": maintenance if is_admin else {},
            "integrations": {
                "whatsappConfigured": whatsapp_configured(),
                "paymentConfigured": PaymentAdapter(con).configured,
                "otpDeliveryConfigured": whatsapp_configured() or (
                    APP_ENV != "production" and bool(os.environ.get("KHADAMATI_DEV_OTP_CODE"))
                ),
                "postgresReady": True,
            },
            "permissions": ALL_PERMISSIONS if is_admin else [],
        }
        if session and session.get("kind") == "admin":
            data["adminUser"] = {k: session[k] for k in ("id", "name", "role", "permissions")}
            if has_permission(session, "manage_admins"):
                data["adminUsers"] = [admin_public(r) for r in con.execute("SELECT * FROM admin_users ORDER BY created_at")]
        elif is_user and users:
            data["currentUser"] = users[0]
        if is_provider:
            provider_id = session["providerId"]
            data["providerEntitlements"] = EntitlementService(con).for_provider(provider_id)
            data["currentProvider"] = next((p for p in providers if p["id"] == provider_id), None)
            data["providerSession"] = {
                "role": session.get("role", "provider_owner"),
                "memberId": session.get("memberId", ""),
                "permissions": session.get("providerPermissions", []),
            }
            data["providerTeam"] = [
                {k: v for k, v in dict(r).items() if k != "pin_hash"}
                for r in con.execute(
                    "SELECT * FROM provider_team_members WHERE provider_id=? ORDER BY created_at",
                    (provider_id,),
                )
            ]
            data["providerBranches"] = [
                dict(r) for r in con.execute(
                    "SELECT * FROM provider_branches WHERE provider_id=? ORDER BY created_at",
                    (provider_id,),
                )
            ]
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
    try:
        with db() as con:
            con.execute("INSERT INTO whatsapp_logs VALUES(?,?,?,?,CURRENT_TIMESTAMP)", (slug("wa"), target, status, detail[:900]))
    except sqlite3.OperationalError as err:
        print(f"WhatsApp log skipped: {err}", flush=True)


def send_whatsapp(to, text):
    target = normalize_phone(to)
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    version = os.environ.get("WHATSAPP_API_VERSION", "v20.0")
    if not token or not phone_id or not target:
        return {"ok": False, "configured": False}
    if not re.fullmatch(r"v\d{1,2}\.\d{1,2}", version) or not str(phone_id).isdigit():
        return {"ok": False, "configured": False, "error": "invalid_whatsapp_configuration"}
    payload = {
        "messaging_product": "whatsapp",
        "to": target,
        "type": "text",
        "text": {"preview_url": False, "body": text[:3500]},
    }
    connection = http.client.HTTPSConnection("graph.facebook.com", 443, timeout=12)
    try:
        connection.request(
            "POST", f"/{version}/{phone_id}/messages", body=jdump(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        if 200 <= response.status < 300:
            log_whatsapp(target, "sent", body)
            return {"ok": True, "configured": True, "response": jload(body, {})}
        log_whatsapp(target, "failed", body)
        return {"ok": False, "configured": True, "error": body}
    except Exception as err:
        log_whatsapp(target, "failed", str(err))
        return {"ok": False, "configured": True, "error": "delivery_failed"}
    finally:
        connection.close()


def save_upload_data(owner_id, data_url, slot, allowed_mimes, max_bytes):
    if not data_url:
        return ""
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError("invalid_upload")
    head, raw = data_url.split(";base64,", 1)
    mime = head.replace("data:", "")
    ext = allowed_mimes.get(mime)
    if not ext:
        raise ValueError("unsupported_upload_type")
    blob = base64.b64decode(raw, validate=True)
    if len(blob) > max_bytes:
        raise ValueError("upload_too_large")
    if not upload_signature_matches(mime, blob):
        raise ValueError("upload_content_mismatch")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_owner = "".join(ch for ch in str(owner_id) if ch.isalnum() or ch in ("_", "-"))[:60] or "file"
    safe_slot = "".join(ch for ch in str(slot) if ch.isalnum() or ch in ("_", "-"))[:40] or secrets.token_hex(4)
    filename = f"{safe_owner}-{safe_slot}-{secrets.token_hex(12)}.{ext}"
    rel = f"uploads/{filename}"
    (UPLOAD_DIR / filename).write_bytes(blob)
    return rel


def save_data_url(provider_id, image_data):
    return save_upload_data(provider_id, image_data, "avatar", IMAGE_MIMES, 2_500_000)


def save_many_images(owner_id, images, prefix="work", limit=5):
    paths = []
    for i, image_data in enumerate((images or [])[:limit], 1):
        if image_data:
            paths.append(save_upload_data(owner_id, image_data, f"{prefix}{i}", IMAGE_MIMES, 2_500_000))
    return paths


def save_many_documents(owner_id, docs, prefix="doc", limit=3):
    paths = []
    for i, doc_data in enumerate((docs or [])[:limit], 1):
        if doc_data:
            paths.append(save_upload_data(owner_id, doc_data, f"{prefix}{i}", DOCUMENT_MIMES, 5_000_000))
    return paths


def upsert_provider(con, data):
    p = data | {"id": data.get("id") or slug("p")}
    p["id"] = safe_text(p["id"], 120)
    p["name"] = safe_text(data.get("name", ""), 120)
    p["phone"] = normalize_phone(data.get("phone", ""))
    existing = con.execute("SELECT * FROM providers WHERE id=?", (p["id"],)).fetchone()
    existing_provider = row_provider(existing, private=True) if existing else {}
    if not p["id"] or not p["name"] or len(p["phone"]) < 8:
        raise DomainError("provider_identity_required", 400)
    p["status"] = safe_text(data.get("status", existing_provider.get("status", "available")), 30)
    if p["status"] not in {"available", "busy", "unavailable", "under_review", "pending", "suspended", "deleted"}:
        raise DomainError("invalid_provider_status", 400)
    p["providerType"] = safe_text(
        data.get("providerType", existing_provider.get("providerType", "individual")), 30
    )
    if p["providerType"] not in {"individual", "company"}:
        raise DomainError("invalid_provider_type", 400)
    image_path = data.get("imagePath") or ""
    if data.get("imageData"):
        image_path = save_data_url(p["id"], data["imageData"])
    elif not image_path:
        image_path = existing_provider.get("imagePath", "")
    pin_hash = data.get("pinHash") or ""
    if data.get("pin"):
        pin_hash = hash_pin(data["pin"])
    if not pin_hash:
        pin_hash = existing["pin_hash"] if existing else ""
    work_images = data.get("workImages") or existing_provider.get("workImages", [])
    if data.get("workImagesData"):
        limit = 15 if data.get("providerType", existing_provider.get("providerType")) == "company" else 5
        new_images = save_many_images(
            p["id"], data.get("workImagesData"),
            f"work{int(time.time())}-", max(0, limit - len(work_images)),
        )
        work_images = list(dict.fromkeys([*work_images, *new_images]))[:limit]
    card_image = data.get("cardImage") or existing_provider.get("cardImage", "") or image_url(image_path)
    if isinstance(card_image, str) and card_image.startswith("data:"):
        if card_image == data.get("imageData"):
            card_image = image_url(image_path)
        else:
            source_images = data.get("workImagesData") or []
            try:
                card_image = image_url(work_images[source_images.index(card_image)])
            except (ValueError, IndexError):
                card_image = image_url(image_path)
    documents = data.get("documents") or existing_provider.get("documents", [])
    if data.get("documentsData"):
        new_documents = save_many_documents(
            p["id"], data.get("documentsData"),
            f"doc{int(time.time())}-", max(0, 6 - len(documents)),
        )
        documents = list(dict.fromkeys([*documents, *new_documents]))[:6]
    before_after = data.get("beforeAfter")
    if before_after is None:
        before_after = existing_provider.get("beforeAfter", [])
    before_after = [
        {
            **item,
            "before": str(item.get("before", "")).lstrip("/")
            if str(item.get("before", "")).startswith("/uploads/")
            else item.get("before", ""),
            "after": str(item.get("after", "")).lstrip("/")
            if str(item.get("after", "")).startswith("/uploads/")
            else item.get("after", ""),
        }
        for item in before_after
        if isinstance(item, dict) and item.get("before") and item.get("after")
    ][:8]
    pair_data = data.get("beforeAfterData") or {}
    if pair_data.get("before") and pair_data.get("after"):
        pair_id = slug("compare")
        pair_paths = save_many_images(
            p["id"], [pair_data["before"], pair_data["after"]], pair_id, 2
        )
        if len(pair_paths) == 2:
            before_after.append(
                {
                    "id": pair_id,
                    "before": pair_paths[0],
                    "after": pair_paths[1],
                    "caption": str(pair_data.get("caption", "") or "")[:120],
                    "createdAt": datetime.now(UTC).isoformat(),
                }
            )
            before_after = before_after[-8:]
    intro_video_url = data.get(
        "introVideoUrl", existing_provider.get("introVideoUrl", "")
    )
    if data.get("introVideoData"):
        intro_video_url = save_upload_data(
            p["id"], data["introVideoData"], "intro", VIDEO_MIMES, 12_000_000
        )
    if isinstance(intro_video_url, str) and intro_video_url.startswith("/uploads/"):
        intro_video_url = intro_video_url.lstrip("/")
    location = normalized_location(data.get("location") or existing_provider.get("location") or {})
    con.execute(
        """INSERT INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
        package_id,rating,reviews,admin_note,image_path,card_image,pin_hash,services,work_images,documents,quality_score,response_score,
        subscription_until,subscription_start,provider_type,company_name,company_id,commercial_no,
        verification_expiry,commercial_expiry,license_expiry,latitude,longitude,location_updated_at,
        map_visible,primary_service_id,stats)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,phone=excluded.phone,gov=excluded.gov,
        wilayah=excluded.wilayah,areas=excluded.areas,bio=excluded.bio,hours=excluded.hours,status=excluded.status,
        active=excluded.active,verified=excluded.verified,featured=excluded.featured,package_id=excluded.package_id,
        rating=excluded.rating,reviews=excluded.reviews,admin_note=excluded.admin_note,image_path=excluded.image_path,card_image=excluded.card_image,
        pin_hash=excluded.pin_hash,services=excluded.services,work_images=excluded.work_images,documents=excluded.documents,
        quality_score=excluded.quality_score,response_score=excluded.response_score,subscription_until=excluded.subscription_until,
        subscription_start=excluded.subscription_start,provider_type=excluded.provider_type,
        company_name=excluded.company_name,company_id=excluded.company_id,commercial_no=excluded.commercial_no,
        verification_expiry=excluded.verification_expiry,commercial_expiry=excluded.commercial_expiry,
        license_expiry=excluded.license_expiry,latitude=excluded.latitude,longitude=excluded.longitude,
        location_updated_at=excluded.location_updated_at,map_visible=excluded.map_visible,
        primary_service_id=excluded.primary_service_id""",
        (
            p["id"], p.get("name", ""), p.get("phone", ""), p.get("gov", ""), p.get("wilayah", ""),
            jdump(p.get("areas", [])), p.get("bio", ""), p.get("hours", ""), p.get("status", "available"),
            int(bool(p.get("active", True))), int(bool(p.get("verified", False))), int(bool(p.get("featured", False))),
            p.get("packageId", existing_provider.get("packageId", "foundation_12m")),
            finite_number(p.get("rating", existing_provider.get("rating", 0)), minimum=0, maximum=5),
            int(finite_number(p.get("reviews", existing_provider.get("reviews", 0)), minimum=0, maximum=10_000_000)),
            p.get("adminNote", ""), image_path, card_image, pin_hash, jdump(p.get("services", [])), jdump(work_images), jdump(documents),
            int(finite_number(p.get("qualityScore", existing_provider.get("qualityScore", 60)), default=60, minimum=0, maximum=100)),
            int(finite_number(p.get("responseScore", existing_provider.get("responseScore", 70)), default=70, minimum=0, maximum=100)),
            p.get("subscriptionUntil", existing_provider.get("subscriptionUntil", "")),
            p.get("subscriptionStart", existing_provider.get("subscriptionStart", "")),
            p.get("providerType", existing_provider.get("providerType", "individual")),
            p.get("companyName", existing_provider.get("companyName", "")),
            p.get("companyId", existing_provider.get("companyId", "")),
            p.get("commercialNo", existing_provider.get("commercialNo", "")),
            p.get("verificationExpiry", existing_provider.get("verificationExpiry", "")),
            p.get("commercialExpiry", existing_provider.get("commercialExpiry", "")),
            p.get("licenseExpiry", existing_provider.get("licenseExpiry", "")),
            location.get("lat"),
            location.get("lng"),
            location.get("updatedAt", ""),
            int(bool(p.get("mapVisible", existing_provider.get("mapVisible", True)))),
            safe_text(
                p.get("primaryServiceId", existing_provider.get("primaryServiceId", "")), 80
            ),
            jdump(p.get("stats", existing_provider.get("stats", {"views": 0, "whatsapp": 0, "calls": 0}))),
        ),
    )
    con.execute(
        """UPDATE providers SET before_after=?,intro_video_url=?,availability=?,
        response_minutes=?,completed_jobs=? WHERE id=?""",
        (
            jdump(before_after), intro_video_url,
            jdump(p.get("availability", existing_provider.get("availability", {}))),
            int(finite_number(p.get("responseMinutes", existing_provider.get("responseMinutes", 30)), default=30, minimum=0, maximum=100_000)),
            int(finite_number(p.get("completedJobs", existing_provider.get("completedJobs", 0)), minimum=0, maximum=100_000_000)),
            p["id"],
        ),
    )
    p["imagePath"] = image_path
    p["cardImage"] = card_image
    p["workImages"] = work_images
    p["documents"] = documents
    p["beforeAfter"] = before_after
    p["introVideoUrl"] = image_url(intro_video_url)
    recompute_provider_quality(con, p["id"])
    return p


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), geolocation=(self), microphone=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https:; "
            "img-src 'self' data: blob: https:; media-src 'self' data: blob: https:; "
            "font-src 'self' data: https:; connect-src 'self' https://khadamati-app-api.onrender.com https:; "
            "frame-src https://www.openstreetmap.org; worker-src 'self' blob:; manifest-src 'self'",
        )
        if self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        origin = self.headers.get("Origin", "").rstrip("/")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def send_json(self, data, status=200):
        raw = jdump(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_bytes(self, raw, content_type, filename=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        path = urlparse(self.path).path
        raw = self.read_raw(JSON_LIMITS.get(path, DEFAULT_JSON_LIMIT))
        if not raw:
            return {}
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise DomainError("json_object_required", 400)
        return value

    def read_raw(self, max_bytes=1_000_000):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError) as error:
            raise DomainError("invalid_content_length", 400) from error
        if length < 0 or length > max_bytes:
            raise DomainError("request_too_large", 413)
        return self.rfile.read(length) if length else b""

    def client_key(self):
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        remote = forwarded or (self.client_address[0] if self.client_address else "unknown")
        return hashlib.sha256(remote.encode("utf-8")).hexdigest()[:32]

    def send_domain_error(self, error):
        return self.send_json(
            {"error": error.code, "detail": error.detail or ""}, error.status
        )

    def session(self):
        return token_session(self.headers)

    def require_admin(self, permission="view_reports"):
        session = self.session()
        if not has_permission(session, permission):
            self.send_json({"error": "permission_denied", "permission": permission}, 403)
            return None
        return session

    def require_provider(self, permission=""):
        session = self.session()
        if not session or session.get("kind") != "provider":
            self.send_json({"error": "provider_auth_required"}, 401)
            return None
        role = session.get("role", "provider_owner")
        selected = set(session.get("providerPermissions") or [])
        allowed = PROVIDER_ROLE_PERMISSIONS.get(role, set()) | selected
        if permission and permission not in allowed:
            self.send_json({"error": "provider_permission_denied", "permission": permission}, 403)
            return None
        return session

    def require_user(self):
        session = self.session()
        if not session or session.get("kind") != "user":
            self.send_json({"error": "user_auth_required"}, 401)
            return None
        return session

    def send_upload(self, path):
        filename = upload_filename(path)
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
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/media/"):
            filename = path.removeprefix("/media/")
            query = parse_qs(parsed.query)
            if not valid_media_signature(
                filename,
                (query.get("exp") or [""])[0],
                (query.get("sig") or [""])[0],
            ):
                return self.send_json({"error": "media_link_invalid_or_expired"}, 403)
            return self.send_upload(path)
        if path.startswith("/uploads/"):
            if is_private_upload(path):
                session = self.session()
                if not (
                    has_permission(session, "review_requests")
                    or has_permission(session, "manage_providers")
                ):
                    return self.send_json({"error": "private_media_requires_signed_url"}, 403)
            return self.send_upload(path)
        if path.startswith("/share/provider/"):
            provider_id = path.rsplit("/", 1)[-1]
            with db() as con:
                row = con.execute("SELECT * FROM providers WHERE id=? AND active=1", (provider_id,)).fetchone()
            if not row:
                return self.send_error(404)
            provider = row_provider(row)
            host = self.headers.get("Host", "localhost")
            scheme = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0]
            image_path = provider.get("cardImage") or provider.get("imageUrl") or "/app-icon-512.png"
            image = image_path if str(image_path).startswith("http") else f"{scheme}://{host}{image_path}"
            target = f"{PUBLIC_APP_URL}#provider={provider_id}"
            page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <title>{html.escape(provider['name'])} | خدماتي</title>
            <meta property="og:type" content="profile"><meta property="og:site_name" content="خدماتي">
            <meta property="og:title" content="{html.escape(provider['name'])}">
            <meta property="og:description" content="{html.escape(provider.get('bio') or 'مزود خدمة على منصة خدماتي')}">
            <meta property="og:image" content="{html.escape(image)}">
            <meta property="og:url" content="{html.escape(f'{scheme}://{host}{path}')}">
            <style>body{{font-family:Arial;background:#f4f6fb;color:#102a43;display:grid;place-items:center;min-height:100vh}}
            a{{background:#168f7a;color:white;padding:14px 22px;border-radius:12px;text-decoration:none;font-weight:bold}}</style>
            <meta http-equiv="refresh" content="1;url={html.escape(target)}"></head>
            <body><a href="{html.escape(target)}">فتح بطاقة {html.escape(provider['name'])} في خدماتي</a></body></html>"""
            return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/classic-state":
            session = self.require_admin("manage_settings")
            if not session:
                return
            state = get_classic_state()
            return self.send_json({"ok": True, "state": state})
        if path == "/api/bootstrap":
            return self.send_json(get_bootstrap(self.session()))
        if path == "/api/config":
            return self.send_json({
                "nameAr": "خدماتي",
                "nameEn": "Khadamati App",
                "supportEmail": SUPPORT_EMAIL,
                "policyVersion": POLICY_VERSION,
                "currency": OMR,
                "publicUrl": PUBLIC_APP_URL,
            })
        if path == "/api/push/public-key":
            return self.send_json({"publicKey": os.environ.get("VAPID_PUBLIC_KEY", "")})
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
                return self.send_json({"provider": row_provider(row, private=True, sign_private=True)})
        if path == "/api/backup":
            session = self.require_admin("backup")
            if not session:
                return
            return self.send_json(get_bootstrap(session))
        if path.startswith("/api/reports/"):
            session = self.require_admin("view_reports")
            if not session:
                return
            return self.download_report(path)
        return super().do_GET()

    def download_report(self, path):
        with db() as con:
            rows = [
                ["المؤشر", "القيمة"],
                ["المستخدمون", con.execute("SELECT COUNT(*) n FROM app_users WHERE status='active'").fetchone()["n"]],
                ["المزودون", con.execute("SELECT COUNT(*) n FROM providers").fetchone()["n"]],
                ["الشركات", con.execute("SELECT COUNT(*) n FROM providers WHERE provider_type='company'").fetchone()["n"]],
                ["طلبات العملاء", con.execute("SELECT COUNT(*) n FROM customer_requests").fetchone()["n"]],
                ["طلبات غير متاحة", con.execute("SELECT COUNT(*) n FROM customer_requests WHERE status='unavailable'").fetchone()["n"]],
                ["اشتراكات نشطة", con.execute("SELECT COUNT(*) n FROM subscriptions WHERE status='active'").fetchone()["n"]],
                ["إيرادات مسجلة", con.execute("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind='revenue' AND status='paid'").fetchone()["n"]],
            ]
        stamp = datetime.now().strftime("%Y-%m-%d")
        if path.endswith(".csv"):
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerows(rows)
            raw = ("\ufeff" + output.getvalue()).encode("utf-8")
            return self.send_bytes(raw, "text/csv; charset=utf-8", f"khadamati-report-{stamp}.csv")
        if path.endswith(".docx"):
            paragraphs = "".join(
                f'<w:p><w:pPr><w:bidi/></w:pPr><w:r><w:rPr><w:rtl/></w:rPr><w:t>{html.escape(str(label))}: {html.escape(str(value))}</w:t></w:r></w:p>'
                for label, value in rows
            )
            document = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
            )
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                    "</Types>",
                )
                archive.writestr(
                    "_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                    "</Relationships>",
                )
                archive.writestr("word/document.xml", document)
            return self.send_bytes(
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                f"khadamati-report-{stamp}.docx",
            )
        table_rows = "".join(
            f"<tr><th>{html.escape(str(label))}</th><td>{html.escape(str(value))}</td></tr>"
            for label, value in rows
        )
        page = f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
        <title>تقرير خدماتي</title><style>
        body{{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;color:#102a43}}
        h1{{color:#087f8c}}table{{width:100%;border-collapse:collapse}}
        th,td{{padding:12px;border:1px solid #d9e2ec;text-align:right}}th{{background:#f0f7f8}}
        button{{padding:10px 18px;border:0;border-radius:8px;background:#087f8c;color:white}}
        @media print{{button{{display:none}}}}
        </style><h1>تقرير منصة خدماتي</h1><p>{stamp}</p>
        <table>{table_rows}</table><p><button onclick="print()">طباعة / حفظ PDF</button></p></html>"""
        return self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        try:
            return self._do_POST()
        except DomainError as err:
            return self.send_domain_error(err)
        except ValueError as err:
            code = str(err)
            if code not in {
                "state_must_be_object", "invalid_upload", "unsupported_upload_type",
                "upload_too_large", "upload_content_mismatch",
            }:
                code = "invalid_request_data"
            return self.send_json({"error": code}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception:
            return self.send_json({"error": "server_error"}, 500)

    def _do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/payments/webhook":
            try:
                raw = self.read_raw()
                signature = self.headers.get("X-Khadamati-Signature") or self.headers.get("X-Signature", "")
                with db() as con:
                    result = PaymentAdapter(con).verify_webhook(raw, signature)
                return self.send_json(result)
            except DomainError as err:
                return self.send_domain_error(err)
            except Exception:
                return self.send_json({"error": "invalid_webhook_payload"}, 400)
        try:
            data = self.read_json()
        except DomainError as err:
            return self.send_domain_error(err)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return self.send_json({"error": "invalid_request_data"}, 400)
        if path == "/api/auth/logout":
            return self.send_json({"ok": True, "revoked": revoke_session(self.headers)})
        if path == "/api/otp/request":
            purpose = str(data.get("purpose", "login") or "login")[:80]
            target_kind = str(data.get("targetKind", "user") or "user")[:40]
            if purpose not in {"login", "recovery", "delete_account", "change_pin"}:
                return self.send_json({"error": "invalid_otp_purpose"}, 400)
            if target_kind not in {"user", "provider", "company"}:
                return self.send_json({"error": "invalid_otp_target"}, 400)

            def deliver(phone, code):
                result = send_whatsapp(
                    phone,
                    f"رمز التحقق لخدماتي: {code}. لا تشارك الرمز مع أي شخص.",
                )
                return bool(result.get("ok"))

            try:
                with db() as con:
                    result = OTPService(
                        con, deliver=deliver if whatsapp_configured() else None
                    ).request(
                        data.get("phone", ""), purpose, target_kind
                    )
                return self.send_json({"ok": True, **result}, 201)
            except DomainError as err:
                return self.send_domain_error(err)
        if path == "/api/otp/verify":
            try:
                with db() as con:
                    result = OTPService(con).verify(
                        str(data.get("challengeId", "") or ""),
                        str(data.get("code", "") or ""),
                    )
                return self.send_json(result)
            except DomainError as err:
                return self.send_domain_error(err)
        if path == "/api/classic-state":
            session = self.require_admin("manage_settings")
            if not session:
                return
            try:
                saved_at = save_classic_state(data.get("state", data))
                return self.send_json({"ok": True, "savedAt": saved_at})
            except ValueError as err:
                return self.send_json({"error": str(err)}, 400)
        if path == "/api/admin/login":
            supplied_code = safe_text(data.get("code", ""), 128)
            lock_key = f"ip:{self.client_key()}"
            with db() as con:
                lock_state = login_failure_state(con, "admin", lock_key)
                if lock_state["locked"]:
                    return self.send_json(
                        {"error": "login_temporarily_locked", "retryAfter": lock_state["retryAfter"]},
                        429,
                    )
                row = next(
                    (
                        candidate for candidate in con.execute("SELECT * FROM admin_users WHERE active=1")
                        if verify_secret(supplied_code, candidate["code_hash"])
                    ),
                    None,
                )
                if row and not str(row["code_hash"] or "").startswith("pbkdf2_sha256$"):
                    con.execute(
                        "UPDATE admin_users SET code_hash=? WHERE id=?",
                        (hash_pin(supplied_code), row["id"]),
                    )
                if not row:
                    attempts = record_login_failure(con, "admin", lock_key)
                    return self.send_json({"error": "invalid_code", "attempts": attempts}, 403)
                clear_login_failures(con, "admin", lock_key)
            user = admin_public(row)
            token = issue_token({"kind": "admin", **user})
            return self.send_json({"token": token, "user": user})
        if path == "/api/provider/login":
            phone = normalize_phone(data.get("phone", ""))
            if len(phone) < 11:
                return self.send_json({"error": "valid_phone_required"}, 400)
            pending_request = None
            provider_row = None
            pin = safe_text(data.get("pin", ""), 8)
            if not pin:
                pin = safe_text(data.get("code", ""), 8)
            with db() as con:
                lock_state = login_failure_state(con, "provider", phone)
                if lock_state["locked"]:
                    return self.send_json(
                        {"error": "login_temporarily_locked", "retryAfter": lock_state["retryAfter"]},
                        429,
                    )
                row = con.execute(
                    """SELECT * FROM providers WHERE active=1 AND status!='deleted'
                    AND (phone=? OR phone=?)""",
                    (phone, phone.replace("968", "", 1)),
                ).fetchone()
                if not row:
                    row = next(
                        (
                            candidate for candidate in con.execute(
                                "SELECT * FROM providers WHERE active=1 AND status!='deleted'"
                            )
                            if phone_matches(candidate["phone"], phone)
                        ),
                        None,
                    )
                team_row = con.execute(
                    """SELECT tm.*,p.name provider_name FROM provider_team_members tm
                    JOIN providers p ON p.id=tm.provider_id
                    WHERE tm.active=1 AND p.active=1 AND p.status!='deleted'
                    AND (tm.phone=? OR tm.phone=?) LIMIT 1""",
                    (phone, phone.replace("968", "", 1)),
                ).fetchone()
                if not team_row:
                    team_row = next(
                        (
                            candidate
                            for candidate in con.execute(
                                """SELECT tm.*,p.name provider_name FROM provider_team_members tm
                                JOIN providers p ON p.id=tm.provider_id
                                WHERE tm.active=1 AND p.active=1 AND p.status!='deleted'"""
                            )
                            if phone_matches(candidate["phone"], phone)
                        ),
                        None,
                    )
                otp_ok = False
                if data.get("challengeId") and data.get("otpCode"):
                    try:
                        proof = OTPService(con).verify(
                            str(data.get("challengeId")), str(data.get("otpCode"))
                        )
                        otp_ok = (
                            proof["phone"] == phone
                            and proof["purpose"] == "login"
                            and proof["targetKind"] in {"provider", "company"}
                        )
                    except DomainError:
                        otp_ok = False
                owner_pin_ok = bool(row and row["pin_hash"] and verify_secret(pin, row["pin_hash"]))
                team_pin_ok = bool(
                    team_row and team_row["pin_hash"] and verify_secret(pin, team_row["pin_hash"])
                )
                if not (owner_pin_ok or team_pin_ok or otp_ok):
                    for request_row in con.execute(
                        "SELECT id,payload,created_at FROM provider_requests ORDER BY created_at DESC"
                    ):
                        request_payload = jload(request_row["payload"], {})
                        if (
                            phone_matches(request_payload.get("phone", ""), phone)
                            and request_payload.get("pinHash")
                            and verify_secret(pin, request_payload["pinHash"])
                        ):
                            pending_request = provider_request_view(
                                request_payload, request_row["created_at"]
                            )
                            break
                if not (owner_pin_ok or team_pin_ok or otp_ok or pending_request):
                    attempts = record_login_failure(con, "provider", phone, phone)
                    return self.send_json(
                        {"error": "invalid_provider_login", "attempts": attempts},
                        403,
                    )
                if pending_request:
                    clear_login_failures(con, "provider", phone)
                elif row and (owner_pin_ok or otp_ok):
                    provider_row = row
                    provider_id = row["id"]
                    clear_login_failures(con, "provider", phone)
                    provider_role = "provider_owner"
                    provider_permissions = list(PROVIDER_ROLE_PERMISSIONS["provider_owner"])
                    member_id = ""
                elif team_row and (team_pin_ok or otp_ok):
                    provider_id = team_row["provider_id"]
                    provider_row = con.execute(
                        "SELECT * FROM providers WHERE id=? AND active=1 AND status!='deleted'",
                        (provider_id,),
                    ).fetchone()
                    clear_login_failures(con, "provider", phone)
                    if not str(team_row["pin_hash"] or "").startswith("pbkdf2_sha256$"):
                        con.execute(
                            "UPDATE provider_team_members SET pin_hash=? WHERE id=?",
                            (hash_pin(pin), team_row["id"]),
                        )
                    provider_role = team_row["role"]
                    provider_permissions = jload(team_row["permissions"], [])
                    member_id = team_row["id"]
                if owner_pin_ok and row and not str(row["pin_hash"] or "").startswith("pbkdf2_sha256$"):
                    con.execute(
                        "UPDATE providers SET pin_hash=? WHERE id=?",
                        (hash_pin(pin), row["id"]),
                    )
            if pending_request:
                token = issue_token({
                    "kind": "provider_pending", "requestId": pending_request["id"],
                    "name": pending_request.get("name", ""), "phone": phone,
                })
                return self.send_json({
                    "token": token, "pending": True, "request": pending_request,
                })
            if not provider_row:
                return self.send_json({"error": "invalid_provider_login"}, 403)
            provider = row_provider(provider_row, private=True, sign_private=True)
            token = issue_token({
                "kind": "provider", "providerId": provider["id"], "name": provider["name"],
                "role": provider_role, "memberId": member_id,
                "providerPermissions": provider_permissions,
            })
            return self.send_json({"token": token, "provider": provider})
        if path == "/api/users/login":
            phone = normalize_phone(data.get("phone", ""))
            name = str(data.get("name", "") or "").strip()[:80]
            pin = str(data.get("pin", "") or "")
            if len(phone) < 11:
                return self.send_json({"error": "valid_phone_required"}, 400)
            with db() as con:
                row = con.execute("SELECT * FROM app_users WHERE phone=?", (phone,)).fetchone()
                if row and row["status"] != "active":
                    return self.send_json({"error": "account_inactive"}, 403)
                lock_key = row["id"] if row else phone
                lock_state = login_failure_state(con, "user", lock_key)
                if lock_state["locked"]:
                    return self.send_json(
                        {"error": "login_temporarily_locked", "retryAfter": lock_state["retryAfter"]},
                        429,
                    )
                otp_ok = False
                if data.get("challengeId") and data.get("otpCode"):
                    try:
                        proof = OTPService(con).verify(
                            str(data.get("challengeId")), str(data.get("otpCode"))
                        )
                        otp_ok = (
                            proof["phone"] == phone
                            and proof["purpose"] == "login"
                            and proof["targetKind"] == "user"
                        )
                    except DomainError:
                        otp_ok = False
                pin_ok = bool(row and row["pin_hash"] and verify_secret(pin, row["pin_hash"]))
                if row and row["pin_hash"] and not (pin_ok or otp_ok):
                    attempts = record_login_failure(con, "user", lock_key, phone)
                    con.execute("UPDATE app_users SET failed_attempts=? WHERE id=?", (attempts, row["id"]))
                    return self.send_json({"error": "invalid_user_pin", "attempts": attempts}, 403)
                if not row and len(pin) < 4 and not otp_ok:
                    return self.send_json({"error": "pin_required"}, 400)
                if row and not row["pin_hash"] and len(pin) < 4 and not otp_ok:
                    return self.send_json({"error": "otp_or_new_pin_required"}, 403)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                if row:
                    con.execute(
                        """UPDATE app_users SET name=COALESCE(NULLIF(?,''),name),gov=COALESCE(NULLIF(?,''),gov),
                        wilayah=COALESCE(NULLIF(?,''),wilayah),latitude=COALESCE(?,latitude),
                        longitude=COALESCE(?,longitude),last_login=CURRENT_TIMESTAMP,
                        login_count=login_count+1,failed_attempts=0 WHERE id=?""",
                        (
                            name, data.get("gov", ""), data.get("wilayah", ""),
                            location.get("lat"), location.get("lng"), row["id"],
                        ),
                    )
                    user_id = row["id"]
                    clear_login_failures(con, "user", lock_key)
                    if not row["pin_hash"] and len(pin) >= 4:
                        con.execute(
                            "UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), user_id)
                        )
                    if pin_ok and not str(row["pin_hash"] or "").startswith("pbkdf2_sha256$"):
                        con.execute(
                            "UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), user_id)
                        )
                else:
                    user_id = slug("usr")
                    con.execute(
                        """INSERT INTO app_users(
                        id,phone,name,pin_hash,gov,wilayah,latitude,longitude)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            user_id, phone, name, hash_pin(pin) if len(pin) >= 4 else "",
                            data.get("gov", ""), data.get("wilayah", ""),
                            location.get("lat"), location.get("lng"),
                        ),
                    )
                    create_notification(
                        con, "admin", "", "مستخدم جديد",
                        f"تم تسجيل {name or phone}", type_="user", related_id=user_id,
                        action_text="فتح المستخدم", action_route=f"admin:user:{user_id}",
                    )
                user_row = con.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
            user = row_app_user(user_row, private=True, sign_private=True)
            token = issue_token({"kind": "user", "userId": user_id, "name": user["name"], "phone": phone})
            return self.send_json({"token": token, "user": user})
        if path == "/api/provider-requests":
            pin = str(data.get("pin", "")).strip()[:128]
            req_id = slug("req")
            try:
                location = normalized_location(data.get("location"))
                base_price = finite_number(
                    data.get("priceFrom", 0), minimum=0, maximum=1_000_000
                )
            except DomainError as err:
                return self.send_domain_error(err)
            item = {
                "id": req_id,
                "name": safe_text(data.get("name"), 120),
                "phone": normalize_phone(data.get("phone", "")),
                "providerType": data.get("providerType", "individual") if data.get("providerType") in ("individual", "company") else "individual",
                "companyName": safe_text(data.get("companyName"), 160),
                "commercialNo": safe_text(data.get("commercialNo"), 120),
                "companySize": safe_text(data.get("companySize"), 80),
                "businessRole": safe_text(data.get("businessRole"), 80),
                "gov": safe_text(data.get("gov", "مسقط"), 80),
                "wilayah": safe_text(data.get("wilayah"), 80),
                "location": location,
                "service": safe_text(data.get("service"), 180),
                "services": [],
                "priceFrom": base_price,
                "note": safe_text(data.get("note"), 600),
                "bio": safe_text(data.get("bio") or data.get("note"), 600),
                "hours": safe_text(data.get("hours"), 240),
                "imagePath": "",
                "workImages": [],
                "documents": [],
                "pinHash": hash_pin(pin) if len(pin) >= 4 else "",
            }
            item["note"] = item["bio"]
            raw_services = data.get("services") if isinstance(data.get("services"), list) else []
            if not item["services"] and "|" in item["service"]:
                cat_id, service_id = item["service"].split("|", 1)
                raw_services = [{
                    "catId": cat_id, "serviceId": service_id,
                    "priceFrom": item["priceFrom"], "active": True,
                    "areas": [item["wilayah"]],
                }]
            try:
                with db() as con:
                    foundation = PlanCatalog.get(con, "foundation_12m", False) or {}
                    is_company = item["providerType"] == "company"
                    item["services"] = normalized_provider_services(
                        con, raw_services,
                        limit=max(1, int(foundation.get("max_services") or 1)) if is_company else 1,
                        category_limit=max(1, int(foundation.get("max_categories") or 1)) if is_company else 1,
                        fallback_price=item["priceFrom"], default_areas=[item["wilayah"]],
                    )
            except DomainError as err:
                return self.send_domain_error(err)
            if item["services"]:
                first = item["services"][0]
                item["service"] = f"{first['catId']}|{first['serviceId']}"
            if not item["name"] or len(item["phone"]) < 11 or not item["pinHash"]:
                return self.send_json({"error": "name_phone_pin_required"}, 400)
            if len(pin) < 4:
                return self.send_json({"error": "pin_too_short"}, 400)
            if item["providerType"] == "company" and not item["companyName"]:
                return self.send_json({"error": "company_name_required"}, 400)
            if not item["commercialNo"]:
                return self.send_json({"error": "commercial_number_required"}, 400)
            note_words = len(str(item["note"]).split())
            if note_words < 3 or note_words > 20:
                return self.send_json({"error": "description_word_limit"}, 400)
            if not item["services"]:
                return self.send_json({"error": "service_required"}, 400)
            if not item["hours"]:
                return self.send_json({"error": "availability_required"}, 400)
            if not data.get("documentsData"):
                return self.send_json({"error": "documents_required"}, 400)
            try:
                if data.get("imageData"):
                    item["imagePath"] = save_data_url(req_id, data.get("imageData"))
                if data.get("workImagesData"):
                    item["workImages"] = save_many_images(req_id, data.get("workImagesData"), "work", 15 if item["providerType"] == "company" else 5)
                if data.get("documentsData"):
                    item["documents"] = save_many_documents(req_id, data.get("documentsData"), "doc", 3)
            except ValueError as err:
                return self.send_json({"error": str(err)}, 400)
            with db() as con:
                con.execute("INSERT INTO provider_requests(id,payload) VALUES(?,?)", (item["id"], jdump(item)))
                settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
            send_whatsapp(settings.get("adminWhatsapp"), f"طلب مزود جديد في خدماتي: {item['name']} - {item['phone']} - {len(item['services'])} خدمات")
            safe_item = provider_request_view(item)
            return self.send_json({"ok": True, "request": safe_item}, 201)
        if path == "/api/reviews":
            return self.save_review(data)
        if path == "/api/complaints":
            return self.save_complaint(data)
        if path == "/api/leads":
            return self.save_lead(data)
        if path.startswith("/api/user/"):
            return self.user_post(path, data)
        if path == "/api/requests/action":
            return self.request_action(data)
        if path == "/api/request-suggestions":
            return self.request_suggestion(data)
        if path == "/api/request/collaboration":
            return self.request_collaboration(data)
        if path == "/api/notifications/action":
            return self.notification_action(data)
        if path == "/api/recovery/request":
            return self.recovery_request(data)
        if path == "/api/recovery/complete":
            return self.recovery_complete(data)
        if path == "/api/account/delete":
            return self.delete_account(data)
        if path == "/api/push/subscribe":
            return self.push_subscribe(data)
        if path == "/api/policy/accept":
            return self.policy_accept(data)
        if path.startswith("/api/provider/"):
            return self.provider_post(path, data)
        if path.startswith("/api/admin/"):
            return self.admin_post(path, data)
        self.send_json({"error": "not_found"}, 404)

    def save_review(self, data):
        session = self.require_user()
        if not session:
            return
        provider_id = data.get("providerId")
        request_id = str(data.get("requestId", "") or "")
        rating = int(data.get("rating", 0) or 0)
        if not provider_id or not request_id or rating < 1 or rating > 5:
            return self.send_json({"error": "invalid_review"}, 400)
        with db() as con:
            user = con.execute("SELECT * FROM app_users WHERE id=?", (session["userId"],)).fetchone()
            request_row = con.execute(
                """SELECT id FROM customer_requests WHERE id=? AND user_id=?
                AND accepted_provider_id=? AND status IN ('closed','completed','archived')""",
                (request_id, session["userId"], provider_id),
            ).fetchone()
            if not user or not request_row:
                return self.send_json({"error": "completed_request_required"}, 403)
            if con.execute("SELECT id FROM reviews WHERE request_id=? AND user_id=?", (request_id, session["userId"])).fetchone():
                return self.send_json({"error": "request_already_reviewed"}, 409)
            if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                return self.send_json({"error": "provider_not_found"}, 404)
            item = {
                "id": slug("rev"),
                "provider_id": provider_id,
                "request_id": request_id,
                "user_id": session["userId"],
                "rating": rating,
                "customer_name": user["name"],
                "phone": user["phone"],
                "comment": str(data.get("comment", "") or "").strip()[:900],
            }
            con.execute(
                """INSERT INTO reviews(
                id,provider_id,rating,customer_name,phone,comment,approved,request_id,user_id)
                VALUES(?,?,?,?,?,?,1,?,?)""",
                (
                    item["id"], item["provider_id"], item["rating"], item["customer_name"],
                    item["phone"], item["comment"], item["request_id"], item["user_id"],
                ),
            )
            recompute_provider_quality(con, provider_id)
            log_audit(con, session, "review.created", provider_id, request_id)
        return self.send_json({"ok": True, "review": item}, 201)

    def save_complaint(self, data):
        session = self.require_user()
        if not session:
            return
        provider_id = data.get("providerId")
        request_id = str(data.get("requestId", "") or "")
        with db() as con:
            user = con.execute("SELECT * FROM app_users WHERE id=?", (session["userId"],)).fetchone()
            if not user:
                return self.send_json({"error": "user_not_found"}, 404)
            if request_id and not con.execute(
                "SELECT id FROM customer_requests WHERE id=? AND user_id=?", (request_id, session["userId"])
            ).fetchone():
                return self.send_json({"error": "request_not_found"}, 404)
            item = {
                "id": slug("cmp"),
                "provider_id": provider_id,
                "request_id": request_id,
                "user_id": session["userId"],
                "customer_name": user["name"],
                "phone": user["phone"],
                "reason": str(data.get("reason", "quality") or "quality").strip()[:80],
                "detail": str(data.get("detail", "") or "").strip()[:1400],
                "priority": data.get("priority", "normal") if data.get("priority") in ("low", "normal", "high") else "normal",
            }
            if not item["detail"]:
                return self.send_json({"error": "complaint_required_fields"}, 400)
            con.execute(
                """INSERT INTO complaints(
                id,provider_id,customer_name,phone,reason,detail,status,priority,resolution,request_id,user_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item["id"], item["provider_id"], item["customer_name"], item["phone"],
                    item["reason"], item["detail"], "open", item["priority"], "",
                    item["request_id"], item["user_id"],
                ),
            )
            if provider_id:
                recompute_provider_quality(con, provider_id)
            log_audit(con, session, "complaint.created", provider_id or "", request_id or item["reason"])
            settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
        send_whatsapp(settings.get("adminWhatsapp"), f"شكوى جديدة في خدماتي: {item['customer_name']} - {item['phone']} - {item['reason']}")
        return self.send_json({"ok": True, "complaint": item}, 201)

    def save_lead(self, data):
        kind = data.get("kind", "whatsapp")
        if kind not in ("request", "views", "whatsapp", "calls", "booking", "quote"):
            kind = "request"
        session = self.session()
        session_kind = (session or {}).get("kind", "")

        # This endpoint remains for backwards compatibility. Identity and phone data
        # are always sourced from the authenticated session, never from the browser.
        if kind == "quote" and session_kind != "provider":
            return self.send_json({"error": "provider_auth_required"}, 401)
        if kind in ("request", "views", "whatsapp", "calls") and session_kind != "user":
            return self.send_json({"error": "user_auth_required"}, 401)
        if kind == "booking" and session_kind != "admin":
            return self.send_json({"error": "permission_denied"}, 403)

        supplied_id = str(data.get("id") or "").strip()[:80]
        lead_id = supplied_id if session_kind == "admin" and supplied_id else slug("lead")
        provider_id = str(data.get("providerId") or "").strip()[:80]
        if kind == "quote":
            provider_id = str(session.get("providerId") or "").strip()[:80]
        item = {
            "id": lead_id,
            "provider_id": provider_id,
            "kind": kind,
            "customer_name": "",
            "phone": "",
            "note": (data.get("note", "") or "").strip()[:1200],
            "service_value": (data.get("serviceValue", "") or "").strip()[:120],
            "service_name": (data.get("serviceName", "") or "").strip()[:120],
            "gov": (data.get("gov", "") or "").strip()[:80],
            "status": (data.get("status", "open") or "open").strip()[:40],
        }
        with db() as con:
            if item["provider_id"]:
                provider_row = con.execute(
                    "SELECT id FROM providers WHERE id=? AND active=1", (item["provider_id"],)
                ).fetchone()
                if not provider_row:
                    return self.send_json({"error": "provider_not_found"}, 404)
            if session_kind == "user":
                user_row = con.execute(
                    "SELECT name,phone FROM app_users WHERE id=? AND status='active'",
                    (session.get("userId"),),
                ).fetchone()
                if not user_row:
                    return self.send_json({"error": "user_not_found"}, 404)
                item["customer_name"] = user_row["name"] or ""
                item["phone"] = user_row["phone"] or ""
            elif session_kind == "admin":
                item["customer_name"] = "إدارة خدماتي"
            elif kind == "quote":
                # A quote never needs a copied customer phone number.
                item["customer_name"] = str(data.get("customerName") or "").strip()[:80]

            exists = con.execute("SELECT id FROM leads WHERE id=?", (item["id"],)).fetchone()
            if exists:
                con.execute(
                    """UPDATE leads
                    SET provider_id=?, kind=?, customer_name=?, phone=?, note=?, service_value=?, service_name=?, gov=?, status=?
                    WHERE id=?""",
                    (
                        item["provider_id"], item["kind"], item["customer_name"], item["phone"], item["note"],
                        item["service_value"], item["service_name"], item["gov"], item["status"], item["id"],
                    ),
                )
            else:
                con.execute(
                    """INSERT INTO leads(id,provider_id,kind,customer_name,phone,note,service_value,service_name,gov,status,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (
                        item["id"], item["provider_id"], item["kind"], item["customer_name"], item["phone"], item["note"],
                        item["service_value"], item["service_name"], item["gov"], item["status"],
                    ),
                )
            provider = None
            if item["kind"] in ("views", "whatsapp", "calls") and item["provider_id"]:
                r = con.execute("SELECT * FROM providers WHERE id=?", (item["provider_id"],)).fetchone()
                if r:
                    provider = row_provider(r, private=True)
                    stats = provider["stats"]
                    stats[item["kind"]] = int(stats.get(item["kind"], 0)) + 1
                    con.execute("UPDATE providers SET stats=? WHERE id=?", (jdump(stats), item["provider_id"]))
            if (
                item["provider_id"]
                and session
                and session.get("kind") == "admin"
                and item["kind"] in ("booking", "quote", "request")
            ):
                create_notification(
                    con, "provider", item["provider_id"], "ملاحظة من الإدارة",
                    item["note"], type_="admin", related_id=item["id"], priority="high",
                    action_text="فتح الرسالة", action_route="provider:support",
                )
        if data.get("notifyProvider") and provider and session_kind == "admin":
            send_whatsapp(provider["phone"], f"تنبيه من خدماتي: لديك تواصل جديد. {item['note']}".strip())
        safe_item = dict(item)
        if session_kind != "admin":
            safe_item.pop("phone", None)
        return self.send_json({"ok": True, "lead": safe_item}, 200 if exists else 201)

    def user_post(self, path, data):
        session = self.require_user()
        if not session:
            return
        user_id = session["userId"]
        with db() as con:
            user_row = con.execute("SELECT * FROM app_users WHERE id=? AND status='active'", (user_id,)).fetchone()
            if not user_row:
                return self.send_json({"error": "user_not_found"}, 404)
            if path == "/api/user/profile":
                avatar = user_row["avatar"] or ""
                if data.get("avatarData"):
                    avatar = save_upload_data(user_id, data["avatarData"], "avatar", IMAGE_MIMES, 2_500_000)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                con.execute(
                    """UPDATE app_users SET name=?,gov=?,wilayah=?,avatar=?,
                    latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude)
                    WHERE id=?""",
                    (
                        str(data.get("name", user_row["name"]) or "").strip()[:80],
                        str(data.get("gov", user_row["gov"]) or "").strip()[:80],
                        str(data.get("wilayah", user_row["wilayah"]) or "").strip()[:80],
                        avatar, location.get("lat"), location.get("lng"), user_id,
                    ),
                )
                updated = con.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
                return self.send_json({"ok": True, "user": row_app_user(updated, private=True, sign_private=True)})
            if path == "/api/user/pin":
                pin = str(data.get("pin", ""))
                if not re.fullmatch(r"\d{4,8}", pin):
                    return self.send_json({"error": "pin_too_short"}, 400)
                if user_row["pin_hash"] and not verify_secret(data.get("currentPin", ""), user_row["pin_hash"]):
                    return self.send_json({"error": "current_pin_incorrect"}, 403)
                con.execute("UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), user_id))
                authorization = str(self.headers.get("Authorization", "") or "")
                current_hash = hash_secret(authorization[7:].strip()) if authorization.startswith("Bearer ") else ""
                revoke_account_sessions(con, "user", user_id, current_hash)
                return self.send_json({"ok": True})
            if path == "/api/user/requests":
                request_id = str(data.get("id", "") or "")
                action = data.get("action", "save")
                if request_id:
                    current = con.execute(
                        "SELECT * FROM customer_requests WHERE id=? AND user_id=?",
                        (request_id, user_id),
                    ).fetchone()
                    if not current:
                        return self.send_json({"error": "request_not_found"}, 404)
                    if action in ("cancel", "delete", "pause", "complete", "archive"):
                        if action in ("complete", "archive") and not current["accepted_provider_id"]:
                            return self.send_json({"error": "accepted_provider_required"}, 409)
                        next_status = {
                            "cancel": "cancelled", "delete": "deleted", "pause": "paused",
                            "complete": "closed", "archive": "archived",
                        }[action]
                        con.execute(
                            """UPDATE customer_requests SET status=?,offers_open=0,
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                            (next_status, request_id),
                        )
                        create_notification(
                            con, "admin", "", "تم تحديث طلب",
                            f"الطلب {request_id}", type_="request", related_id=request_id,
                            action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                        )
                        return self.send_json({"ok": True, "status": next_status})
                    if current["accepted_provider_id"]:
                        return self.send_json({"error": "accepted_request_cannot_be_edited"}, 409)
                else:
                    request_id = slug("ord")
                service_value = str(data.get("serviceValue", "") or "").strip()[:120]
                service_name = str(data.get("serviceName", "") or "").strip()[:120]
                if not service_value or "|" not in service_value:
                    return self.send_json({"error": "service_required"}, 400)
                cat_id, service_id = service_value.split("|", 1)
                service_row = con.execute(
                    """SELECT s.ar,s.en FROM services s JOIN categories c ON c.id=s.category_id
                    WHERE s.id=? AND s.category_id=? AND s.active=1 AND c.active=1""",
                    (safe_text(service_id, 80), safe_text(cat_id, 80)),
                ).fetchone()
                if not service_row:
                    return self.send_json({"error": "service_not_found"}, 400)
                service_value = f"{safe_text(cat_id, 80)}|{safe_text(service_id, 80)}"
                service_name = service_name or service_row["ar"]
                images = jload(current["images"], []) if request_id and 'current' in locals() else []
                if data.get("imagesData"):
                    images = save_many_images(request_id, data["imagesData"], "problem", 5)
                request_item = {
                    "id": request_id,
                    "userId": user_id,
                    "customerName": str(data.get("customerName", user_row["name"]) or "")[:80],
                    "phone": user_row["phone"],
                    "serviceValue": service_value,
                    "serviceName": service_name,
                    "gov": str(data.get("gov", user_row["gov"]) or "")[:80],
                    "wilayah": str(data.get("wilayah", user_row["wilayah"]) or "")[:80],
                }
                try:
                    location = normalized_location(data.get("location"))
                    budget_min = finite_number(
                        data.get("budgetMin", 0), minimum=0, maximum=1_000_000
                    )
                    budget_max = finite_number(
                        data.get("budgetMax", 0), minimum=0, maximum=1_000_000
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                if budget_max and budget_min > budget_max:
                    return self.send_json({"error": "invalid_budget_range"}, 400)
                urgency = data.get("urgency", "normal")
                if urgency not in {"normal", "urgent"}:
                    urgency = "normal"
                schedule_type = data.get("scheduleType", "flexible")
                if schedule_type not in {"flexible", "scheduled"}:
                    schedule_type = "flexible"
                if data.get("id"):
                    con.execute("DELETE FROM request_dispatches WHERE request_id=?", (request_id,))
                con.execute(
                    """INSERT INTO customer_requests(
                    id,user_id,customer_name,phone,service_value,service_name,gov,wilayah,
                    latitude,longitude,urgency,schedule_type,requested_at,budget_min,budget_max,
                    location_text,note,images,status,accepted_provider_id,matching_provider_ids,
                    declined_provider_ids,offers_open,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                    customer_name=excluded.customer_name,service_value=excluded.service_value,
                    service_name=excluded.service_name,gov=excluded.gov,wilayah=excluded.wilayah,
                    latitude=excluded.latitude,longitude=excluded.longitude,urgency=excluded.urgency,
                    schedule_type=excluded.schedule_type,requested_at=excluded.requested_at,
                    budget_min=excluded.budget_min,budget_max=excluded.budget_max,
                    location_text=excluded.location_text,note=excluded.note,images=excluded.images,
                    status=excluded.status,matching_provider_ids=excluded.matching_provider_ids,
                    offers_open=1,updated_at=CURRENT_TIMESTAMP""",
                    (
                        request_id, user_id, request_item["customerName"], user_row["phone"],
                        service_value, service_name, request_item["gov"], request_item["wilayah"],
                        location.get("lat"), location.get("lng"), urgency,
                        schedule_type, safe_text(data.get("requestedAt"), 80),
                        budget_min, budget_max,
                        str(data.get("locationText", "") or "")[:240],
                        str(data.get("note", "") or "")[:1200], jdump(images), "matching", "",
                        "[]", "[]", 1,
                    ),
                )
                marketplace = RequestMarketplace(con)
                ranked = marketplace.schedule(request_id)
                released = marketplace.release_due(request_id)
                create_marketplace_notifications(con, released)
                status = "matching" if ranked else "unavailable"
                create_notification(
                    con, "admin", "", "طلب خدمة جديد" if ranked else "خدمة غير متاحة",
                    f"{service_name or service_value} - {request_item['wilayah'] or request_item['gov']}",
                    type_="request", related_id=request_id,
                    priority="normal" if ranked else "high",
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )
                saved = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
                return self.send_json(
                    {
                        "ok": True,
                        "request": row_customer_request(saved, sign_private=True),
                        "matchedProviders": len(ranked),
                        "notifiedProviders": len(released),
                    },
                    200 if data.get("id") else 201,
                )
        return self.send_json({"error": "not_found"}, 404)

    def request_suggestion(self, data):
        session = self.require_user()
        if not session:
            return
        user_id = session["userId"]
        action = str(data.get("action") or "candidates")
        suggestion_id = str(data.get("suggestionId") or "")
        request_id = str(data.get("requestId") or "")
        with db() as con:
            suggestion_row = None
            if suggestion_id:
                suggestion_row = con.execute(
                    """SELECT s.*,r.user_id request_owner_id FROM request_provider_suggestions s
                    JOIN customer_requests r ON r.id=s.request_id WHERE s.id=?""",
                    (suggestion_id,),
                ).fetchone()
                if not suggestion_row:
                    return self.send_json({"error": "suggestion_not_found"}, 404)
                request_id = suggestion_row["request_id"]
            request_row = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not request_row:
                return self.send_json({"error": "request_not_found"}, 404)
            if request_row["status"] not in ACTIVE_REQUEST_STATES or not bool(request_row["offers_open"]):
                return self.send_json({"error": "request_not_open"}, 409)

            if action == "candidates":
                if request_row["user_id"] == user_id:
                    return self.send_json({"error": "request_owner_cannot_suggest"}, 403)
                existing = {
                    row["provider_id"]
                    for row in con.execute(
                        """SELECT provider_id FROM request_provider_suggestions
                        WHERE request_id=? AND status IN ('active','selected')""",
                        (request_id,),
                    )
                }
                providers = [
                    provider for provider in ranked_suggestion_candidates(con, request_row)
                    if provider["id"] not in existing
                ]
                return self.send_json({"ok": True, "providers": providers})

            if action == "create":
                if request_row["user_id"] == user_id:
                    return self.send_json({"error": "request_owner_cannot_suggest"}, 403)
                provider_id = str(data.get("providerId") or "")
                preset_key = str(data.get("presetKey") or "")
                comment = re.sub(r"\s+", " ", str(data.get("comment") or "")).strip()[:160]
                if preset_key not in SUGGESTION_PRESET_KEYS:
                    return self.send_json({"error": "suggestion_comment_required"}, 400)
                candidates = {provider["id"]: provider for provider in ranked_suggestion_candidates(con, request_row, limit=20)}
                provider = candidates.get(provider_id)
                if not provider:
                    return self.send_json({"error": "provider_not_eligible_for_request"}, 409)
                duplicate = con.execute(
                    "SELECT id FROM request_provider_suggestions WHERE request_id=? AND provider_id=?",
                    (request_id, provider_id),
                ).fetchone()
                if duplicate:
                    return self.send_json({"error": "suggestion_already_exists"}, 409)
                per_request = con.execute(
                    """SELECT COUNT(*) n FROM request_provider_suggestions
                    WHERE request_id=? AND suggested_by_user_id=? AND status IN ('active','selected')""",
                    (request_id, user_id),
                ).fetchone()["n"]
                daily = con.execute(
                    """SELECT COUNT(*) n FROM request_provider_suggestions
                    WHERE suggested_by_user_id=? AND created_at>=datetime('now','-1 day')""",
                    (user_id,),
                ).fetchone()["n"]
                if int(per_request) >= 3 or int(daily) >= 10:
                    return self.send_json({"error": "suggestion_rate_limited"}, 429)
                suggestion_id = slug("suggestion")
                con.execute(
                    """INSERT INTO request_provider_suggestions(
                    id,request_id,provider_id,suggested_by_user_id,preset_key,comment)
                    VALUES(?,?,?,?,?,?)""",
                    (suggestion_id, request_id, provider_id, user_id, preset_key, comment),
                )
                create_notification(
                    con, "user", request_row["user_id"], "ترشيح مزود جديد",
                    f"تم ترشيح {provider['name']} لطلب {request_row['service_name'] or request_row['service_value']}",
                    type_="provider_suggestion", related_id=suggestion_id, priority="normal",
                    action_text="عرض الترشيح",
                    action_route=f"user:suggestion:{suggestion_id}:provider:{provider_id}:request:{request_id}",
                )
                item = request_suggestion_by_id(con, suggestion_id)
                return self.send_json({"ok": True, "suggestion": item}, 201)

            if action == "delete":
                if suggestion_row["status"] not in ("active", "selected"):
                    return self.send_json({"error": "suggestion_not_active"}, 409)
                if user_id not in {suggestion_row["suggested_by_user_id"], suggestion_row["request_owner_id"]}:
                    return self.send_json({"error": "suggestion_action_denied"}, 403)
                con.execute(
                    """UPDATE request_provider_suggestions SET status='deleted',deleted_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (suggestion_id,),
                )
                con.execute(
                    "DELETE FROM app_notifications WHERE type='provider_suggestion' AND related_id=?",
                    (suggestion_id,),
                )
                return self.send_json({"ok": True})

            if action == "report":
                if suggestion_row["status"] not in ("active", "selected"):
                    return self.send_json({"error": "suggestion_not_active"}, 409)
                if user_id not in {suggestion_row["suggested_by_user_id"], suggestion_row["request_owner_id"]}:
                    return self.send_json({"error": "suggestion_action_denied"}, 403)
                reason = re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:240]
                if not reason:
                    return self.send_json({"error": "report_reason_required"}, 400)
                con.execute(
                    """UPDATE request_provider_suggestions SET status='reported',report_reason=?,
                    reported_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (reason, suggestion_id),
                )
                con.execute(
                    "DELETE FROM app_notifications WHERE type='provider_suggestion' AND related_id=?",
                    (suggestion_id,),
                )
                create_notification(
                    con, "admin", "", "بلاغ عن ترشيح مزود", reason,
                    type_="provider_suggestion", related_id=suggestion_id, priority="high",
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )
                return self.send_json({"ok": True})

            if action == "select":
                if suggestion_row["status"] != "active":
                    return self.send_json({"error": "suggestion_not_active"}, 409)
                if user_id != suggestion_row["request_owner_id"]:
                    return self.send_json({"error": "suggestion_action_denied"}, 403)
                provider_id = suggestion_row["provider_id"]
                candidates = {provider["id"]: provider for provider in ranked_suggestion_candidates(con, request_row, limit=20)}
                provider = candidates.get(provider_id)
                if not provider:
                    return self.send_json({"error": "provider_no_longer_available"}, 409)
                matching = jload(request_row["matching_provider_ids"], [])
                matching = list(dict.fromkeys([*matching, provider_id]))
                con.execute(
                    """UPDATE request_provider_suggestions SET status='selected',selected_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (suggestion_id,),
                )
                con.execute(
                    """UPDATE customer_requests SET matching_provider_ids=?,status='matching',waitlisted=0,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (jdump(matching), request_id),
                )
                con.execute(
                    """INSERT INTO request_dispatches(
                    id,request_id,provider_id,rank,score,score_breakdown,wave,release_at,status,notified_at)
                    VALUES(?,?,?,?,?,'{}',1,CURRENT_TIMESTAMP,'notified',CURRENT_TIMESTAMP)
                    ON CONFLICT(request_id,provider_id) DO UPDATE SET status='notified',
                    release_at=CURRENT_TIMESTAMP,notified_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP""",
                    (slug("dispatch"), request_id, provider_id, 1, float(provider.get("suggestionScore") or 0)),
                )
                create_notification(
                    con, "provider", provider_id, "طلب خدمة اختارك صاحبه",
                    f"أرسل لك صاحب الطلب خدمة {request_row['service_name'] or request_row['service_value']}",
                    type_="request", related_id=request_id, priority="high",
                    action_text="فتح الطلب", action_route=f"provider:request:{request_id}",
                )
                updated = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
                return self.send_json({
                    "ok": True,
                    "suggestion": request_suggestion_by_id(con, suggestion_id),
                    "request": row_customer_request(updated, sign_private=True),
                })

        return self.send_json({"error": "invalid_suggestion_action"}, 400)

    def request_action(self, data):
        session = self.require_provider("requests")
        if not session:
            return
        request_id = str(data.get("id", ""))
        action = data.get("action")
        if action not in ("accept", "decline"):
            return self.send_json({"error": "invalid_request_action"}, 400)
        provider_id = session["providerId"]
        with db() as con:
            row = con.execute("SELECT * FROM customer_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                return self.send_json({"error": "request_not_found"}, 404)
            item = row_customer_request(row)
            if provider_id not in item["matchingProviderIds"]:
                return self.send_json({"error": "request_not_assigned_to_provider"}, 403)
            if action == "accept":
                result = con.execute(
                    """UPDATE customer_requests SET accepted_provider_id=?,status='accepted',
                    offers_open=0,contact_consent=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND offers_open=1 AND COALESCE(accepted_provider_id,'')=''""",
                    (provider_id, jdump({"chat": False, "whatsapp": False, "call": False}), request_id),
                )
                if result.rowcount != 1:
                    return self.send_json({"error": "request_already_accepted"}, 409)
                consent_service = ContactConsentService(con)
                for channel in ("chat", "whatsapp", "call"):
                    consent_service.set_channel(request_id, item["userId"], provider_id, channel, False)
                con.execute(
                    """UPDATE request_dispatches SET status='accepted',accepted_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND provider_id=?""",
                    (request_id, provider_id),
                )
                create_notification(
                    con, "user", item["userId"], "تم قبول طلبك",
                    f"وافق مزود على طلب {item['serviceName'] or item['serviceValue']}",
                    type_="request", related_id=request_id, priority="high",
                    action_text="عرض الطلب", action_route=f"user:request:{request_id}",
                )
                create_notification(
                    con, "admin", "", "تم قبول طلب",
                    f"{request_id} بواسطة {session.get('name', provider_id)}",
                    type_="request", related_id=request_id,
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )
            else:
                declined = list(item["declinedProviderIds"])
                if provider_id not in declined:
                    declined.append(provider_id)
                remaining = [pid for pid in item["matchingProviderIds"] if pid not in declined]
                status = "matching" if remaining else "unavailable"
                con.execute(
                    """UPDATE customer_requests SET declined_provider_ids=?,status=?,
                    offers_open=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (jdump(declined), status, int(bool(remaining)), request_id),
                )
                if not remaining:
                    create_notification(
                        con, "user", item["userId"], "لم يتوفر مزود بعد",
                        "سنحتفظ بطلبك لإيجاد مزود مناسب.", type_="request",
                        related_id=request_id, action_text="عرض الطلب",
                        action_route=f"user:request:{request_id}",
                    )
            return self.send_json({"ok": True, "status": action})

    def request_collaboration(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        request_id = str(data.get("id", "") or "")
        action = str(data.get("action", "") or "")
        if not request_id or action not in (
            "offer", "choose_offer", "contact_consent", "message", "arrival", "waitlist"
        ):
            return self.send_json({"error": "invalid_request_action"}, 400)
        with db() as con:
            row = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                return self.send_json({"error": "request_not_found"}, 404)
            item = row_customer_request(row)
            provider_id = session.get("providerId", "")
            user_id = session.get("userId", "")
            is_user = bool(user_id and user_id == item["userId"])
            is_provider = bool(
                provider_id
                and (
                    provider_id in item["matchingProviderIds"]
                    or provider_id == item["acceptedProviderId"]
                )
            )

            if action == "offer":
                if not is_provider or not item["offersOpen"] or item["acceptedProviderId"]:
                    return self.send_json({"error": "offer_not_allowed"}, 403)
                try:
                    price = finite_number(
                        data.get("price", 0), minimum=0, maximum=1_000_000
                    )
                except DomainError:
                    return self.send_json({"error": "invalid_offer_price"}, 400)
                duration = str(data.get("duration", "") or "").strip()[:100]
                if not duration:
                    return self.send_json({"error": "offer_duration_required"}, 400)
                offers = list(item.get("offers") or [])
                existing = next(
                    (offer for offer in offers if offer.get("providerId") == provider_id),
                    None,
                )
                offer = {
                    "id": existing.get("id") if existing else slug("offer"),
                    "providerId": provider_id,
                    "price": price,
                    "duration": duration,
                    "note": str(data.get("note", "") or "").strip()[:500],
                    "status": "pending",
                    "createdAt": existing.get("createdAt") if existing else datetime.now(UTC).isoformat(),
                    "updatedAt": datetime.now(UTC).isoformat(),
                }
                if existing:
                    offers = [offer if row_offer is existing else row_offer for row_offer in offers]
                else:
                    offers.append(offer)
                con.execute(
                    "UPDATE customer_requests SET offers=?,status='viewed',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(offers[-12:]), request_id),
                )
                create_notification(
                    con, "user", item["userId"], "وصل عرض جديد لطلبك",
                    f"{session.get('name', 'مزود')} أرسل سعراً ومدة لخدمة {item['serviceName'] or item['serviceValue']}.",
                    type_="request", related_id=request_id, priority="high",
                    action_text="مقارنة العروض", action_route=f"user:request:{request_id}",
                )

            elif action == "choose_offer":
                if not is_user or item["acceptedProviderId"]:
                    return self.send_json({"error": "offer_selection_not_allowed"}, 403)
                offer_id = str(data.get("offerId", "") or "")
                offers = list(item.get("offers") or [])
                selected = next((offer for offer in offers if offer.get("id") == offer_id), None)
                if not selected:
                    return self.send_json({"error": "offer_not_found"}, 404)
                selected_provider = selected.get("providerId", "")
                chat_granted = bool(data.get("chat", False))
                for offer in offers:
                    offer["status"] = "accepted" if offer.get("id") == offer_id else "declined"
                con.execute(
                    """UPDATE customer_requests SET offers=?,accepted_provider_id=?,
                    status='accepted',offers_open=0,waitlisted=0,contact_consent=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (
                        jdump(offers), selected_provider,
                        jdump({"chat": chat_granted, "whatsapp": False, "call": False}),
                        request_id,
                    ),
                )
                consent_service = ContactConsentService(con)
                consent_service.set_channel(
                    request_id, item["userId"], selected_provider, "chat", chat_granted
                )
                consent_service.set_channel(
                    request_id, item["userId"], selected_provider, "whatsapp", False
                )
                consent_service.set_channel(
                    request_id, item["userId"], selected_provider, "call", False
                )
                con.execute(
                    """UPDATE request_dispatches SET status=CASE WHEN provider_id=? THEN 'accepted'
                    ELSE 'closed' END,accepted_at=CASE WHEN provider_id=? THEN CURRENT_TIMESTAMP
                    ELSE accepted_at END,updated_at=CURRENT_TIMESTAMP WHERE request_id=?""",
                    (selected_provider, selected_provider, request_id),
                )
                create_notification(
                    con, "provider", selected_provider, "اختار العميل عرضك",
                    f"تم اختيار عرضك لخدمة {item['serviceName'] or item['serviceValue']}.",
                    type_="request", related_id=request_id, priority="high",
                    action_text="فتح الطلب", action_route=f"provider:request:{request_id}",
                )
                create_notification(
                    con, "admin", "", "تم اختيار عرض",
                    f"{request_id} - المزود {selected_provider}",
                    type_="request", related_id=request_id,
                    action_text="فتح الطلب", action_route=f"admin:request:{request_id}",
                )

            elif action == "contact_consent":
                if not is_user or not item["acceptedProviderId"]:
                    return self.send_json({"error": "contact_consent_not_allowed"}, 403)
                consent_service = ContactConsentService(con)
                existing_consent = consent_service.summary(request_id, item["acceptedProviderId"])
                consent = {
                    "chat": bool(data.get("chat", existing_consent.get("chat", False))),
                    "whatsapp": bool(data.get("whatsapp", existing_consent.get("whatsapp", False))),
                    "call": bool(data.get("call", existing_consent.get("call", False))),
                }
                for channel, granted in consent.items():
                    consent_service.set_channel(
                        request_id, item["userId"], item["acceptedProviderId"], channel, granted
                    )
                consent = consent_service.summary(request_id, item["acceptedProviderId"])
                con.execute(
                    "UPDATE customer_requests SET contact_consent=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(consent), request_id),
                )
                enabled = [
                    label for key, label in (("chat", "المحادثة"), ("whatsapp", "واتساب"), ("call", "الاتصال"))
                    if consent[key]
                ]
                create_notification(
                    con, "provider", item["acceptedProviderId"], "حدّث العميل خيارات التواصل",
                    "سمح العميل بـ " + (" و".join(enabled) if enabled else "لا توجد قناة تواصل مفعلة بعد"),
                    type_="request", related_id=request_id, priority="normal",
                    action_text="فتح الطلب", action_route=f"provider:request:{request_id}",
                )

            elif action == "message":
                accepted_provider = item["acceptedProviderId"]
                consent_service = ContactConsentService(con)
                chat_allowed = bool(
                    accepted_provider
                    and consent_service.allowed(request_id, accepted_provider, "chat")
                )
                if not (is_user or (is_provider and provider_id == accepted_provider)):
                    return self.send_json({"error": "chat_not_allowed"}, 403)
                if not chat_allowed:
                    return self.send_json({"error": "chat_consent_required"}, 403)
                text = str(data.get("text", "") or "").strip()[:1000]
                image_path = ""
                audio_path = ""
                message_id = slug("msg")
                try:
                    if data.get("imageData"):
                        image_path = save_upload_data(
                            request_id, data["imageData"], f"{message_id}-image",
                            IMAGE_MIMES, 2_500_000,
                        )
                    if data.get("audioData"):
                        audio_path = save_upload_data(
                            request_id, data["audioData"], f"{message_id}-audio",
                            CHAT_MIMES, 4_000_000,
                        )
                except ValueError as err:
                    return self.send_json({"error": str(err)}, 400)
                if not text and not image_path and not audio_path:
                    return self.send_json({"error": "empty_message"}, 400)
                messages = list(item.get("messages") or [])
                message = {
                    "id": message_id,
                    "sender": "user" if is_user else "provider",
                    "senderId": user_id if is_user else provider_id,
                    "text": text,
                    "image": image_path,
                    "audio": audio_path,
                    "createdAt": datetime.now(UTC).isoformat(),
                }
                messages.append(message)
                con.execute(
                    "UPDATE customer_requests SET messages=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(messages[-120:]), request_id),
                )
                target_kind = "provider" if is_user else "user"
                target_id = item["acceptedProviderId"] if is_user else item["userId"]
                if is_user:
                    sender_name = item.get("customerName") or "عميل خدماتي"
                else:
                    provider_row = con.execute(
                        "SELECT name FROM providers WHERE id=?", (provider_id,)
                    ).fetchone()
                    sender_name = provider_row["name"] if provider_row else "مزود الخدمة"
                preview = text[:105] or ("صورة جديدة" if image_path else "رسالة صوتية جديدة")
                create_notification(
                    con, target_kind, target_id, f"رسالة جديدة من {sender_name}",
                    f"{item.get('serviceName') or 'طلب خدمة'} • {preview}",
                    type_="chat", related_id=request_id, priority="normal",
                    action_text="فتح المحادثة",
                    action_route=f"{target_kind}:chat:{request_id}",
                )

            elif action == "arrival":
                if not is_provider or provider_id != item["acceptedProviderId"]:
                    return self.send_json({"error": "arrival_not_allowed"}, 403)
                status = str(data.get("status", "onTheWay") or "onTheWay")
                if status not in ("onTheWay", "near", "arrived"):
                    return self.send_json({"error": "invalid_arrival_status"}, 400)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                arrival = {
                    **(item.get("arrival") or {}),
                    "status": status,
                    "providerLocation": {
                        "lat": location.get("lat"),
                        "lng": location.get("lng"),
                        "accuracy": location.get("accuracy", 0),
                        "updatedAt": location.get("updatedAt", datetime.now(UTC).isoformat()),
                    },
                    "etaMinutes": max(0, int(data.get("etaMinutes", 0) or 0)),
                    "startedAt": (item.get("arrival") or {}).get("startedAt")
                    or datetime.now(UTC).isoformat(),
                    "updatedAt": datetime.now(UTC).isoformat(),
                }
                con.execute(
                    "UPDATE customer_requests SET arrival=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (jdump(arrival), request_id),
                )
                if status in ("onTheWay", "arrived"):
                    create_notification(
                        con, "user", item["userId"],
                        "المزود في الطريق" if status == "onTheWay" else "وصل المزود",
                        f"{session.get('name', 'المزود')} "
                        + (
                            f"سيصل خلال نحو {arrival['etaMinutes']} دقيقة."
                            if status == "onTheWay"
                            else "وصل إلى موقع تنفيذ الخدمة."
                        ),
                        type_="request", related_id=request_id, priority="high",
                        action_text="متابعة الوصول", action_route=f"user:request:{request_id}",
                    )

            elif action == "waitlist":
                if not is_user:
                    return self.send_json({"error": "waitlist_not_allowed"}, 403)
                enabled = bool(data.get("enabled", True))
                con.execute(
                    "UPDATE customer_requests SET waitlisted=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (int(enabled), request_id),
                )

            updated = con.execute(
                "SELECT * FROM customer_requests WHERE id=?", (request_id,)
            ).fetchone()
            response_request = row_customer_request(updated, sign_private=True)
            if provider_id:
                consent = response_request.get("contactConsent") or {}
                if response_request.get("acceptedProviderId") != provider_id or not (
                    consent.get("whatsapp") or consent.get("call")
                ):
                    response_request["phone"] = ""
            return self.send_json({"ok": True, "request": response_request})

    def notification_action(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        notification_id = str(data.get("id", ""))
        action = data.get("action", "read")
        target_kind = session.get("kind")
        target_id = session.get("providerId") or session.get("userId") or ""
        with db() as con:
            row = con.execute("SELECT * FROM app_notifications WHERE id=?", (notification_id,)).fetchone()
            if not row:
                return self.send_json({"error": "notification_not_found"}, 404)
            if target_kind != "admin" and (
                row["target_kind"] != target_kind or row["target_id"] != target_id
            ):
                return self.send_json({"error": "notification_access_denied"}, 403)
            if action == "delete":
                con.execute("DELETE FROM app_notifications WHERE id=?", (notification_id,))
            else:
                con.execute("UPDATE app_notifications SET is_read=1 WHERE id=?", (notification_id,))
            return self.send_json({"ok": True})

    def recovery_request(self, data):
        phone = normalize_phone(data.get("phone", ""))
        kind = data.get("kind", "user")
        if kind not in ("user", "provider"):
            return self.send_json({"error": "invalid_account_kind"}, 400)
        with db() as con:
            recent = con.execute(
                """SELECT COUNT(*) n FROM password_recoveries
                WHERE phone=? AND created_at>=datetime('now','-1 hour')""", (phone,)
            ).fetchone()["n"]
            if int(recent or 0) >= 5:
                return self.send_json({"error": "recovery_rate_limited"}, 429)
            if kind == "user":
                row = con.execute("SELECT id,name FROM app_users WHERE phone=? AND status='active'", (phone,)).fetchone()
            else:
                row = con.execute(
                    """SELECT id,name FROM providers WHERE active=1 AND status!='deleted'
                    AND (phone=? OR phone=?)""",
                    (phone, phone.replace("968", "", 1)),
                ).fetchone()
            if not row:
                # Keep the response indistinguishable from an existing account.
                return self.send_json(
                    {
                        "ok": True,
                        "recoveryId": slug("rcv"),
                        "deliveryConfigured": whatsapp_configured(),
                    },
                    202,
                )
            development_code = os.environ.get("KHADAMATI_DEV_OTP_CODE", "").strip()
            code = (
                development_code
                if APP_ENV != "production" and development_code
                else f"{secrets.randbelow(1_000_000):06d}"
            )
            recovery_id = slug("rcv")
            con.execute(
                """INSERT INTO password_recoveries(
                id,account_kind,account_id,phone,code_hash,expires_at)
                VALUES(?,?,?,?,?,?)""",
                (recovery_id, kind, row["id"], phone, hash_pin(code), iso_datetime(minutes=10)),
            )
            create_notification(
                con, "admin", "", "طلب استعادة رمز",
                f"{row['name']} - {phone}", type_="security", related_id=row["id"],
                priority="high", action_text="فتح الحساب",
                action_route=f"admin:{kind}:{row['id']}",
            )
        delivery = send_whatsapp(phone, f"رمز استعادة حساب خدماتي هو: {code}. صالح لمدة 10 دقائق.")
        if APP_ENV == "production" and not delivery.get("ok"):
            with db() as con:
                con.execute("DELETE FROM password_recoveries WHERE id=?", (recovery_id,))
            return self.send_json({"error": "otp_delivery_unavailable"}, 503)
        response = {"ok": True, "recoveryId": recovery_id, "deliveryConfigured": delivery.get("configured", False)}
        if APP_ENV != "production" and development_code:
            response["debugCode"] = code
        return self.send_json(response)

    def recovery_complete(self, data):
        recovery_id = str(data.get("recoveryId", ""))
        code = str(data.get("code", ""))
        pin = str(data.get("pin", ""))
        if not re.fullmatch(r"\d{4,8}", pin):
            return self.send_json({"error": "pin_too_short"}, 400)
        with db() as con:
            row = con.execute(
                "SELECT * FROM password_recoveries WHERE id=? AND COALESCE(used_at,'')=''",
                (recovery_id,),
            ).fetchone()
            if not row:
                return self.send_json({"error": "recovery_not_found"}, 404)
            try:
                expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
            except ValueError:
                expires = datetime.now(UTC) - timedelta(seconds=1)
            if expires <= datetime.now(UTC):
                return self.send_json({"error": "recovery_expired"}, 410)
            if int(row["attempts"] or 0) >= 5 or not verify_secret(code, row["code_hash"]):
                con.execute("UPDATE password_recoveries SET attempts=attempts+1 WHERE id=?", (recovery_id,))
                return self.send_json({"error": "invalid_recovery_code"}, 403)
            if row["account_kind"] == "user":
                con.execute(
                    "UPDATE app_users SET pin_hash=? WHERE id=?", (hash_pin(pin), row["account_id"])
                )
            else:
                con.execute(
                    "UPDATE providers SET pin_hash=? WHERE id=?", (hash_pin(pin), row["account_id"])
                )
            con.execute("UPDATE password_recoveries SET used_at=CURRENT_TIMESTAMP WHERE id=?", (recovery_id,))
            clear_login_failures(con, row["account_kind"], row["account_id"])
            revoke_account_sessions(con, row["account_kind"], row["account_id"])
        return self.send_json({"ok": True})

    def delete_account(self, data):
        session = self.session()
        if not session or session.get("kind") not in ("user", "provider"):
            return self.send_json({"error": "auth_required"}, 401)
        pin = str(data.get("pin", ""))
        with db() as con:
            if session["kind"] == "user":
                account_id = session["userId"]
                row = con.execute("SELECT pin_hash FROM app_users WHERE id=?", (account_id,)).fetchone()
                if not row or not row["pin_hash"]:
                    return self.send_json({"error": "pin_not_configured"}, 409)
                if row and row["pin_hash"] and not verify_secret(pin, row["pin_hash"]):
                    return self.send_json({"error": "invalid_user_pin"}, 403)
                anonymous_phone = f"deleted-{hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:16]}"
                con.execute(
                    """UPDATE app_users SET status='deleted',name='حساب محذوف',phone=?,pin_hash='',
                    avatar='',latitude=NULL,longitude=NULL,location_updated_at='',updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (anonymous_phone, account_id),
                )
                con.execute(
                    """UPDATE contact_consents SET status='revoked',revoked_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND status='granted'""",
                    (account_id,),
                )
                con.execute(
                    "UPDATE push_subscriptions SET active=0 WHERE target_kind='user' AND target_id=?",
                    (account_id,),
                )
            else:
                account_id = session["providerId"]
                row = con.execute("SELECT pin_hash FROM providers WHERE id=?", (account_id,)).fetchone()
                if not row or not verify_secret(pin, row["pin_hash"]):
                    return self.send_json({"error": "invalid_provider_login"}, 403)
                anonymous_phone = f"deleted-{hashlib.sha256(account_id.encode('utf-8')).hexdigest()[:16]}"
                con.execute(
                    """UPDATE providers SET active=0,status='deleted',listing_enabled=0,request_enabled=0,
                    phone=?,pin_hash='',latitude=NULL,longitude=NULL,location_updated_at='',updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                    (anonymous_phone, account_id),
                )
                con.execute(
                    "UPDATE push_subscriptions SET active=0 WHERE target_kind='provider' AND target_id=?",
                    (account_id,),
                )
            revoke_account_sessions(con, session["kind"], account_id)
            create_notification(
                con, "admin", "", "تم حذف حساب",
                f"{session['kind']} - {account_id}", type_="security",
                related_id=account_id, priority="high",
            )
            log_audit(con, session, "account.deleted", account_id, "personal_data_anonymized")
        return self.send_json({"ok": True})

    def push_subscribe(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        subscription = data.get("subscription") or {}
        endpoint = safe_text(subscription.get("endpoint"), 2048)
        endpoint_url = urlparse(endpoint)
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        if (
            endpoint_url.scheme != "https"
            or not endpoint_url.netloc
            or not safe_text(keys.get("p256dh"), 512)
            or not safe_text(keys.get("auth"), 512)
        ):
            return self.send_json({"error": "push_endpoint_required"}, 400)
        subscription = {
            "endpoint": endpoint,
            "expirationTime": subscription.get("expirationTime"),
            "keys": {
                "p256dh": safe_text(keys.get("p256dh"), 512),
                "auth": safe_text(keys.get("auth"), 512),
            },
        }
        target_id = session.get("providerId") or session.get("userId") or session.get("id") or ""
        with db() as con:
            con.execute(
                """INSERT INTO push_subscriptions(
                id,target_kind,target_id,endpoint,subscription_json)
                VALUES(?,?,?,?,?)
                ON CONFLICT(endpoint) DO UPDATE SET target_kind=excluded.target_kind,
                target_id=excluded.target_id,subscription_json=excluded.subscription_json,active=1""",
                (slug("push"), session["kind"], target_id, endpoint, jdump(subscription)),
            )
        return self.send_json({"ok": True, "deliveryReady": bool(os.environ.get("VAPID_PRIVATE_KEY"))})

    def policy_accept(self, data):
        session = self.session()
        if not session:
            return self.send_json({"error": "auth_required"}, 401)
        policy_version = str(data.get("version", POLICY_VERSION) or POLICY_VERSION)[:40]
        if policy_version != POLICY_VERSION:
            return self.send_json({"error": "policy_version_outdated", "currentVersion": POLICY_VERSION}, 409)
        allowed_documents = {"privacy", "quality", "terms", "cancellation"}
        documents = [
            item for item in data.get("documents", list(allowed_documents))
            if item in allowed_documents
        ]
        if not documents:
            return self.send_json({"error": "policy_documents_required"}, 400)
        user_id = session.get("userId") or session.get("providerId") or session.get("id") or ""
        phone = session.get("phone", "")
        with db() as con:
            if data.get("action") == "withdraw":
                con.execute(
                    """UPDATE policy_acceptances SET withdrawn_at=CURRENT_TIMESTAMP
                    WHERE user_id=? AND policy_version=? AND COALESCE(withdrawn_at,'')=''""",
                    (user_id, policy_version),
                )
                log_audit(con, session, "policy.withdrawn", user_id, policy_version)
                return self.send_json({"ok": True, "withdrawn": True})
            existing = con.execute(
                """SELECT id FROM policy_acceptances WHERE user_id=? AND policy_version=?
                AND COALESCE(withdrawn_at,'')='' LIMIT 1""", (user_id, policy_version)
            ).fetchone()
            if not existing:
                con.execute(
                    """INSERT INTO policy_acceptances(
                    id,user_id,phone,policy_version,document_types,language,metadata)
                    VALUES(?,?,?,?,?,?,?)""",
                    (
                        slug("pol"), user_id, phone, policy_version, jdump(documents),
                        "en" if data.get("language") == "en" else "ar",
                        jdump({"source": "in_app", "consent": True}),
                    ),
                )
                log_audit(con, session, "policy.accepted", user_id, policy_version)
        return self.send_json({"ok": True, "version": policy_version, "documents": documents})

    def provider_post(self, path, data):
        permission = {
            "/api/provider/profile": "profile",
            "/api/provider/image": "media",
            "/api/provider/work-images": "media",
            "/api/provider/media": "media",
            "/api/provider/documents": "documents",
            "/api/provider/pin": "profile",
            "/api/provider/subscription-request": "subscription",
            "/api/provider/payment-intent": "subscription",
            "/api/provider/team": "team",
            "/api/provider/branches": "branches",
        }.get(path, "requests")
        session = self.require_provider(permission)
        if not session:
            return
        with db() as con:
            row = con.execute("SELECT * FROM providers WHERE id=?", (session["providerId"],)).fetchone()
            if not row:
                return self.send_json({"error": "not_found"}, 404)
            provider = row_provider(row, private=True)
            if path == "/api/provider/profile":
                entitlements = EntitlementService(con).profile_limits(
                    provider["id"], preserve_existing=True
                )
                name = safe_text(data.get("name", provider["name"]), 120)
                phone = normalize_phone(data.get("phone", provider["phone"]))
                bio = safe_text(data.get("bio", provider["bio"]), 900)
                commercial_no = safe_text(
                    data.get("commercialNo", provider.get("commercialNo", "")), 120
                )
                status = data.get("status", provider["status"])
                if status not in {"available", "busy", "unavailable", "under_review"}:
                    return self.send_json({"error": "invalid_provider_status"}, 400)
                areas_value = data.get("areas", provider["areas"])
                if not isinstance(areas_value, list):
                    return self.send_json({"error": "areas_must_be_list"}, 400)
                areas = list(
                    dict.fromkeys(safe_text(area, 80) for area in areas_value if safe_text(area, 80))
                )
                try:
                    location = normalized_location(
                        data.get("location", provider.get("location"))
                    )
                    services = normalized_provider_services(
                        con,
                        data.get("services", provider["services"]),
                        limit=max(1, int(entitlements.get("maxServices") or 1)),
                        category_limit=max(1, int(entitlements.get("maxCategories") or 1)),
                        default_areas=areas,
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                if not name or len(phone) < 11:
                    return self.send_json({"error": "name_and_valid_phone_required"}, 400)
                if not commercial_no:
                    return self.send_json({"error": "commercial_number_required"}, 400)
                word_count = len(bio.split())
                if word_count < 3 or word_count > 20:
                    return self.send_json({"error": "description_word_limit"}, 400)
                if not services:
                    return self.send_json({"error": "service_required"}, 400)
                primary_service_id = safe_text(
                    data.get("primaryServiceId", provider.get("primaryServiceId", "")), 80
                )
                service_ids = {service.get("serviceId") for service in services}
                if primary_service_id not in service_ids:
                    primary_service_id = services[0].get("serviceId", "")
                if len(areas) > int(entitlements.get("maxWilayats") or max(1, len(areas))):
                    return self.send_json({"error": "area_limit_exceeded"}, 409)
                provider.update({
                    "name": name,
                    "phone": phone,
                    "commercialNo": commercial_no,
                    "verificationExpiry": safe_text(data.get("verificationExpiry", provider.get("verificationExpiry", "")), 40),
                    "commercialExpiry": safe_text(data.get("commercialExpiry", provider.get("commercialExpiry", "")), 40),
                    "licenseExpiry": safe_text(data.get("licenseExpiry", provider.get("licenseExpiry", "")), 40),
                    "gov": safe_text(data.get("gov", provider["gov"]), 80),
                    "wilayah": safe_text(data.get("wilayah", provider["wilayah"]), 80),
                    "location": location,
                    "areas": areas,
                    "bio": bio,
                    "hours": safe_text(data.get("hours", provider["hours"]), 240),
                    "status": status,
                    "services": services,
                    "primaryServiceId": primary_service_id,
                    "mapVisible": strict_bool(
                        data.get("mapVisible"), provider.get("mapVisible", True)
                    ),
                    "cardImage": data.get("cardImage", provider.get("cardImage", "")),
                    "beforeAfter": data.get("beforeAfter", provider.get("beforeAfter", [])),
                    "beforeAfterData": data.get("beforeAfterData", {}),
                    "introVideoUrl": data.get("introVideoUrl", provider.get("introVideoUrl", "")),
                    "introVideoData": data.get("introVideoData", ""),
                    "active": provider["active"],
                    "verified": provider["verified"],
                    "featured": provider["featured"],
                })
                if data.get("imageData"):
                    provider["imageData"] = data["imageData"]
                if data.get("workImagesData"):
                    provider["workImagesData"] = data["workImagesData"]
                if data.get("documentsData"):
                    provider["documentsData"] = data["documentsData"]
                try:
                    EntitlementService(con).validate_profile(
                        provider["id"], services=provider.get("services", []), areas=provider.get("areas", [])
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                saved_provider = upsert_provider(con, provider)
                if saved_provider.get("status") == "available":
                    waiting_rows = con.execute(
                        "SELECT * FROM customer_requests WHERE waitlisted=1 AND status='unavailable'"
                    ).fetchall()
                    for waiting_row in waiting_rows:
                        waiting_request = row_customer_request(waiting_row)
                        if not request_matches_provider(waiting_request, saved_provider):
                            continue
                        marketplace = RequestMarketplace(con)
                        ranked = marketplace.schedule(waiting_request["id"])
                        if not ranked:
                            continue
                        released = marketplace.release_due(waiting_request["id"])
                        create_marketplace_notifications(con, released)
                        create_notification(
                            con, "user", waiting_request["userId"], "توفر مزود لخدمتك",
                            f"أصبح هناك مزود مناسب لطلب {waiting_request['serviceName'] or waiting_request['serviceValue']}.",
                            type_="request", related_id=waiting_request["id"], priority="high",
                            action_text="فتح الطلب", action_route=f"user:request:{waiting_request['id']}",
                        )
                log_audit(con, session, "provider.profile.updated", provider["id"], provider["name"])
                updated = con.execute("SELECT * FROM providers WHERE id=?", (provider["id"],)).fetchone()
                return self.send_json({"ok": True, "provider": row_provider(updated, private=True, sign_private=True)})
            if path == "/api/provider/image":
                image_path = save_data_url(provider["id"], data.get("imageData", ""))
                con.execute("UPDATE providers SET image_path=? WHERE id=?", (image_path, provider["id"]))
                recompute_provider_quality(con, provider["id"])
                return self.send_json({"ok": True, "imageUrl": image_url(image_path)})
            if path == "/api/provider/work-images":
                entitlements = EntitlementService(con).for_provider(provider["id"])
                images = save_many_images(
                    provider["id"], data.get("workImagesData", []), "work",
                    max(1, int(entitlements.get("maxImages") or 5)),
                )
                if images:
                    con.execute("UPDATE providers SET work_images=? WHERE id=?", (jdump(images), provider["id"]))
                    recompute_provider_quality(con, provider["id"])
                return self.send_json({"ok": True, "workImageUrls": urls(images)})
            if path == "/api/provider/media":
                action = data.get("action")
                raw_path = str(data.get("path", "") or "")
                selected_path = raw_path.lstrip("/")
                allowed = {provider.get("imagePath", ""), *(provider.get("workImages") or [])}
                if selected_path not in allowed:
                    return self.send_json({"error": "media_not_found"}, 404)
                if action == "set-card":
                    con.execute("UPDATE providers SET card_image=? WHERE id=?", (image_url(selected_path), provider["id"]))
                    return self.send_json({"ok": True, "cardImage": image_url(selected_path)})
                if action == "delete":
                    work_images = [p for p in provider.get("workImages", []) if p != selected_path]
                    avatar = "" if provider.get("imagePath") == selected_path else provider.get("imagePath", "")
                    card_image = provider.get("cardImage", "")
                    if card_image.lstrip("/") == selected_path:
                        card_image = image_url(avatar) if avatar else (image_url(work_images[0]) if work_images else "")
                    con.execute(
                        "UPDATE providers SET image_path=?,work_images=?,card_image=? WHERE id=?",
                        (avatar, jdump(work_images), card_image, provider["id"]),
                    )
                    target = (UPLOAD_DIR / Path(selected_path).name).resolve()
                    try:
                        target.relative_to(UPLOAD_DIR.resolve())
                        if target.is_file():
                            target.unlink()
                    except ValueError:
                        pass
                    recompute_provider_quality(con, provider["id"])
                    return self.send_json({"ok": True, "cardImage": card_image})
                return self.send_json({"error": "invalid_media_action"}, 400)
            if path == "/api/provider/documents":
                docs = save_many_documents(provider["id"], data.get("documentsData", []), "doc", 3)
                if docs:
                    con.execute("UPDATE providers SET documents=? WHERE id=?", (jdump(docs), provider["id"]))
                return self.send_json({
                    "ok": True,
                    "documents": [secure_media_url(path) for path in docs],
                })
            if path == "/api/provider/pin":
                if session.get("role") != "provider_owner" or session.get("memberId"):
                    return self.send_json({"error": "provider_owner_required"}, 403)
                pin = str(data.get("pin", ""))
                if not re.fullmatch(r"\d{4,8}", pin):
                    return self.send_json({"error": "pin_too_short"}, 400)
                if not verify_secret(data.get("currentPin", ""), row["pin_hash"]):
                    return self.send_json({"error": "current_pin_incorrect"}, 403)
                con.execute("UPDATE providers SET pin_hash=? WHERE id=?", (hash_pin(pin), provider["id"]))
                authorization = str(self.headers.get("Authorization", "") or "")
                current_hash = hash_secret(authorization[7:].strip()) if authorization.startswith("Bearer ") else ""
                revoke_account_sessions(con, "provider", provider["id"], current_hash)
                return self.send_json({"ok": True})
            if path == "/api/provider/subscription-request":
                package_id = str(data.get("packageId", "") or "")
                try:
                    result = SubscriptionService(con).request_plan(
                        provider["id"], package_id,
                        coupon_code=str(data.get("couponCode", "") or ""),
                        payment_required=package_id != "foundation_12m",
                        actor=f"provider:{provider['id']}",
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                sub_id = result["subscriptionId"]
                pkg = PlanCatalog.get(con, package_id, False)
                create_notification(
                    con, "admin", "", "طلب ترقية باقة",
                    f"{provider['name']} - {pkg['ar']} - {result['amount']} ر.ع",
                    type_="subscription", related_id=sub_id, priority="high",
                    action_text="مراجعة الطلب", action_route=f"admin:subscription:{sub_id}",
                )
                log_audit(con, session, "subscription.requested", provider["id"], package_id)
                return self.send_json({"ok": True, **result})
            if path == "/api/provider/payment-intent":
                try:
                    result = PaymentAdapter(con).create_intent(
                        str(data.get("subscriptionId", "") or ""), provider["id"],
                        client_amount=data.get("amount"),
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                log_audit(con, session, "payment.intent.created", result["paymentId"], result["reference"])
                return self.send_json({"ok": True, **result}, 201)
            if path == "/api/provider/team":
                entitlements = EntitlementService(con).for_provider(provider["id"])
                requested_id = safe_text(data.get("id"), 100)
                member_id = requested_id or slug("member")
                owned_member = None
                if requested_id:
                    owned_member = con.execute(
                        "SELECT * FROM provider_team_members WHERE id=? AND provider_id=?",
                        (member_id, provider["id"]),
                    ).fetchone()
                    if not owned_member:
                        return self.send_json({"error": "team_member_not_found"}, 404)
                if session.get("role") == "provider_manager" and owned_member and owned_member["role"] != "provider_staff":
                    return self.send_json({"error": "provider_permission_denied"}, 403)
                if data.get("action") == "delete":
                    result = con.execute(
                        "UPDATE provider_team_members SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND provider_id=?",
                        (member_id, provider["id"]),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "team_member_not_found"}, 404)
                    log_audit(con, session, "provider.team.disabled", member_id, provider["id"])
                    return self.send_json({"ok": True})
                existing_count = con.execute(
                    """SELECT COUNT(*) n FROM provider_team_members
                    WHERE provider_id=? AND active=1 AND id!=?""",
                    (provider["id"], member_id),
                ).fetchone()["n"]
                if int(existing_count or 0) >= int(entitlements.get("teamMembers") or 1) - 1:
                    return self.send_json({"error": "team_member_limit_exceeded"}, 409)
                role = str(data.get("role", "provider_staff") or "provider_staff")
                if role not in {"provider_manager", "provider_staff"}:
                    return self.send_json({"error": "invalid_provider_role"}, 400)
                if session.get("role") == "provider_manager" and role != "provider_staff":
                    return self.send_json({"error": "provider_permission_denied"}, 403)
                name = safe_text(data.get("name"), 120)
                phone = normalize_phone(data.get("phone", ""))
                if not name or len(phone) < 11:
                    return self.send_json({"error": "name_and_valid_phone_required"}, 400)
                pin_hash = owned_member["pin_hash"] if owned_member else ""
                if data.get("pin"):
                    if len(str(data["pin"])) < 4 or len(str(data["pin"])) > 128:
                        return self.send_json({"error": "invalid_pin_length"}, 400)
                    pin_hash = hash_pin(str(data["pin"]))
                if not pin_hash:
                    return self.send_json({"error": "pin_required"}, 400)
                selected_permissions = [
                    item for item in data.get("permissions", [])
                    if item in PROVIDER_ROLE_PERMISSIONS[role]
                ] if isinstance(data.get("permissions"), list) else []
                try:
                    con.execute(
                        """INSERT INTO provider_team_members(
                        id,provider_id,name,phone,role,pin_hash,permissions,active)
                        VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,phone=excluded.phone,role=excluded.role,
                        pin_hash=excluded.pin_hash,permissions=excluded.permissions,active=1,
                        updated_at=CURRENT_TIMESTAMP
                        WHERE provider_team_members.provider_id=excluded.provider_id""",
                        (
                            member_id, provider["id"], name, phone, role, pin_hash,
                            jdump(selected_permissions),
                        ),
                    )
                except sqlite3.IntegrityError:
                    return self.send_json({"error": "team_phone_already_used"}, 409)
                log_audit(con, session, "provider.team.upserted", member_id, role)
                return self.send_json({"ok": True, "id": member_id})
            if path == "/api/provider/branches":
                entitlements = EntitlementService(con).for_provider(provider["id"])
                requested_id = safe_text(data.get("id"), 100)
                branch_id = requested_id or slug("branch")
                if requested_id and not con.execute(
                    "SELECT id FROM provider_branches WHERE id=? AND provider_id=?",
                    (branch_id, provider["id"]),
                ).fetchone():
                    return self.send_json({"error": "branch_not_found"}, 404)
                if data.get("action") == "delete":
                    result = con.execute(
                        "UPDATE provider_branches SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=? AND provider_id=?",
                        (branch_id, provider["id"]),
                    )
                    if result.rowcount != 1:
                        return self.send_json({"error": "branch_not_found"}, 404)
                    log_audit(con, session, "provider.branch.disabled", branch_id, provider["id"])
                    return self.send_json({"ok": True})
                existing_count = con.execute(
                    """SELECT COUNT(*) n FROM provider_branches
                    WHERE provider_id=? AND active=1 AND id!=?""",
                    (provider["id"], branch_id),
                ).fetchone()["n"]
                if int(existing_count or 0) >= int(entitlements.get("branches") or 1):
                    return self.send_json({"error": "branch_limit_exceeded"}, 409)
                name = safe_text(data.get("name"), 120)
                if not name:
                    return self.send_json({"error": "branch_name_required"}, 400)
                try:
                    location = normalized_location(data.get("location"))
                except DomainError as err:
                    return self.send_domain_error(err)
                con.execute(
                    """INSERT INTO provider_branches(
                    id,provider_id,name,gov,wilayah,address,latitude,longitude,phone,active)
                    VALUES(?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,gov=excluded.gov,wilayah=excluded.wilayah,
                    address=excluded.address,latitude=excluded.latitude,longitude=excluded.longitude,
                    phone=excluded.phone,active=1,updated_at=CURRENT_TIMESTAMP
                    WHERE provider_branches.provider_id=excluded.provider_id""",
                    (
                        branch_id, provider["id"], name,
                        safe_text(data.get("gov"), 80), safe_text(data.get("wilayah"), 80),
                        safe_text(data.get("address"), 240), location.get("lat"), location.get("lng"),
                        normalize_phone(data.get("phone", provider.get("phone", ""))),
                    ),
                )
                log_audit(con, session, "provider.branch.upserted", branch_id, provider["id"])
                return self.send_json({"ok": True, "id": branch_id})
        return self.send_json({"error": "not_found"}, 404)

    def admin_post(self, path, data):
        permission = {
            "/api/admin/providers": "manage_providers",
            "/api/admin/provider-status": "manage_providers",
            "/api/admin/provider-delete": "manage_providers",
            "/api/admin/request-decision": "review_requests",
            "/api/admin/review-status": "manage_quality",
            "/api/admin/complaint-status": "manage_quality",
            "/api/admin/packages": "manage_subscriptions",
            "/api/admin/subscriptions": "manage_subscriptions",
            "/api/admin/payments": "manage_finance",
            "/api/admin/coupons": "manage_subscriptions",
            "/api/admin/campaigns": "manage_campaigns",
            "/api/admin/team": "manage_team",
            "/api/admin/branches": "manage_team",
            "/api/admin/contact-consents": "manage_consent",
            "/api/admin/settings": "manage_settings",
            "/api/admin/users": "manage_admins",
            "/api/admin/test-whatsapp": "manage_settings",
            "/api/admin/ads": "manage_settings",
        }.get(path, "view_reports")
        session = self.require_admin(permission)
        if not session:
            return
        with db() as con:
            if path == "/api/admin/providers":
                p = upsert_provider(con, data)
                log_audit(con, session, "provider.upserted", p["id"], p.get("name", ""))
                return self.send_json({"ok": True, "provider": p})
            if path == "/api/admin/provider-status":
                provider_id = safe_text(data.get("id"), 120)
                status = safe_text(data.get("status", "available"), 30)
                if status not in {"available", "busy", "unavailable", "under_review", "pending", "suspended", "deleted"}:
                    return self.send_json({"error": "invalid_provider_status"}, 400)
                flags = []
                for key, default in (("active", 1), ("verified", 0), ("featured", 0)):
                    value = data.get(key, default)
                    if value not in (True, False, 0, 1):
                        return self.send_json({"error": "invalid_boolean", "field": key}, 400)
                    flags.append(int(bool(value)))
                if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                    return self.send_json({"error": "provider_not_found"}, 404)
                con.execute(
                    "UPDATE providers SET active=?, verified=?, featured=?, status=? WHERE id=?",
                    (*flags, status, provider_id),
                )
                recompute_provider_quality(con, provider_id)
                log_audit(con, session, "provider.status.updated", provider_id, status)
                return self.send_json({"ok": True})
            if path == "/api/admin/provider-delete":
                provider_id = str(data.get("id", "") or "")
                provider_row = con.execute(
                    "SELECT id,name,phone,active,status FROM providers WHERE id=?", (provider_id,)
                ).fetchone()
                if not provider_row:
                    return self.send_json({"error": "provider_not_found"}, 404)
                if int(provider_row["active"] or 0) or provider_row["status"] not in {
                    "unavailable", "suspended", "deleted"
                }:
                    return self.send_json({"error": "provider_must_be_stopped_before_delete"}, 409)
                anonymous_phone = f"deleted-{hashlib.sha256(provider_id.encode('utf-8')).hexdigest()[:16]}"
                con.execute(
                    """UPDATE providers SET active=0,status='deleted',listing_enabled=0,
                    request_enabled=0,name='حساب مزود محذوف',phone=?,pin_hash='',image_path='',
                    card_image='',work_images='[]',documents='[]',latitude=NULL,longitude=NULL,
                    location_updated_at='',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (anonymous_phone, provider_id),
                )
                con.execute(
                    "UPDATE push_subscriptions SET active=0 WHERE target_kind='provider' AND target_id=?",
                    (provider_id,),
                )
                revoke_account_sessions(con, "provider", provider_id)
                log_audit(con, session, "provider.deleted", provider_id, provider_row["name"])
                return self.send_json({"ok": True})
            if path == "/api/admin/request-decision":
                decision = safe_text(data.get("decision"), 20)
                if decision not in {"accept", "reject"}:
                    return self.send_json({"error": "invalid_request_decision"}, 400)
                row = con.execute("SELECT payload FROM provider_requests WHERE id=?", (data.get("id"),)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                payload = jload(row["payload"], {})
                description = safe_text(payload.get("bio") or payload.get("note"), 600)
                if decision == "accept":
                    note_words = len(description.split())
                    if not payload.get("commercialNo"):
                        return self.send_json({"error": "commercial_number_required"}, 400)
                    if note_words < 3 or note_words > 20:
                        return self.send_json({"error": "description_word_limit"}, 400)
                    if not payload.get("documents"):
                        return self.send_json({"error": "documents_required"}, 400)
                    if not payload.get("pinHash"):
                        return self.send_json({"error": "pin_not_configured"}, 400)
                con.execute("DELETE FROM provider_requests WHERE id=?", (data.get("id"),))
                if decision == "accept":
                    provider = {
                        "id": slug("p"),
                        "name": payload.get("name", ""),
                        "phone": payload.get("phone", ""),
                        "providerType": payload.get("providerType", "individual"),
                        "companyName": payload.get("companyName", ""),
                        "companyId": payload.get("companyName", "") if payload.get("providerType") == "company" else "",
                        "commercialNo": payload.get("commercialNo", ""),
                        "companySize": payload.get("companySize", ""),
                        "businessRole": payload.get("businessRole", ""),
                        "gov": payload.get("gov", ""),
                        "wilayah": payload.get("wilayah", ""),
                        "location": payload.get("location"),
                        "areas": [payload.get("wilayah", "")],
                        "bio": description,
                        "hours": payload.get("hours", ""),
                        "status": "available",
                        "active": True,
                        "verified": True,
                        "featured": False,
                        "packageId": "foundation_12m",
                        "rating": 0,
                        "reviews": 0,
                        "imagePath": payload.get("imagePath", ""),
                        "workImages": payload.get("workImages", []),
                        "documents": payload.get("documents", []),
                        "services": [],
                        "stats": {"views": 0, "whatsapp": 0, "calls": 0},
                        "adminNote": "تم قبوله من الطلبات" + (f" | سجل: {payload.get('commercialNo', '')} | فريق: {payload.get('companySize', '')}" if payload.get("providerType") == "company" else f" | مهنة: {payload.get('businessRole', '')}"),
                        "pinHash": payload.get("pinHash") or "",
                    }
                    services = payload.get("services") if isinstance(payload.get("services"), list) else []
                    service_limit = 3
                    provider["services"] = [
                        {
                            "id": svc.get("id") or slug("ps"),
                            "catId": svc.get("catId", ""),
                            "serviceId": svc.get("serviceId", ""),
                            "priceFrom": float(svc.get("priceFrom") or payload.get("priceFrom") or 0),
                            "active": bool(svc.get("active", True)),
                            "areas": svc.get("areas") or [payload.get("wilayah", "")],
                        }
                        for svc in services[:service_limit]
                        if isinstance(svc, dict) and svc.get("catId") and svc.get("serviceId")
                    ]
                    service = payload.get("service", "")
                    if not provider["services"] and "|" in service:
                        cat_id, service_id = service.split("|", 1)
                        provider["services"] = [{"id": slug("ps"), "catId": cat_id, "serviceId": service_id, "priceFrom": float(payload.get("priceFrom") or 0), "active": True, "areas": [payload.get("wilayah", "")]}]
                    upsert_provider(con, provider)
                    promoted_session = {
                        "kind": "provider", "providerId": provider["id"],
                        "name": provider["name"], "role": "provider_owner", "memberId": "",
                        "providerPermissions": list(PROVIDER_ROLE_PERMISSIONS["provider_owner"]),
                    }
                    for auth_row in con.execute(
                        "SELECT id,session_json FROM auth_sessions WHERE revoked=0"
                    ):
                        auth_data = jload(auth_row["session_json"], {})
                        if (
                            auth_data.get("kind") == "provider_pending"
                            and auth_data.get("requestId") == data.get("id")
                        ):
                            con.execute(
                                "UPDATE auth_sessions SET session_json=? WHERE id=?",
                                (jdump(promoted_session), auth_row["id"]),
                            )
                    create_notification(
                        con, "provider", provider["id"], "تم اعتماد حسابك",
                        "أصبح حسابك جاهزًا للدخول واستقبال الطلبات المطابقة لخدماتك.",
                        type_="provider", related_id=provider["id"], priority="high",
                        action_text="فتح الحساب", action_route="home",
                    )
                    try:
                        SubscriptionService(con).request_plan(
                            provider["id"], "foundation_12m", payment_required=False,
                            actor=f"admin:{session['id']}",
                        )
                    except DomainError as err:
                        con.execute(
                            """UPDATE providers SET listing_enabled=0,request_enabled=0,
                            subscription_state='pending_payment' WHERE id=?""",
                            (provider["id"],),
                        )
                        create_notification(
                            con, "admin", "", "تعذر منح فترة التأسيس",
                            f"{provider['name']}: {err.code}", type_="subscription",
                            related_id=provider["id"], priority="high",
                            action_text="فتح المزود", action_route=f"admin:provider:{provider['id']}",
                        )
                    log_audit(con, session, "provider.request.accepted", provider["id"], provider["name"])
                    send_whatsapp(provider["phone"], "تم قبول حسابك كمزود في خدماتي. يمكنك الدخول من بوابة المزودين.")
                    approved_row = con.execute(
                        "SELECT * FROM providers WHERE id=?", (provider["id"],)
                    ).fetchone()
                else:
                    log_audit(con, session, "provider.request.rejected", data.get("id", ""), payload.get("name", ""))
                return self.send_json({
                    "ok": True,
                    "provider": row_provider(approved_row, private=True, sign_private=True)
                    if decision == "accept" and approved_row else None,
                })
            if path == "/api/admin/review-status":
                review_id = safe_text(data.get("id"), 120)
                approved = strict_bool(data.get("approved"), True)
                row = con.execute("SELECT provider_id FROM reviews WHERE id=?", (review_id,)).fetchone()
                if not row:
                    return self.send_json({"error": "review_not_found"}, 404)
                con.execute("UPDATE reviews SET approved=? WHERE id=?", (int(approved), review_id))
                recompute_provider_quality(con, row["provider_id"])
                log_audit(con, session, "review.status.updated", review_id, str(approved))
                return self.send_json({"ok": True})
            if path == "/api/admin/complaint-status":
                complaint_id = data.get("id")
                row = con.execute("SELECT provider_id FROM complaints WHERE id=?", (complaint_id,)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                status = safe_text(data.get("status", "open"), 30)
                priority = safe_text(data.get("priority", "normal"), 30)
                if status not in ("open", "reviewing", "closed"):
                    return self.send_json({"error": "invalid_complaint_status"}, 400)
                if priority not in ("low", "normal", "high"):
                    return self.send_json({"error": "invalid_complaint_priority"}, 400)
                con.execute(
                    "UPDATE complaints SET status=?, priority=?, resolution=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (status, priority, data.get("resolution", ""), complaint_id),
                )
                if row["provider_id"]:
                    recompute_provider_quality(con, row["provider_id"])
                log_audit(con, session, "complaint.status.updated", complaint_id, status)
                return self.send_json({"ok": True})
            if path == "/api/admin/packages":
                package_id = str(data.get("id", "") or "")
                if package_id not in PLAN_IDS:
                    return self.send_json({"error": "fixed_plan_catalog"}, 409)
                entitlements = {
                    "maxServices": bounded_int(data.get("maxServices", 1), 1, minimum=1, maximum=100),
                    "maxCategories": bounded_int(data.get("maxCategories", 1), 1, minimum=1, maximum=20),
                    "maxImages": bounded_int(data.get("maxImages", 5), 5, minimum=1, maximum=100),
                    "maxWilayats": bounded_int(data.get("maxWilayats", 5), 5, minimum=1, maximum=100),
                    "maxGovernorates": bounded_int(data.get("maxGovernorates", 1), 1, minimum=1, maximum=20),
                    "monthlyResponses": bounded_int(data.get("monthlyResponses", 0), 0, minimum=0, maximum=100000),
                    "leadDelayMinutes": bounded_int(data.get("leadDelayMinutes", 0), 0, minimum=0, maximum=1440),
                    "teamMembers": bounded_int(data.get("teamMembers", 1), 1, minimum=1, maximum=100),
                    "branches": bounded_int(data.get("branches", 1), 1, minimum=1, maximum=100),
                    "sharedInbox": strict_bool(data.get("sharedInbox"), False),
                    "advancedReports": strict_bool(data.get("advancedReports"), False),
                }
                con.execute(
                    """UPDATE packages SET ar=?,en=?,price=?,currency='OMR',duration_days=?,
                    max_services=?,max_categories=?,max_images=?,max_wilayats=?,max_governorates=?,
                    monthly_response_limit=?,lead_delay_minutes=?,max_team_members=?,max_branches=?,
                    shared_inbox=?,advanced_reports=?,badge_ar=?,badge_en=?,entitlements=?,
                    active=?,legacy=0 WHERE id=?""",
                    (
                        data.get("ar", "باقة"),
                        data.get("en", "Package"),
                        finite_number(data.get("price", 0), 0, minimum=0, maximum=1_000_000),
                        bounded_int(data.get("durationDays", 30), 30, minimum=1, maximum=3650),
                        entitlements["maxServices"], entitlements["maxCategories"], entitlements["maxImages"],
                        entitlements["maxWilayats"], entitlements["maxGovernorates"],
                        entitlements["monthlyResponses"], entitlements["leadDelayMinutes"],
                        entitlements["teamMembers"], entitlements["branches"],
                        int(entitlements["sharedInbox"]), int(entitlements["advancedReports"]),
                        str(data.get("badgeAr", "") or "")[:80],
                        str(data.get("badgeEn", "") or "")[:80],
                        jdump(entitlements), int(strict_bool(data.get("active"), True)), package_id,
                    ),
                )
                log_audit(con, session, "package.upserted", package_id, data.get("ar", ""))
                saved = con.execute("SELECT * FROM packages WHERE id=?", (package_id,)).fetchone()
                return self.send_json({"ok": True, "package": row_package(saved)})
            if path == "/api/admin/subscriptions":
                action = str(data.get("action", "request") or "request")
                service = SubscriptionService(con)
                try:
                    if action == "activate":
                        result = service.activate(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}"
                        )
                    elif action == "extend":
                        result = service.extend(
                            str(data.get("id", "") or ""),
                            days=int(data.get("days", 0) or 0) or None,
                            actor=f"admin:{session['id']}",
                        )
                    elif action == "suspend":
                        service.suspend(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}",
                            reason=str(data.get("note", "") or ""),
                        )
                        result = {"id": data.get("id"), "status": "suspended"}
                    elif action == "cancel":
                        service.cancel(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}",
                            reason=str(data.get("note", "") or ""),
                        )
                        result = {"id": data.get("id"), "status": "cancelled"}
                    elif action == "refund":
                        service.refund(
                            str(data.get("id", "") or ""), actor=f"admin:{session['id']}",
                            reason=str(data.get("note", "") or ""),
                        )
                        result = {"id": data.get("id"), "status": "refunded"}
                    else:
                        provider_id = str(data.get("providerId", "") or "")
                        package_id = str(data.get("packageId", "") or "")
                        result = service.request_plan(
                            provider_id, package_id,
                            coupon_code=str(data.get("couponCode", "") or ""),
                            payment_required=not bool(data.get("approveWithoutPayment", False)),
                            actor=f"admin:{session['id']}",
                        )
                except DomainError as err:
                    return self.send_domain_error(err)
                sub_id = result.get("id") or result.get("subscriptionId") or data.get("id", "")
                if sub_id and data.get("note"):
                    con.execute(
                        "UPDATE subscriptions SET note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (str(data.get("note", ""))[:500], sub_id),
                    )
                subscription_row = con.execute(
                    """SELECT s.provider_id,s.package_id,s.status,p.ar package_ar
                    FROM subscriptions s LEFT JOIN packages p ON p.id=s.package_id WHERE s.id=?""",
                    (sub_id,),
                ).fetchone() if sub_id else None
                if subscription_row:
                    notification_copy = {
                        "activate": ("تم تفعيل الاشتراك", "أصبحت صلاحيات الباقة متاحة للحساب."),
                        "extend": ("تم تمديد الاشتراك", "تم تحديث تاريخ انتهاء الاشتراك."),
                        "suspend": ("تم إيقاف الاشتراك", "توقف الظهور واستقبال الطلبات حتى إعادة التفعيل."),
                        "cancel": ("تم إلغاء الاشتراك", "تم إلغاء طلب الاشتراك مع الاحتفاظ بسجل الحساب."),
                        "refund": ("تم استرداد الاشتراك", "تم تحديث حالة الاشتراك والمدفوعات."),
                    }
                    if action in notification_copy:
                        title, message = notification_copy[action]
                        create_notification(
                            con, "provider", subscription_row["provider_id"], title, message,
                            type_="subscription", related_id=sub_id, priority="high" if action in {"suspend", "cancel", "refund"} else "normal",
                            action_text="فتح الاشتراك", action_route="subscription",
                        )
                    elif action == "request" and subscription_row["status"] == "pending_payment":
                        create_notification(
                            con, "admin", "", "طلب اشتراك ينتظر الإجراء",
                            f"طلب جديد لباقـة {subscription_row['package_ar'] or subscription_row['package_id']}.",
                            type_="subscription", related_id=sub_id, priority="normal",
                            action_text="فتح الاشتراكات", action_route=f"admin:subscription:{sub_id}",
                        )
                log_audit(con, session, f"subscription.{action}", sub_id, jdump(result))
                return self.send_json({"ok": True, "subscription": result})
            if path == "/api/admin/payments":
                action = str(data.get("action", "confirm") or "confirm")
                payment_id = str(data.get("id", "") or "")
                try:
                    if action == "record":
                        subscription_id = str(data.get("subscriptionId", "") or "")
                        subscription = con.execute(
                            "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
                        ).fetchone()
                        if not subscription:
                            raise DomainError("subscription_not_found", 404)
                        adapter = PaymentAdapter(con)
                        intent = adapter.create_intent(
                            subscription_id,
                            subscription["provider_id"],
                            client_amount=data.get("amount"),
                        )
                        payment_id = intent["paymentId"]
                        method = str(data.get("method", "manual") or "manual")
                        if method not in {"manual", "cash", "bank"}:
                            raise DomainError("invalid_payment_method")
                        con.execute(
                            "UPDATE payments SET method=?,note=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (method, str(data.get("note", ""))[:500], payment_id),
                        )
                        result = adapter.confirm_manual(payment_id, actor=f"admin:{session['id']}")
                    elif action == "confirm":
                        result = PaymentAdapter(con).confirm_manual(
                            payment_id, actor=f"admin:{session['id']}"
                        )
                    elif action == "refund":
                        payment = con.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
                        if not payment:
                            raise DomainError("payment_not_found", 404)
                        if payment["status"] != "paid":
                            raise DomainError("invalid_payment_transition", 409)
                        con.execute(
                            """UPDATE payments SET status='refunded',refunded_at=CURRENT_TIMESTAMP,
                            updated_at=CURRENT_TIMESTAMP WHERE id=?""", (payment_id,)
                        )
                        if payment["subscription_id"]:
                            SubscriptionService(con).refund(
                                payment["subscription_id"], actor=f"admin:{session['id']}",
                                reason=str(data.get("note", "") or ""),
                            )
                        result = {"id": payment_id, "status": "refunded"}
                    else:
                        raise DomainError("invalid_payment_action")
                except DomainError as err:
                    return self.send_domain_error(err)
                if action in {"record", "confirm"}:
                    paid = con.execute(
                        """SELECT p.provider_id,p.subscription_id,s.package_id
                        FROM payments p LEFT JOIN subscriptions s ON s.id=p.subscription_id WHERE p.id=?""",
                        (payment_id,),
                    ).fetchone()
                    if paid:
                        create_notification(
                            con, "provider", paid["provider_id"], "تم تأكيد الدفعة وتفعيل الاشتراك",
                            "تم التحقق من المبلغ وتحديث صلاحيات الحساب وإنشاء سجل الفاتورة.",
                            type_="subscription", related_id=paid["subscription_id"], priority="normal",
                            action_text="فتح الاشتراك", action_route="subscription",
                        )
                log_audit(con, session, f"payment.{action}", payment_id, jdump(result))
                return self.send_json({"ok": True, "payment": result})
            if path == "/api/admin/coupons":
                coupon_id = str(data.get("id") or slug("coupon"))
                if data.get("action") == "disable":
                    con.execute(
                        "UPDATE coupons SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (coupon_id,),
                    )
                    log_audit(con, session, "coupon.disabled", coupon_id, "")
                    return self.send_json({"ok": True})
                code = re.sub(r"[^A-Z0-9_-]", "", str(data.get("code", "") or "").upper())[:32]
                discount_type = str(data.get("discountType", "fixed") or "fixed")
                if not code or discount_type not in {"fixed", "percent"}:
                    return self.send_json({"error": "invalid_coupon"}, 400)
                value = finite_number(data.get("discountValue", 0), minimum=0, maximum=1_000_000)
                if discount_type == "percent" and value > 100:
                    return self.send_json({"error": "invalid_coupon_value"}, 400)
                applies_to = [plan for plan in data.get("appliesTo", []) if plan in PLAN_IDS]
                con.execute(
                    """INSERT INTO coupons(
                    id,code,name_ar,name_en,discount_type,discount_value,applies_to,
                    starts_at,ends_at,max_uses,active)
                    VALUES(?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(id) DO UPDATE SET code=excluded.code,name_ar=excluded.name_ar,
                    name_en=excluded.name_en,discount_type=excluded.discount_type,
                    discount_value=excluded.discount_value,applies_to=excluded.applies_to,
                    starts_at=excluded.starts_at,ends_at=excluded.ends_at,
                    max_uses=excluded.max_uses,active=1,updated_at=CURRENT_TIMESTAMP""",
                    (
                        coupon_id, code, str(data.get("nameAr", "") or "")[:120],
                        str(data.get("nameEn", "") or "")[:120], discount_type, value,
                        jdump(applies_to), str(data.get("startsAt", "") or "")[:40],
                        str(data.get("endsAt", "") or "")[:40],
                        max(0, int(data.get("maxUses", 0) or 0)),
                    ),
                )
                log_audit(con, session, "coupon.upserted", coupon_id, code)
                return self.send_json({"ok": True, "id": coupon_id})
            if path == "/api/admin/campaigns":
                campaign_id = str(data.get("id") or slug("campaign"))
                status = str(data.get("status", "draft") or "draft")
                if status not in {"draft", "scheduled", "active", "paused", "completed", "cancelled"}:
                    return self.send_json({"error": "invalid_campaign_status"}, 400)
                con.execute(
                    """INSERT INTO campaigns(
                    id,name_ar,name_en,kind,starts_at,ends_at,budget,status,rules)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name_ar=excluded.name_ar,name_en=excluded.name_en,kind=excluded.kind,
                    starts_at=excluded.starts_at,ends_at=excluded.ends_at,budget=excluded.budget,
                    status=excluded.status,rules=excluded.rules,updated_at=CURRENT_TIMESTAMP""",
                    (
                        campaign_id, str(data.get("nameAr", "") or "")[:160],
                        str(data.get("nameEn", "") or "")[:160],
                        str(data.get("kind", "subscription") or "subscription")[:40],
                        str(data.get("startsAt", "") or "")[:40],
                        str(data.get("endsAt", "") or "")[:40],
                        finite_number(data.get("budget", 0), minimum=0, maximum=1_000_000_000), status,
                        jdump(data.get("rules", {}) if isinstance(data.get("rules"), dict) else {}),
                    ),
                )
                log_audit(con, session, "campaign.upserted", campaign_id, status)
                return self.send_json({"ok": True, "id": campaign_id})
            if path == "/api/admin/team":
                member_id = safe_text(data.get("id"), 100) or slug("member")
                if data.get("action") == "delete":
                    result = con.execute("UPDATE provider_team_members SET active=0 WHERE id=?", (member_id,))
                    if result.rowcount != 1:
                        return self.send_json({"error": "team_member_not_found"}, 404)
                    log_audit(con, session, "team.disabled", member_id, "")
                    return self.send_json({"ok": True})
                provider_id = safe_text(data.get("providerId"), 120)
                if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                    return self.send_json({"error": "provider_not_found"}, 404)
                role = str(data.get("role", "provider_staff") or "provider_staff")
                if role not in {"provider_owner", "provider_manager", "provider_staff"}:
                    return self.send_json({"error": "invalid_provider_role"}, 400)
                name = safe_text(data.get("name"), 120)
                phone = normalize_phone(data.get("phone", ""))
                if not name or len(phone) < 11:
                    return self.send_json({"error": "name_and_valid_phone_required"}, 400)
                existing = con.execute(
                    "SELECT pin_hash,provider_id FROM provider_team_members WHERE id=?", (member_id,)
                ).fetchone()
                if existing and existing["provider_id"] != provider_id:
                    return self.send_json({"error": "team_member_provider_mismatch"}, 409)
                pin_hash = existing["pin_hash"] if existing else ""
                if data.get("pin"):
                    pin = str(data["pin"])
                    if not re.fullmatch(r"\d{4,10}", pin):
                        return self.send_json({"error": "invalid_pin"}, 400)
                    pin_hash = hash_pin(pin)
                if not pin_hash:
                    return self.send_json({"error": "pin_required"}, 400)
                selected_permissions = [
                    item for item in data.get("permissions", [])
                    if item in PROVIDER_ROLE_PERMISSIONS[role]
                ] if isinstance(data.get("permissions"), list) else []
                active = strict_bool(data.get("active"), True)
                con.execute(
                    """INSERT INTO provider_team_members(
                    id,provider_id,name,phone,role,pin_hash,permissions,active)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    provider_id=excluded.provider_id,name=excluded.name,phone=excluded.phone,
                    role=excluded.role,pin_hash=excluded.pin_hash,permissions=excluded.permissions,
                    active=excluded.active,updated_at=CURRENT_TIMESTAMP""",
                    (
                        member_id, provider_id, name, phone, role, pin_hash,
                        jdump(selected_permissions), int(active),
                    ),
                )
                log_audit(con, session, "team.upserted", member_id, role)
                return self.send_json({"ok": True, "id": member_id})
            if path == "/api/admin/branches":
                branch_id = safe_text(data.get("id"), 100) or slug("branch")
                if data.get("action") == "delete":
                    result = con.execute("UPDATE provider_branches SET active=0 WHERE id=?", (branch_id,))
                    if result.rowcount != 1:
                        return self.send_json({"error": "branch_not_found"}, 404)
                    log_audit(con, session, "branch.disabled", branch_id, "")
                    return self.send_json({"ok": True})
                provider_id = safe_text(data.get("providerId"), 120)
                if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                    return self.send_json({"error": "provider_not_found"}, 404)
                existing = con.execute(
                    "SELECT provider_id FROM provider_branches WHERE id=?", (branch_id,)
                ).fetchone()
                if existing and existing["provider_id"] != provider_id:
                    return self.send_json({"error": "branch_provider_mismatch"}, 409)
                name = safe_text(data.get("name"), 120)
                if not name:
                    return self.send_json({"error": "branch_name_required"}, 400)
                location = normalized_location(data.get("location"))
                active = strict_bool(data.get("active"), True)
                con.execute(
                    """INSERT INTO provider_branches(
                    id,provider_id,name,gov,wilayah,address,latitude,longitude,phone,active)
                    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    provider_id=excluded.provider_id,name=excluded.name,gov=excluded.gov,
                    wilayah=excluded.wilayah,address=excluded.address,latitude=excluded.latitude,
                    longitude=excluded.longitude,phone=excluded.phone,active=excluded.active,
                    updated_at=CURRENT_TIMESTAMP""",
                    (
                        branch_id, provider_id, name,
                        str(data.get("gov", "") or "")[:80], str(data.get("wilayah", "") or "")[:80],
                        str(data.get("address", "") or "")[:240], location.get("lat"), location.get("lng"),
                        normalize_phone(data.get("phone", "")), int(active),
                    ),
                )
                log_audit(con, session, "branch.upserted", branch_id, provider_id)
                return self.send_json({"ok": True, "id": branch_id})
            if path == "/api/admin/contact-consents":
                if data.get("action") != "revoke":
                    return self.send_json({"error": "invalid_consent_action"}, 400)
                request_id = str(data.get("requestId", "") or "")
                provider_id = str(data.get("providerId", "") or "")
                channel = str(data.get("channel", "") or "")
                row = con.execute(
                    "SELECT user_id FROM customer_requests WHERE id=?", (request_id,)
                ).fetchone()
                if not row:
                    return self.send_json({"error": "request_not_found"}, 404)
                try:
                    consent = ContactConsentService(con).set_channel(
                        request_id, row["user_id"], provider_id, channel, False
                    )
                except DomainError as err:
                    return self.send_domain_error(err)
                log_audit(con, session, "consent.revoked", request_id, f"{provider_id}:{channel}")
                return self.send_json({"ok": True, "consent": consent})
            if path == "/api/admin/settings":
                current_settings_row = con.execute(
                    "SELECT value FROM settings WHERE key='platform'"
                ).fetchone()
                settings_data = jload(current_settings_row["value"], {}) if current_settings_row else {}
                settings_data.update(dict(data))
                new_admin_code = str(settings_data.pop("adminCode", "") or "")
                settings_data.pop("passwords", None)
                settings_data.pop("otpCode", None)
                if new_admin_code:
                    if not re.fullmatch(r"\d{4,10}", new_admin_code):
                        return self.send_json({"error": "invalid_admin_code"}, 400)
                    con.execute(
                        "UPDATE admin_users SET code_hash=? WHERE id=?",
                        (hash_pin(new_admin_code), session["id"]),
                    )
                settings_data["nameAr"] = "خدماتي"
                settings_data["nameEn"] = "Khadamati App"
                settings_data["supportEmail"] = SUPPORT_EMAIL
                settings_data["policyVersion"] = POLICY_VERSION
                settings_data["currency"] = OMR
                con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(settings_data),))
                log_audit(con, session, "settings.updated", "platform", "")
                return self.send_json({"ok": True})
            if path == "/api/admin/ads":
                ad_id = str(data.get("id") or slug("ad"))
                existing = con.execute("SELECT * FROM advertisements WHERE id=?", (ad_id,)).fetchone()
                if data.get("action") == "delete":
                    if not existing:
                        return self.send_json({"error": "advertisement_not_found"}, 404)
                    con.execute(
                        "UPDATE advertisements SET active=0,deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (ad_id,),
                    )
                    create_notification(
                        con, "admin", "", "تمت أرشفة إعلان",
                        existing["advertiser"] or ad_id, type_="advertisement", related_id=ad_id,
                    )
                    return self.send_json({"ok": True, "archived": True})
                image_path = existing["image_path"] if existing else ""
                if data.get("imageData"):
                    image_path = save_upload_data(ad_id, data["imageData"], "banner", IMAGE_MIMES, 4_000_000)
                if not image_path:
                    return self.send_json({"error": "advertisement_image_required"}, 400)
                con.execute(
                    """INSERT INTO advertisements(
                    id,image_path,advertiser,phone,amount,title,body,starts_at,ends_at,active,deleted_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET image_path=excluded.image_path,
                    advertiser=excluded.advertiser,phone=excluded.phone,amount=excluded.amount,
                    title=excluded.title,body=excluded.body,starts_at=excluded.starts_at,
                    ends_at=excluded.ends_at,active=excluded.active,deleted_at=excluded.deleted_at,
                    updated_at=CURRENT_TIMESTAMP""",
                    (
                        ad_id, image_path, str(data.get("advertiser", "") or "")[:120],
                        normalize_phone(data.get("phone", "")),
                        finite_number(data.get("amount", 0), minimum=0, maximum=1_000_000_000),
                        str(data.get("title", "") or "")[:160],
                        str(data.get("body", "") or "")[:500],
                        str(data.get("startsAt", "") or "")[:40],
                        str(data.get("endsAt", "") or "")[:40],
                        int(bool(data.get("active", True))), "",
                    ),
                )
                create_notification(
                    con, "admin", "", "تم حفظ إعلان",
                    str(data.get("advertiser", "") or ad_id), type_="advertisement",
                    related_id=ad_id, action_text="فتح الإعلان",
                    action_route=f"admin:advertisement:{ad_id}",
                )
                saved = con.execute("SELECT * FROM advertisements WHERE id=?", (ad_id,)).fetchone()
                return self.send_json({"ok": True, "advertisement": row_advertisement(saved)})
            if path == "/api/admin/users":
                role = data.get("role", "support")
                if role not in {"super_admin", "admin", "manager", "support", "finance"}:
                    return self.send_json({"error": "invalid_admin_role"}, 400)
                perms = permissions_for(role, data.get("permissions"))
                user_id = safe_text(data.get("id"), 100) or slug("admin")
                name = safe_text(data.get("name", "مشرف"), 120)
                if not name:
                    return self.send_json({"error": "admin_name_required"}, 400)
                existing = con.execute("SELECT code_hash FROM admin_users WHERE id=?", (user_id,)).fetchone()
                code_hash = existing["code_hash"] if existing else ""
                if data.get("code"):
                    if not re.fullmatch(r"\d{4,10}", str(data["code"])):
                        return self.send_json({"error": "invalid_admin_code"}, 400)
                    code_hash = hash_pin(data["code"])
                if not code_hash:
                    return self.send_json({"error": "code_required"}, 400)
                con.execute(
                    """INSERT INTO admin_users(id,name,code_hash,role,permissions,active) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,code_hash=excluded.code_hash,
                    role=excluded.role,permissions=excluded.permissions,active=excluded.active""",
                    (user_id, name, code_hash, role, jdump(perms), int(strict_bool(data.get("active"), True))),
                )
                log_audit(con, session, "admin_user.upserted", user_id, role)
                return self.send_json({"ok": True})
            if path == "/api/admin/test-whatsapp":
                return self.send_json(send_whatsapp(data.get("to"), data.get("message", "اختبار من منصة خدماتي")))
        self.send_json({"error": "not_found"}, 404)


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    try:
        display_host = "127.0.0.1" if ipaddress.ip_address(host).is_unspecified else host
    except ValueError:
        display_host = host
    print(f"Khadamati platform running: http://{display_host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
