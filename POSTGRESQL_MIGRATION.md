# خطة نقل خدماتي إلى PostgreSQL

النسخة الحالية تعمل على SQLite لتسهيل التشغيل من اليوم. عند توسع الاستخدام، انقلها إلى PostgreSQL بهذه الخطة:

1. أنشئ قاعدة PostgreSQL في الخادم أو منصة الاستضافة.
2. نفذ الملف `postgres_schema.sql`.
3. صدّر البيانات من لوحة الإدارة عبر `نسخة احتياطية JSON`.
4. استورد الجداول بالترتيب:
   - `settings`
   - `admin_users`
   - `categories`
   - `services`
   - `providers`
   - `provider_requests`
   - `leads`
   - `finance`
   - `whatsapp_logs`
   - `reviews`
   - `complaints`
   - `packages`
   - `subscriptions`
   - `payments`
   - `audit_logs`
5. انقل مجلد الصور `public/uploads`.
6. حدّث طبقة الاتصال في `server.py` لاستخدام PostgreSQL driver مثل `psycopg`.

ملاحظات إنتاجية:

- احتفظ بـ SQLite كنسخة احتياطية قبل النقل.
- لا تنقل رموز الاختبار إلى الإنتاج.
- استخدم متغيرات بيئة للاتصال بقاعدة PostgreSQL بدل حفظ بيانات الاتصال داخل الكود.
- عند الوصول إلى استخدام فعلي متكرر، PostgreSQL أفضل من SQLite للتزامن، النسخ الاحتياطي، والمراقبة.
