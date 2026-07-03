# Khadamati smoke tests

Run the server first:

```powershell
python server.py
```

API smoke test:

```powershell
python tests/smoke-api.py
```

Mobile user/provider flow test (requires Playwright and Chrome):

```powershell
node tests/smoke-ui.js
```

The UI flow covers offer comparison, text/image/voice chat, calendar export,
arrival tracking, provider video, and the before/after gallery. The API flow
also verifies waitlist matching and cross-account request collaboration.

Set `KHADAMATI_TEST_URL` to test another local or deployed URL.
