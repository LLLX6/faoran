from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timedelta, UTC
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
UPLOAD_DIR = Path(os.environ.get("KHADAMATI_UPLOAD_DIR") or os.environ.get("FORAN_UPLOAD_DIR") or (PUBLIC_DIR / "uploads"))
_legacy_db = BASE_DIR / "foran.sqlite3"
DB_PATH = Path(os.environ.get("KHADAMATI_DB_PATH") or os.environ.get("FORAN_DB_PATH") or (_legacy_db if _legacy_db.exists() else BASE_DIR / "khadamati.sqlite3"))
ADMIN_CODE = os.environ.get("KHADAMATI_ADMIN_CODE") or os.environ.get("FORAN_ADMIN_CODE", "0000")
ADMIN_HASH = hashlib.sha256(ADMIN_CODE.encode("utf-8")).hexdigest()
TOKENS = {}

ALL_PERMISSIONS = [
    "view_reports",
    "manage_providers",
    "review_requests",
    "manage_quality",
    "manage_subscriptions",
    "manage_finance",
    "manage_settings",
    "manage_admins",
    "backup",
]
ROLE_PERMISSIONS = {
    "owner": ALL_PERMISSIONS,
    "manager": ["view_reports", "manage_providers", "review_requests", "manage_quality", "manage_subscriptions", "manage_finance", "backup"],
    "support": ["view_reports", "review_requests", "manage_quality"],
    "finance": ["view_reports", "manage_subscriptions", "manage_finance", "backup"],
}

IMAGE_MIMES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DOCUMENT_MIMES = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=12)
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


def iso_date(days=0):
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")


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
              pin_hash TEXT DEFAULT '', services TEXT NOT NULL, work_images TEXT DEFAULT '[]', documents TEXT DEFAULT '[]',
              quality_score INTEGER DEFAULT 60, response_score INTEGER DEFAULT 70, subscription_until TEXT DEFAULT '',
              subscription_start TEXT DEFAULT '', provider_type TEXT DEFAULT 'individual', company_name TEXT DEFAULT '', company_id TEXT DEFAULT '',
              commercial_no TEXT DEFAULT '', verification_expiry TEXT DEFAULT '', commercial_expiry TEXT DEFAULT '', license_expiry TEXT DEFAULT '',
              latitude REAL, longitude REAL, location_updated_at TEXT DEFAULT '',
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
              max_services INTEGER NOT NULL DEFAULT 3, max_images INTEGER NOT NULL DEFAULT 5, active INTEGER NOT NULL DEFAULT 1
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
            """
        )
        ensure_column(con, "providers", "image_path", "TEXT DEFAULT ''")
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
        ensure_column(con, "leads", "service_value", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "service_name", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "gov", "TEXT DEFAULT ''")
        ensure_column(con, "leads", "status", "TEXT DEFAULT 'open'")
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
                    hash_secret(default_provider_pin(p.get("phone", ""))),
                ),
            )
        for r in con.execute("SELECT id, phone FROM providers WHERE COALESCE(pin_hash,'')=''"):
            con.execute("UPDATE providers SET pin_hash=? WHERE id=?", (hash_secret(default_provider_pin(r["phone"])), r["id"]))
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
                "INSERT OR IGNORE INTO packages VALUES(?,?,?,?,?,?,?,?,?)",
                pkg,
            )
        con.execute("UPDATE packages SET max_services=5,max_images=15 WHERE id='company_year' AND max_services>5")
        if con.execute("SELECT COUNT(*) n FROM reviews").fetchone()["n"] == 0:
            con.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)", ("rev_seed_1", "p1", 5, "عميل موثق", "", "خدمة سريعة ومرتبة"))
            con.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)", ("rev_seed_2", "p2", 5, "عميلة", "", "التنظيف ممتاز والموعد واضح"))
        con.execute(
            "INSERT OR IGNORE INTO settings VALUES('platform', ?)",
            (jdump({
                "nameAr": "خدماتي",
                "nameEn": "Khadamati",
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
        if platform_settings.get("nameAr") in (None, "", "فوراً") or platform_settings.get("nameEn") in (None, "", "Fawran"):
            platform_settings["nameAr"] = "خدماتي"
            platform_settings["nameEn"] = "Khadamati"
            con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(platform_settings),))
        con.execute("INSERT OR IGNORE INTO settings VALUES('adminHash', ?)", (ADMIN_HASH,))
        if con.execute("SELECT COUNT(*) n FROM admin_users").fetchone()["n"] == 0:
            con.execute(
                "INSERT INTO admin_users VALUES(?,?,?,?,?,1,CURRENT_TIMESTAMP)",
                ("admin_owner", "المالك", ADMIN_HASH, "owner", jdump(ALL_PERMISSIONS)),
            )
        legacy_admin_hash = hash_secret("1995")
        con.execute(
            "UPDATE admin_users SET code_hash=? WHERE id='admin_owner' AND code_hash=?",
            (ADMIN_HASH, legacy_admin_hash),
        )


def image_url(path):
    return f"/{path.replace(os.sep, '/')}" if path else ""


def urls(paths):
    return [image_url(p) for p in paths if p]


def row_provider(r, private=False):
    d = dict(r)
    d["areas"] = jload(d["areas"], [])
    d["services"] = jload(d["services"], [])
    d["stats"] = jload(d["stats"], {"views": 0, "whatsapp": 0, "calls": 0})
    d["workImages"] = jload(d.pop("work_images", "[]"), [])
    d["workImageUrls"] = urls(d["workImages"])
    d["documents"] = jload(d.pop("documents", "[]"), [])
    for k in ("active", "verified", "featured"):
        d[k] = bool(d[k])
    d["packageId"] = d.pop("package_id", "")
    d["adminNote"] = d.pop("admin_note", "")
    d["imagePath"] = d.pop("image_path", "")
    d["imageUrl"] = image_url(d["imagePath"])
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
    d["location"] = (
        {"lat": latitude, "lng": longitude, "updatedAt": location_updated_at}
        if latitude is not None and longitude is not None
        else None
    )
    d["pinConfigured"] = bool(d.pop("pin_hash", ""))
    if not private:
        d.pop("adminNote", None)
        d.pop("documents", None)
    return d


def row_review(r):
    d = dict(r)
    d["approved"] = bool(d["approved"])
    return d


def row_complaint(r):
    return dict(r)


def row_package(r):
    d = dict(r)
    d["active"] = bool(d["active"])
    d["durationDays"] = d.pop("duration_days")
    d["featuredBoost"] = d.pop("featured_boost")
    d["maxServices"] = d.pop("max_services")
    d["maxImages"] = d.pop("max_images")
    return d


def row_subscription(r):
    d = dict(r)
    d["packageId"] = d.pop("package_id")
    d["providerId"] = d.pop("provider_id")
    d["startDate"] = d.pop("start_date")
    d["endDate"] = d.pop("end_date")
    return d


def row_payment(r):
    d = dict(r)
    d["providerId"] = d.pop("provider_id")
    d["subscriptionId"] = d.pop("subscription_id")
    return d


def row_audit(r):
    return dict(r)


def row_lead(r):
    return dict(r)


def lead_matches_provider(lead, provider):
    if lead.get("kind") != "request" or lead.get("status") in ("cancelled", "deleted", "closed"):
        return False
    service_value = (lead.get("service_value") or "").strip()
    service_tokens = set()
    if "|" in service_value:
        cat_id, service_id = service_value.split("|", 1)
        service_tokens.update([cat_id, service_id])
    service_tokens.update(x for x in [lead.get("service_name"), lead.get("note")] if x)
    provider_services = provider.get("services") or []
    service_ok = not service_value
    for svc in provider_services:
        if svc.get("catId") in service_tokens or svc.get("serviceId") in service_tokens:
            service_ok = True
            break
        if service_value and service_value == f"{svc.get('catId')}|{svc.get('serviceId')}":
            service_ok = True
            break
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
        is_admin = bool(session and session.get("kind") == "admin")
        is_provider = bool(session and session.get("kind") == "provider")
        providers = [row_provider(r, private=is_admin) for r in con.execute("SELECT * FROM providers ORDER BY featured DESC, quality_score DESC, rating DESC")]
        requests = []
        if has_permission(session, "review_requests"):
            for r in con.execute("SELECT * FROM provider_requests ORDER BY created_at DESC"):
                payload = jload(r["payload"], {}) | {"createdAt": r["created_at"]}
                payload["pending"] = True
                payload["active"] = False
                payload["status"] = payload.get("status", "unavailable")
                payload["services"] = payload.get("services", [])
                if not payload["services"] and "|" in payload.get("service", ""):
                    cat_id, service_id = payload["service"].split("|", 1)
                    payload["services"] = [{"id": f"pending-{payload.get('id','')}", "catId": cat_id, "serviceId": service_id, "priceFrom": payload.get("priceFrom", 0), "active": True, "areas": [payload.get("wilayah", "")]}]
                payload.pop("pinHash", None)
                requests.append(payload)
        settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
        packages = [row_package(r) for r in con.execute("SELECT * FROM packages ORDER BY price")]
        if is_admin:
            reviews = [row_review(r) for r in con.execute("SELECT * FROM reviews ORDER BY created_at DESC")]
            complaints = [row_complaint(r) for r in con.execute("SELECT * FROM complaints ORDER BY created_at DESC")]
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
        payment_revenue = con.execute("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind='revenue' AND status='paid'").fetchone()["n"]
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
            "settings": settings,
            "stats": stats,
            "reports": reports,
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
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_owner = "".join(ch for ch in str(owner_id) if ch.isalnum() or ch in ("_", "-"))[:60] or "file"
    safe_slot = "".join(ch for ch in str(slot) if ch.isalnum() or ch in ("_", "-"))[:40] or secrets.token_hex(4)
    filename = f"{safe_owner}-{safe_slot}.{ext}"
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
    work_images = data.get("workImages") or existing_provider.get("workImages", [])
    if data.get("workImagesData"):
        work_images = save_many_images(p["id"], data.get("workImagesData"), "work", 15 if data.get("providerType") == "company" else 5)
    documents = data.get("documents") or existing_provider.get("documents", [])
    if data.get("documentsData"):
        documents = save_many_documents(p["id"], data.get("documentsData"), "doc", 3)
    location = data.get("location") or existing_provider.get("location") or {}
    con.execute(
        """INSERT INTO providers(id,name,phone,gov,wilayah,areas,bio,hours,status,active,verified,featured,
        package_id,rating,reviews,admin_note,image_path,pin_hash,services,work_images,documents,quality_score,response_score,
        subscription_until,subscription_start,provider_type,company_name,company_id,commercial_no,
        verification_expiry,commercial_expiry,license_expiry,latitude,longitude,location_updated_at,stats)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,phone=excluded.phone,gov=excluded.gov,
        wilayah=excluded.wilayah,areas=excluded.areas,bio=excluded.bio,hours=excluded.hours,status=excluded.status,
        active=excluded.active,verified=excluded.verified,featured=excluded.featured,package_id=excluded.package_id,
        rating=excluded.rating,reviews=excluded.reviews,admin_note=excluded.admin_note,image_path=excluded.image_path,
        pin_hash=excluded.pin_hash,services=excluded.services,work_images=excluded.work_images,documents=excluded.documents,
        quality_score=excluded.quality_score,response_score=excluded.response_score,subscription_until=excluded.subscription_until,
        subscription_start=excluded.subscription_start,provider_type=excluded.provider_type,
        company_name=excluded.company_name,company_id=excluded.company_id,commercial_no=excluded.commercial_no,
        verification_expiry=excluded.verification_expiry,commercial_expiry=excluded.commercial_expiry,
        license_expiry=excluded.license_expiry,latitude=excluded.latitude,longitude=excluded.longitude,
        location_updated_at=excluded.location_updated_at""",
        (
            p["id"], p.get("name", ""), p.get("phone", ""), p.get("gov", ""), p.get("wilayah", ""),
            jdump(p.get("areas", [])), p.get("bio", ""), p.get("hours", ""), p.get("status", "available"),
            int(bool(p.get("active", True))), int(bool(p.get("verified", False))), int(bool(p.get("featured", False))),
            p.get("packageId", existing_provider.get("packageId", "intro")),
            float(p.get("rating", existing_provider.get("rating", 0)) or 0),
            int(p.get("reviews", existing_provider.get("reviews", 0)) or 0),
            p.get("adminNote", ""), image_path, pin_hash, jdump(p.get("services", [])), jdump(work_images), jdump(documents),
            int(p.get("qualityScore", existing_provider.get("qualityScore", 60)) or 60),
            int(p.get("responseScore", existing_provider.get("responseScore", 70)) or 70),
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
            jdump(p.get("stats", existing_provider.get("stats", {"views": 0, "whatsapp": 0, "calls": 0}))),
        ),
    )
    p["imagePath"] = image_path
    p["workImages"] = work_images
    p["documents"] = documents
    recompute_provider_quality(con, p["id"])
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
            req_id = slug("req")
            item = {
                "id": req_id,
                "name": data.get("name", "").strip(),
                "phone": data.get("phone", "").strip(),
                "providerType": data.get("providerType", "individual") if data.get("providerType") in ("individual", "company") else "individual",
                "companyName": data.get("companyName", "").strip(),
                "commercialNo": data.get("commercialNo", "").strip(),
                "companySize": data.get("companySize", "").strip(),
                "businessRole": data.get("businessRole", "").strip(),
                "gov": data.get("gov", "مسقط"),
                "wilayah": data.get("wilayah", ""),
                "location": data.get("location"),
                "service": data.get("service", ""),
                "priceFrom": data.get("priceFrom", 0),
                "note": data.get("note", ""),
                "hours": data.get("hours", ""),
                "imagePath": "",
                "workImages": [],
                "documents": [],
                "pinHash": hash_secret(pin) if len(pin) >= 4 else "",
            }
            if not item["name"] or not item["phone"] or not item["pinHash"]:
                return self.send_json({"error": "name_phone_pin_required"}, 400)
            if item["providerType"] == "company" and not item["companyName"]:
                return self.send_json({"error": "company_name_required"}, 400)
            if not item["commercialNo"]:
                return self.send_json({"error": "commercial_number_required"}, 400)
            note_words = len(str(item["note"]).split())
            if note_words < 3 or note_words > 20:
                return self.send_json({"error": "description_word_limit"}, 400)
            if not item["service"] or "|" not in item["service"]:
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
            send_whatsapp(settings.get("adminWhatsapp"), f"طلب مزود جديد في خدماتي: {item['name']} - {item['phone']} - {item['service']}")
            safe_item = item.copy()
            safe_item.pop("pinHash", None)
            return self.send_json({"ok": True, "request": safe_item}, 201)
        if path == "/api/reviews":
            return self.save_review(data)
        if path == "/api/complaints":
            return self.save_complaint(data)
        if path == "/api/leads":
            return self.save_lead(data)
        if path.startswith("/api/provider/"):
            return self.provider_post(path, data)
        if path.startswith("/api/admin/"):
            return self.admin_post(path, data)
        self.send_json({"error": "not_found"}, 404)

    def save_review(self, data):
        provider_id = data.get("providerId")
        rating = int(data.get("rating", 0) or 0)
        if not provider_id or rating < 1 or rating > 5:
            return self.send_json({"error": "invalid_review"}, 400)
        item = {
            "id": slug("rev"),
            "provider_id": provider_id,
            "rating": rating,
            "customer_name": data.get("customerName", "").strip()[:80],
            "phone": data.get("phone", "").strip()[:30],
            "comment": data.get("comment", "").strip()[:900],
        }
        with db() as con:
            if not con.execute("SELECT id FROM providers WHERE id=?", (provider_id,)).fetchone():
                return self.send_json({"error": "provider_not_found"}, 404)
            con.execute(
                "INSERT INTO reviews VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)",
                (item["id"], item["provider_id"], item["rating"], item["customer_name"], item["phone"], item["comment"]),
            )
            recompute_provider_quality(con, provider_id)
            log_audit(con, {"kind": "customer", "id": item["phone"] or "anonymous"}, "review.created", provider_id, item["comment"])
        return self.send_json({"ok": True, "review": item}, 201)

    def save_complaint(self, data):
        provider_id = data.get("providerId")
        item = {
            "id": slug("cmp"),
            "provider_id": provider_id,
            "customer_name": data.get("customerName", "").strip()[:80],
            "phone": data.get("phone", "").strip()[:30],
            "reason": data.get("reason", "quality").strip()[:80],
            "detail": data.get("detail", "").strip()[:1400],
            "priority": data.get("priority", "normal") if data.get("priority") in ("low", "normal", "high") else "normal",
        }
        if not item["customer_name"] or not item["phone"] or not item["detail"]:
            return self.send_json({"error": "complaint_required_fields"}, 400)
        with db() as con:
            con.execute(
                "INSERT INTO complaints(id,provider_id,customer_name,phone,reason,detail,status,priority,resolution) VALUES(?,?,?,?,?,?,?,?,?)",
                (item["id"], item["provider_id"], item["customer_name"], item["phone"], item["reason"], item["detail"], "open", item["priority"], ""),
            )
            if provider_id:
                recompute_provider_quality(con, provider_id)
            log_audit(con, {"kind": "customer", "id": item["phone"]}, "complaint.created", provider_id or "", item["reason"])
            settings = jload(con.execute("SELECT value FROM settings WHERE key='platform'").fetchone()["value"], {})
        send_whatsapp(settings.get("adminWhatsapp"), f"شكوى جديدة في خدماتي: {item['customer_name']} - {item['phone']} - {item['reason']}")
        return self.send_json({"ok": True, "complaint": item}, 201)

    def save_lead(self, data):
        kind = data.get("kind", "whatsapp")
        if kind not in ("request", "views", "whatsapp", "calls", "booking", "quote"):
            kind = "request"
        lead_id = (data.get("id") or slug("lead")).strip()[:80]
        item = {
            "id": lead_id,
            "provider_id": (data.get("providerId") or "")[:80],
            "kind": kind,
            "customer_name": (data.get("customerName", "") or "").strip()[:80],
            "phone": (data.get("phone", "") or "").strip()[:30],
            "note": (data.get("note", "") or "").strip()[:1200],
            "service_value": (data.get("serviceValue", "") or "").strip()[:120],
            "service_name": (data.get("serviceName", "") or "").strip()[:120],
            "gov": (data.get("gov", "") or "").strip()[:80],
            "status": (data.get("status", "open") or "open").strip()[:40],
        }
        with db() as con:
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
        if data.get("notifyProvider") and provider:
            send_whatsapp(provider["phone"], f"تنبيه من خدماتي: لديك تواصل جديد. {item['note']}".strip())
        return self.send_json({"ok": True, "lead": item}, 200 if data.get("id") else 201)

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
                    "commercialNo": data.get("commercialNo", provider.get("commercialNo", "")),
                    "verificationExpiry": data.get("verificationExpiry", provider.get("verificationExpiry", "")),
                    "commercialExpiry": data.get("commercialExpiry", provider.get("commercialExpiry", "")),
                    "licenseExpiry": data.get("licenseExpiry", provider.get("licenseExpiry", "")),
                    "gov": data.get("gov", provider["gov"]),
                    "wilayah": data.get("wilayah", provider["wilayah"]),
                    "location": data.get("location", provider.get("location")),
                    "areas": data.get("areas", provider["areas"]),
                    "bio": data.get("bio", provider["bio"]),
                    "hours": data.get("hours", provider["hours"]),
                    "status": data.get("status", provider["status"]),
                    "services": data.get("services", provider["services"]),
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
                upsert_provider(con, provider)
                log_audit(con, session, "provider.profile.updated", provider["id"], provider["name"])
                return self.send_json({"ok": True})
            if path == "/api/provider/image":
                image_path = save_data_url(provider["id"], data.get("imageData", ""))
                con.execute("UPDATE providers SET image_path=? WHERE id=?", (image_path, provider["id"]))
                recompute_provider_quality(con, provider["id"])
                return self.send_json({"ok": True, "imageUrl": image_url(image_path)})
            if path == "/api/provider/work-images":
                images = save_many_images(provider["id"], data.get("workImagesData", []), "work", 15 if provider.get("providerType") == "company" else 5)
                if images:
                    con.execute("UPDATE providers SET work_images=? WHERE id=?", (jdump(images), provider["id"]))
                    recompute_provider_quality(con, provider["id"])
                return self.send_json({"ok": True, "workImageUrls": urls(images)})
            if path == "/api/provider/documents":
                docs = save_many_documents(provider["id"], data.get("documentsData", []), "doc", 3)
                if docs:
                    con.execute("UPDATE providers SET documents=? WHERE id=?", (jdump(docs), provider["id"]))
                return self.send_json({"ok": True, "documents": docs})
            if path == "/api/provider/pin":
                if len(str(data.get("pin", ""))) < 4:
                    return self.send_json({"error": "pin_too_short"}, 400)
                con.execute("UPDATE providers SET pin_hash=? WHERE id=?", (hash_secret(data["pin"]), provider["id"]))
                return self.send_json({"ok": True})
            if path == "/api/provider/subscription-request":
                package_id = data.get("packageId", "basic")
                pkg = con.execute("SELECT * FROM packages WHERE id=? AND active=1", (package_id,)).fetchone()
                if not pkg:
                    return self.send_json({"error": "package_not_found"}, 404)
                sub_id = slug("sub")
                con.execute(
                    "INSERT INTO subscriptions(id,provider_id,package_id,amount,status,start_date,end_date,note) VALUES(?,?,?,?,?,?,?,?)",
                    (sub_id, provider["id"], package_id, pkg["price"], "pending", "", "", data.get("note", "")),
                )
                log_audit(con, session, "subscription.requested", provider["id"], package_id)
                return self.send_json({"ok": True, "subscriptionId": sub_id})
        return self.send_json({"error": "not_found"}, 404)

    def admin_post(self, path, data):
        permission = {
            "/api/admin/providers": "manage_providers",
            "/api/admin/provider-status": "manage_providers",
            "/api/admin/request-decision": "review_requests",
            "/api/admin/review-status": "manage_quality",
            "/api/admin/complaint-status": "manage_quality",
            "/api/admin/packages": "manage_subscriptions",
            "/api/admin/subscriptions": "manage_subscriptions",
            "/api/admin/payments": "manage_finance",
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
                log_audit(con, session, "provider.upserted", p["id"], p.get("name", ""))
                return self.send_json({"ok": True, "provider": p})
            if path == "/api/admin/provider-status":
                con.execute(
                    "UPDATE providers SET active=?, verified=?, featured=?, status=? WHERE id=?",
                    (int(data.get("active", 1)), int(data.get("verified", 0)), int(data.get("featured", 0)), data.get("status", "available"), data.get("id")),
                )
                recompute_provider_quality(con, data.get("id"))
                log_audit(con, session, "provider.status.updated", data.get("id", ""), data.get("status", ""))
                return self.send_json({"ok": True})
            if path == "/api/admin/request-decision":
                row = con.execute("SELECT payload FROM provider_requests WHERE id=?", (data.get("id"),)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                payload = jload(row["payload"], {})
                if data.get("decision") == "accept":
                    note_words = len(str(payload.get("note", "")).split())
                    if not payload.get("commercialNo"):
                        return self.send_json({"error": "commercial_number_required"}, 400)
                    if note_words < 3 or note_words > 20:
                        return self.send_json({"error": "description_word_limit"}, 400)
                    if not payload.get("documents"):
                        return self.send_json({"error": "documents_required"}, 400)
                con.execute("DELETE FROM provider_requests WHERE id=?", (data.get("id"),))
                if data.get("decision") == "accept":
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
                        "bio": payload.get("note", ""),
                        "hours": payload.get("hours", ""),
                        "status": "available",
                        "active": True,
                        "verified": False,
                        "featured": False,
                        "packageId": "company_year" if payload.get("providerType") == "company" else "intro",
                        "rating": 0,
                        "reviews": 0,
                        "imagePath": payload.get("imagePath", ""),
                        "workImages": payload.get("workImages", []),
                        "documents": payload.get("documents", []),
                        "services": [],
                        "stats": {"views": 0, "whatsapp": 0, "calls": 0},
                        "adminNote": "تم قبوله من الطلبات" + (f" | سجل: {payload.get('commercialNo', '')} | فريق: {payload.get('companySize', '')}" if payload.get("providerType") == "company" else f" | مهنة: {payload.get('businessRole', '')}"),
                        "pinHash": payload.get("pinHash") or hash_secret(default_provider_pin(payload.get("phone", ""))),
                    }
                    service = payload.get("service", "")
                    if "|" in service:
                        cat_id, service_id = service.split("|", 1)
                        provider["services"] = [{"id": slug("ps"), "catId": cat_id, "serviceId": service_id, "priceFrom": float(payload.get("priceFrom") or 0), "active": True, "areas": [payload.get("wilayah", "")]}]
                    upsert_provider(con, provider)
                    log_audit(con, session, "provider.request.accepted", provider["id"], provider["name"])
                    send_whatsapp(provider["phone"], "تم قبول حسابك كمزود في خدماتي. يمكنك الدخول من بوابة المزودين.")
                else:
                    log_audit(con, session, "provider.request.rejected", data.get("id", ""), payload.get("name", ""))
                return self.send_json({"ok": True})
            if path == "/api/admin/review-status":
                con.execute("UPDATE reviews SET approved=? WHERE id=?", (int(bool(data.get("approved", True))), data.get("id")))
                row = con.execute("SELECT provider_id FROM reviews WHERE id=?", (data.get("id"),)).fetchone()
                if row:
                    recompute_provider_quality(con, row["provider_id"])
                log_audit(con, session, "review.status.updated", data.get("id", ""), str(data.get("approved", True)))
                return self.send_json({"ok": True})
            if path == "/api/admin/complaint-status":
                complaint_id = data.get("id")
                row = con.execute("SELECT provider_id FROM complaints WHERE id=?", (complaint_id,)).fetchone()
                if not row:
                    return self.send_json({"error": "not_found"}, 404)
                status = data.get("status", "open")
                priority = data.get("priority", "normal")
                if status not in ("open", "reviewing", "closed"):
                    status = "open"
                if priority not in ("low", "normal", "high"):
                    priority = "normal"
                con.execute(
                    "UPDATE complaints SET status=?, priority=?, resolution=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (status, priority, data.get("resolution", ""), complaint_id),
                )
                if row["provider_id"]:
                    recompute_provider_quality(con, row["provider_id"])
                log_audit(con, session, "complaint.status.updated", complaint_id, status)
                return self.send_json({"ok": True})
            if path == "/api/admin/packages":
                package_id = data.get("id") or slug("pkg")
                con.execute(
                    """INSERT INTO packages(id,ar,en,price,duration_days,featured_boost,max_services,max_images,active)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET ar=excluded.ar,en=excluded.en,price=excluded.price,
                    duration_days=excluded.duration_days,featured_boost=excluded.featured_boost,
                    max_services=excluded.max_services,max_images=excluded.max_images,active=excluded.active""",
                    (
                        package_id,
                        data.get("ar", "باقة"),
                        data.get("en", "Package"),
                        float(data.get("price", 0) or 0),
                        int(data.get("durationDays", 30) or 30),
                        int(data.get("featuredBoost", 0) or 0),
                        int(data.get("maxServices", 3) or 3),
                        int(data.get("maxImages", 5) or 5),
                        int(bool(data.get("active", True))),
                    ),
                )
                log_audit(con, session, "package.upserted", package_id, data.get("ar", ""))
                return self.send_json({"ok": True, "id": package_id})
            if path == "/api/admin/subscriptions":
                sub_id = data.get("id") or slug("sub")
                provider_id = data.get("providerId")
                package_id = data.get("packageId", "basic")
                pkg = con.execute("SELECT * FROM packages WHERE id=?", (package_id,)).fetchone()
                if not provider_id or not pkg:
                    return self.send_json({"error": "provider_or_package_missing"}, 400)
                status = data.get("status", "active")
                start_date = data.get("startDate") or iso_date()
                end_date = data.get("endDate") or iso_date(int(pkg["duration_days"] or 30))
                amount = float(data.get("amount", pkg["price"]) or 0)
                con.execute(
                    """INSERT INTO subscriptions(id,provider_id,package_id,amount,status,start_date,end_date,note)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET provider_id=excluded.provider_id,package_id=excluded.package_id,
                    amount=excluded.amount,status=excluded.status,start_date=excluded.start_date,end_date=excluded.end_date,note=excluded.note""",
                    (sub_id, provider_id, package_id, amount, status, start_date, end_date, data.get("note", "")),
                )
                if status == "active":
                    con.execute(
                        "UPDATE providers SET package_id=?, featured=?, subscription_start=?, subscription_until=? WHERE id=?",
                        (package_id, 1 if int(pkg["featured_boost"] or 0) > 0 else 0, start_date, end_date, provider_id),
                    )
                log_audit(con, session, "subscription.upserted", sub_id, f"{provider_id}:{package_id}:{status}")
                return self.send_json({"ok": True, "id": sub_id})
            if path == "/api/admin/payments":
                payment_id = data.get("id") or slug("pay")
                con.execute(
                    """INSERT INTO payments(id,provider_id,subscription_id,kind,amount,method,status,note)
                    VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET provider_id=excluded.provider_id,subscription_id=excluded.subscription_id,
                    kind=excluded.kind,amount=excluded.amount,method=excluded.method,status=excluded.status,note=excluded.note""",
                    (
                        payment_id,
                        data.get("providerId", ""),
                        data.get("subscriptionId", ""),
                        data.get("kind", "revenue"),
                        float(data.get("amount", 0) or 0),
                        data.get("method", "manual"),
                        data.get("status", "paid"),
                        data.get("note", ""),
                    ),
                )
                log_audit(con, session, "payment.upserted", payment_id, str(data.get("amount", 0)))
                return self.send_json({"ok": True, "id": payment_id})
            if path == "/api/admin/settings":
                con.execute("UPDATE settings SET value=? WHERE key='platform'", (jdump(data),))
                log_audit(con, session, "settings.updated", "platform", "")
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
                return self.send_json(send_whatsapp(data.get("to"), data.get("message", "اختبار من منصة خدماتي")))
        self.send_json({"error": "not_found"}, 404)


if __name__ == "__main__":
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"Khadamati platform running: http://{display_host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
