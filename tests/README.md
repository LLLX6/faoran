# Khadamati smoke tests

Compile and run the domain tests first:

```powershell
python -m py_compile server.py khadamati_domain.py
python -m unittest discover -s tests -p "test_*.py" -v
python tests/security-api.py
```

API smoke test (run against an isolated server and set its admin code):

```powershell
python tests/smoke-api.py
```

Mobile user/provider flow test (requires Playwright and Chrome). When no URL is
provided, this test starts its own static server and blocks stale service workers:

```powershell
node tests/smoke-ui.js
```

Mobile launch performance check:

```powershell
node tests/performance-ui.js
```

The UI flow covers Arabic RTL and English LTR, modal/map state restoration, live
geolocation recentering, 320px mobile fit, offer comparison,
consent-gated contact, text/image/voice chat, calendar export, arrival tracking,
provider video, before/after media, subscriptions, finance, and visitor isolation.
The API/domain flows also verify map privacy, approved-provider availability,
subscription service/category limits, registration, exact matching, active request visibility,
the public request marketplace, provider recommendations and abuse controls,
subscriptions, contact consent, reviews, and cross-account collaboration.
The security flow starts an isolated database and verifies public-data
minimization, CORS, private signed media, cross-provider IDOR protection, owner
PIN protection, MIME signatures, session revocation, lockout, inactive-account
revocation, request-size limits, and private-cache blocking.

Set `KHADAMATI_TEST_URL` to test another local or deployed URL.
