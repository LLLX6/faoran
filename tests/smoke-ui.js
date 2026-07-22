const { chromium } = require('playwright');
const fs = require('fs');
const http = require('http');
const path = require('path');

const BASE_URL = process.env.KHADAMATI_TEST_URL || '';
const CHROME_PATH = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const SCREENSHOT_DIR = process.env.KHADAMATI_SCREENSHOT_DIR || '';
const VIEWPORT_WIDTH = Number(process.env.KHADAMATI_VIEWPORT_WIDTH || 390);
const VIEWPORT_HEIGHT = Number(process.env.KHADAMATI_VIEWPORT_HEIGHT || 844);
const IS_MOBILE = VIEWPORT_WIDTH <= 760;
let LOCAL_SERVER = null;

async function startStaticServer() {
  const root = path.resolve(__dirname, '..');
  const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.ico': 'image/x-icon' };
  LOCAL_SERVER = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
      const target = path.resolve(root, relative);
      if (target !== root && !target.startsWith(`${root}${path.sep}`)) {
        response.writeHead(403).end();
        return;
      }
      const data = await fs.promises.readFile(target);
      response.writeHead(200, { 'content-type': mime[path.extname(target).toLowerCase()] || 'application/octet-stream', 'cache-control': 'no-store' });
      response.end(data);
    } catch (_) {
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Not found');
    }
  });
  await new Promise((resolve, reject) => {
    LOCAL_SERVER.once('error', reject);
    LOCAL_SERVER.listen(0, '127.0.0.1', resolve);
  });
  return `http://127.0.0.1:${LOCAL_SERVER.address().port}/`;
}

function assert(value, message) {
  if (!value) throw new Error(message);
}

async function capture(page, name) {
  if (!SCREENSHOT_DIR) return;
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${name}.png`), fullPage: true });
}

async function clickUserNav(page, view) {
  const item = page.locator(`.bottom-nav [data-action="nav"][data-view="${view}"]`).first();
  if (await item.isVisible()) await item.click();
  else await item.evaluate(element => element.click());
}

async function clickFirstAction(page, action) {
  const item = page.locator(`[data-action="${action}"]`).first();
  if (await item.isVisible()) await item.click();
  else await item.evaluate(element => element.click());
}

(async () => {
  const testUrl = BASE_URL || await startStaticServer();
  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROME_PATH,
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
  });
  const context = await browser.newContext({
    viewport: { width: VIEWPORT_WIDTH, height: VIEWPORT_HEIGHT },
    deviceScaleFactor: 2,
    isMobile: IS_MOBILE,
    hasTouch: IS_MOBILE,
    serviceWorkers: 'block',
    locale: 'ar-OM',
    permissions: ['geolocation', 'microphone', 'notifications'],
    geolocation: { latitude: 23.61, longitude: 58.24 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.stack || error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !/favicon|tile|Failed to load resource/.test(message.text())) {
      errors.push(message.text());
    }
  });

  // Keep the visual smoke test deterministic while still exercising authenticated UI paths.
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/users/login') {
      return json({ token: 'ui-user-token', user: { id: 'ui-user', phone: '96895550001', name: 'مستخدم الاختبار الآلي', gov: 'مسقط', wilayah: 'السيب', pinConfigured: true } });
    }
    if (url.pathname === '/api/provider/login') {
      return json({ token: 'ui-provider-token', provider: { id: 'p1', name: 'سالم البلوشي', phone: '96891234567', gov: 'مسقط', wilayah: 'السيب', areas: ['السيب'], bio: 'كهربائي منازل بخبرة وعناية', hours: 'الأحد - الخميس: 8:00 ص - 8:00 م', status: 'available', active: true, verified: true, featured: true, mapVisible: true, location: { lat: 23.61, lng: 58.24, updatedAt: '2026-07-18T08:00:00Z' }, packageId: 'professional_12m', subscriptionState: 'active', services: [{ id: 'p1s1', catId: 'homecare', serviceId: 'electrician', priceFrom: 8, active: true, areas: ['السيب'] }], workImages: [], documents: [], rating: 4.9, reviews: 12, qualityScore: 94, pinConfigured: true } });
    }
    if (url.pathname === '/api/provider/profile') return json({});
    if (url.pathname === '/api/admin/login') return json({ token: 'ui-admin-token', user: { id: 'ui-admin', name: 'إدارة خدماتي', role: 'super_admin' } });
    if (url.pathname === '/api/bootstrap' || url.pathname === '/api/admin/session') return json({});
    if (url.pathname === '/api/push/public-key') return json({ publicKey: '' });
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'request_failed' }) });
  });
  await page.goto(testUrl, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.clear());
  try {
    await page.reload({ waitUntil: 'domcontentloaded' });
  } catch (error) {
    if (!/ERR_ABORTED|frame was detached/i.test(String(error))) throw error;
    await page.waitForLoadState('domcontentloaded');
  }
  await page.waitForSelector('[data-action="openUserLogin"]');
  await capture(page, '00-entry');

  await page.locator('[data-action="openUserLogin"]').click();
  await page.locator('#customerLoginPhone').click();
  assert(await page.locator('#customerLoginPhone').isVisible(), 'Clicking inside the sign-in sheet closed it unexpectedly.');
  await page.locator('#customerLoginPhone').fill('95550001');
  await page.locator('#customerLoginName').fill('مستخدم الاختبار الآلي');
  await page.locator('[data-action="customerLogin"]').click();
  assert(await page.locator('#customerLoginPin').isVisible(), 'Submitting without a PIN must keep the sign-in sheet open.');
  await page.locator('#customerLoginPin').fill('2468');
  await page.locator('[data-action="customerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  const onboardingImage = page.locator('.role-onboarding .onboarding-visual img');
  assert(/assets\/onboarding\/v49\//.test(await onboardingImage.getAttribute('src')), 'The square role onboarding image is missing.');
  await onboardingImage.evaluate(image => image.complete ? true : new Promise(resolve => image.addEventListener('load', () => resolve(true), { once: true })));
  assert(await onboardingImage.evaluate(image => image.naturalWidth >= 900 && image.naturalHeight >= 900 && Math.abs(image.naturalWidth - image.naturalHeight) <= 2), 'The onboarding image is not a high-resolution square launch asset.');
  assert(await onboardingImage.evaluate(image => getComputedStyle(image).objectFit === 'cover'), 'Mobile onboarding artwork must fill the square frame without side gaps.');
  const onboardingSets = [
    { role: 'user', slides: ['user-service', 'user-direct-request', 'user-matching', 'user-track'] },
    { role: 'guest', slides: ['guest-browse', 'guest-compare', 'guest-signin', 'guest-privacy'] },
    { role: 'provider', slides: ['provider-profile', 'provider-opportunity', 'provider-availability', 'provider-offer'] },
    { role: 'company', slides: ['company-profile', 'company-dispatch', 'company-analytics', 'company-team'] },
  ].map(set => ({ ...set, slides: set.slides.map(name => `assets/onboarding/v49/${name}.webp`) }));
  for (const set of onboardingSets) {
    assert(set.slides.length === 4, `${set.role} onboarding must contain four focused steps.`);
    assert(set.slides.every(src => /assets\/onboarding\/v49\//.test(src)), `${set.role} onboarding is using an outdated image.`);
    assert(new Set(set.slides).size === set.slides.length, `${set.role} onboarding repeats the same artwork.`);
  }
  assert(new Set(onboardingSets.flatMap(set => set.slides)).size === 16, 'Every onboarding state must use its own artwork.');
  const launchSources = [
    ...new Set(onboardingSets.flatMap(set => set.slides)),
    'assets/ads/v45/home-services.webp',
    'assets/ads/v45/nearby-services.webp',
    'assets/ads/v45/business-services.webp',
  ];
  const launchHtml = await page.content();
  assert(launchSources.every(src => launchHtml.includes(src)), 'The production page is not wired to every current launch image.');
  const launchImages = await page.evaluate(async sources => {
    return Promise.all(sources.map(async src => ({ src, ok: (await fetch(src, { cache: 'no-store' })).ok })));
  }, launchSources);
  assert(launchImages.every(item => item.ok), `A launch image failed to load: ${launchImages.filter(item => !item.ok).map(item => item.src).join(', ')}`);
  await capture(page, '00b-user-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();
  assert(await page.locator('#toastRoot .toast').count() === 0, 'A validation toast remained visible after successful sign-in.');
  const persistedUserAuth = await page.evaluate(() => JSON.parse(localStorage.getItem('KHADAMATI_AUTH_V3') || '{}'));
  assert(persistedUserAuth.userToken === 'ui-user-token', 'User authentication was not persisted for the next app launch.');

  assert((await page.locator('.clean-grid .category-tile').count()) <= 6, 'Home must show no more than six categories.');
  assert(await page.locator('main.view > .home-ad.ad-slider').count(), 'Advertisement slider must be the first home block.');
  assert((await page.locator('.popular-rail').count()) === 0, 'Popular services rail should be removed from home.');
  assert((await page.locator('.offline-sync-card').count()) === 0, 'Offline queue banner should not crowd the home page.');
  assert(await page.locator('.direct-request-card').count(), 'Direct request card is missing.');
  assert((await page.locator('.global-search').count()) === 0, 'Duplicated global search should be removed.');
  assert((await page.locator('main[data-view="home"] .provider-listing').count()) === 0, 'Home should not contain provider recommendation cards.');
  await capture(page, '01-user-home');

  await clickUserNav(page, 'search');
  assert(await page.locator('.search-map-banner').count(), 'Search from map banner is missing.');
  assert(await page.locator('.app-back:visible').count() === 1, 'Search should show only the global back button.');
  assert((await page.locator('.search-filter-panel').count()) === 0, 'Advanced filters should start collapsed.');
  await page.locator('[data-action="searchCategory"]').first().click();
  assert(await page.locator('.service-choice-grid').count(), 'Service stage did not open after choosing a category.');
  const serviceOverflow = await page.locator('.service-choice-grid').evaluate(element => getComputedStyle(element).overflowX);
  assert(['auto', 'scroll'].includes(serviceOverflow), 'Exact services should scroll horizontally.');
  const searchColumns = await page.locator('.search-results-grid').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length);
  assert(searchColumns === (IS_MOBILE ? 2 : 3), 'Search results grid does not match the active viewport.');
  assert(await page.locator('.search-results-grid [data-action="directWhatsapp"]').count() === 0, 'Public provider cards must not expose direct WhatsApp.');
  const providerControlsFit = await page.locator('.search-results-grid .provider-listing').evaluateAll(cards => cards.every(card => {
    const bounds = card.getBoundingClientRect();
    return [...card.querySelectorAll('.provider-card-title-row .status,[data-action="providerDetails"]')].every(control => {
      const rect = control.getBoundingClientRect();
      return rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1;
    });
  }));
  assert(providerControlsFit, 'Provider status or details button overflows its card.');
  assert(await page.locator('.search-results-grid .provider-card-title-row .status.off').count() === 0, 'Unavailable providers must stay hidden from public search.');
  const firstProviderImage = page.locator('.search-results-grid .provider-listing .listing-media img').first();
  assert(/assets\/providers\/omani-electrician-v53\.webp/.test(await firstProviderImage.getAttribute('src')), 'The launch provider card is still using a generated placeholder.');
  assert(await firstProviderImage.evaluate(image => image.complete && image.naturalWidth >= 800), 'The launch provider image did not load at production quality.');
  await capture(page, '01b-progressive-search');
  await clickUserNav(page, 'home');

  await clickFirstAction(page, 'openRequestBoard');
  assert(await page.locator('.request-board-sheet').count(), 'Request board did not open.');
  assert(await page.locator('.request-board-guide').count(), 'Request board guidance is missing.');
  assert(await page.locator('.request-board-guide').evaluate(element => element.getBoundingClientRect().right <= window.innerWidth + 1), 'Request board guidance overflows the mobile viewport.');
  await page.locator('.request-board-guide summary').click();
  const recommendationGuide = page.locator('.request-board-guide img');
  assert(/assets\/onboarding\/v49\/user-matching\.webp/.test(await recommendationGuide.getAttribute('src')), 'Provider recommendation guidance artwork is missing.');
  await recommendationGuide.evaluate(image => image.complete ? true : new Promise(resolve => image.addEventListener('load', () => resolve(true), { once: true })));
  assert(await recommendationGuide.evaluate(image => image.naturalWidth >= 900 && image.naturalHeight >= 900), 'Provider recommendation guidance is not high resolution.');
  assert(await recommendationGuide.evaluate(image => getComputedStyle(image).objectFit === 'cover'), 'Provider recommendation artwork does not fill its square frame.');
  await page.locator('[data-action="closeModal"]').click();
  assert(await page.locator('#modalRoot .modal-backdrop').count() === 0, 'Closing the request board left a blocking modal layer.');
  await page.locator('.direct-request-card [data-action="quickRequestForm"]').click();
  await page.waitForTimeout(150);
  assert(await page.locator('.request-wizard').count(), `Direct request did not open: ${(await page.locator('#toast').textContent().catch(() => '')) || errors.join(' | ') || 'no visible message'}`);
  await capture(page, '01c-direct-service');
  await page.locator('[data-action="requestSelectCategory"].available').first().click();
  await page.waitForSelector('[data-action="requestSelectService"]');
  await page.locator('[data-action="requestSelectService"].available').first().click();
  assert(Boolean(await page.locator('#qrCategory').inputValue()), 'Available category was not selected.');
  assert(Boolean(await page.locator('#qrService').inputValue()), 'Available service was not selected.');
  await page.locator('[data-action="requestWizardNext"][data-step="2"]:visible').click();
  assert(await page.locator('.request-location-stage').count(), 'Location step is missing from direct request.');
  await capture(page, '01d-direct-location');
  const selectedServiceBeforeMap = await page.locator('#qrService').inputValue();
  const selectedGovernorateBeforeMap = await page.locator('#qrGov').inputValue();
  await page.locator('.request-wizard-step.active [data-action="openRequestLocationMap"]').click();
  await page.waitForSelector('.request-map-picker .leaflet-live-map[data-selectable="1"]');
  await page.locator('.request-map-picker [data-action="resumeRequestLocation"]').click();
  assert(await page.locator('.request-wizard[data-step="2"]').count(), 'Closing the request map should return to the location step only.');
  assert(await page.locator('#qrService').inputValue() === selectedServiceBeforeMap, 'Closing the request map lost the chosen service.');
  assert(await page.locator('#qrGov').inputValue() === selectedGovernorateBeforeMap, 'Closing the request map lost the chosen governorate.');
  await page.locator('.request-wizard-step.active [data-action="openRequestLocationMap"]').click();
  await page.waitForSelector('.request-map-picker .leaflet-live-map[data-selectable="1"]');
  await page.locator('.request-map-picker .leaflet-live-map').click({ position: { x: 170, y: 170 } });
  await page.waitForFunction(() => Boolean(document.querySelector('#mapPickLat')?.value && document.querySelector('#mapPickLng')?.value));
  await page.locator('[data-action="usePickedRequestLocation"]').click();
  assert(await page.locator('.request-wizard[data-step="2"]').count(), 'Map selection should resume at the location step.');
  assert(await page.locator('#qrService').inputValue() === selectedServiceBeforeMap, 'Map selection lost the chosen service.');
  assert(Boolean(await page.locator('#qrLocation').inputValue()), 'Selected map point was not saved to the request.');
  await page.locator('[data-action="requestWizardNext"][data-step="3"]:visible').click();
  await page.locator('#qrNote').fill('أحتاج تنفيذ هذه الخدمة في المنزل خلال هذا الأسبوع');
  await page.locator('[data-action="requestWizardNext"][data-step="4"]:visible').click();
  assert(await page.locator('.match-summary').count(), 'Request matching summary is missing.');
  await page.locator('[data-action="saveQuickRequest"]').click();
  await page.waitForSelector('.active-request-home');
  await clickFirstAction(page, 'openRequestBoard');
  assert(await page.locator('.request-opportunity').count(), 'New request is missing from the request board.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'myAccount');
  assert(await page.locator('.requests-disclosure').count(), 'Grouped request sections are missing from My Account.');
  assert(await page.locator('.requests-disclosure[open]').count() === 0, 'Request groups should start collapsed.');
  await page.locator('.requests-disclosure summary').first().click();
  assert(await page.locator('.requests-disclosure[open] .request-card').count(), 'Created request is missing from the active request section.');
  await page.locator('.requests-disclosure summary').first().click();
  assert(await page.locator('.loyalty-card-v40 [role="progressbar"]').count(), 'Clear loyalty progress bar is missing.');
  await page.locator('[data-action="openAppearance"]').click();
  await page.locator('[data-action="setTheme"][data-value="dark"]').click();
  assert(await page.locator('body').getAttribute('data-theme') === 'dark', 'Dark theme was not applied immediately.');
  const darkPanelColor = await page.locator('.appearance-options').first().evaluate(element => getComputedStyle(element.closest('.modal')).backgroundColor);
  assert(!/rgb\(255, 255, 255\)/.test(darkPanelColor), 'Dark theme still renders a light appearance panel.');
  await page.locator('[data-action="setTheme"][data-value="light"]').click();
  assert(await page.locator('body').getAttribute('data-theme') === 'light', 'Light theme was not restored immediately.');
  await page.locator('[data-action="setDisplayScale"][data-value="large"]').click();
  assert(await page.locator('body').getAttribute('data-scale') === 'large', 'Large text mode was not applied.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'home');

  await page.locator('[data-action="goBack"]').click();
  await page.locator('[data-action="enterProvider"]').click();
  await page.locator('[data-action="openProviderAccess"][data-mode="register"]').click();
  assert(await page.locator('#providerRegisterForm').count(), 'Register provider must open the registration form directly.');
  assert(await page.locator('#providerRegisterForm .registration-subservice.show').count() === 0, 'Optional sub-services should start collapsed.');
  const progressDirection = await page.locator('.provider-reg-progress').evaluate(element => getComputedStyle(element).direction);
  assert(progressDirection === 'rtl', 'Arabic provider registration progress must run right to left.');
  assert(!(await page.locator('[data-action="addRegistrationSubservice"]').isVisible()), 'Individual registration must not offer extra services.');
  await page.locator('#regProviderType').selectOption('company');
  assert(await page.locator('[data-action="addRegistrationSubservice"]').isVisible(), 'Company registration must offer plan-limited services.');
  await page.locator('[data-action="addRegistrationSubservice"]').click();
  assert(await page.locator('#providerRegisterForm .registration-subservice.show').count() === 1, 'Company add-service should reveal one optional field at a time.');
  await capture(page, '01e-provider-register');
  await page.locator('#modalRoot [data-action="closeModal"]').click();
  await page.locator('[data-action="toggleLang"]').first().click();
  await page.locator('[data-action="openProviderAccess"][data-mode="register"]').click();
  const registrationHasArabic = async () => page.locator('#providerRegisterForm').evaluate(form => [...form.querySelectorAll('label,button,option,[placeholder],.account-type-note,.upload-hint,h3')].some(element => /[\u0600-\u06ff]/.test((element.getAttribute('placeholder') || element.textContent || '').trim())));
  assert(!(await registrationHasArabic()), 'English individual-provider registration still contains Arabic interface labels.');
  await page.locator('#regProviderType').selectOption('company');
  assert(!(await registrationHasArabic()), 'English company registration still contains Arabic interface labels.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'English provider registration overflows horizontally.');
  await page.locator('#modalRoot [data-action="closeModal"]').click();
  await page.locator('[data-action="toggleLang"]').first().click();
  await page.locator('#loginPhone').fill('91234567');
  await page.locator('#loginOtp').fill('1234');
  await page.locator('[data-action="providerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();
  const dualRoleAuth = await page.evaluate(() => JSON.parse(localStorage.getItem('KHADAMATI_AUTH_V3') || '{}'));
  assert(dualRoleAuth.providerToken === 'ui-provider-token' && dualRoleAuth.userToken === 'ui-user-token', 'Provider sign-in discarded the existing user session on the same device.');
  await page.waitForTimeout(200);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Provider login notification popup is empty.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  const providerBadge = page.locator('.provider-top-actions .notification-badge').first();
  if (await providerBadge.count()) {
    const badgeBox = await providerBadge.boundingBox();
    const bellBox = await providerBadge.locator('..').boundingBox();
    assert(badgeBox && bellBox && badgeBox.x >= bellBox.x - 10 && badgeBox.x <= bellBox.x + bellBox.width + 10, 'Provider notification badge is not anchored to the bell.');
  }
  assert(await page.locator('.week-calendar').count(), 'Provider weekly calendar is missing.');
  assert(await page.locator('.quote-template-grid').count(), 'Provider quote templates are missing.');
  assert(await page.locator('.provider-request-dock').count(), 'Fixed provider request dock is missing.');
  const providerTopFits = await page.locator('.provider-topbar').evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
  assert(providerTopFits, 'Provider top bar overflows the mobile viewport.');
  const dockBox = await page.locator('.provider-request-dock').boundingBox();
  assert(dockBox && dockBox.x >= 0 && dockBox.x + dockBox.width <= VIEWPORT_WIDTH, 'Provider request dock is outside the viewport.');
  await capture(page, '02-provider-dashboard');
  await page.locator('.provider-top-actions [data-action="toggleLang"]').click();
  await page.waitForTimeout(150);
  assert(await page.locator('html').getAttribute('dir') === 'ltr', 'Provider English mode did not switch to LTR.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Provider English layout overflows horizontally.');
  const providerNavFits = await page.locator('.provider-shell .side-nav').evaluate(element => {
    const rect = element.getBoundingClientRect();
    return rect.left >= -1 && rect.right <= window.innerWidth + 1 && (window.innerWidth > 940 || getComputedStyle(element).overflowX !== 'visible');
  });
  assert(providerNavFits, 'Provider English tabs are not contained in their horizontal scroller.');
  const englishDockBox = await page.locator('.provider-request-dock').boundingBox();
  assert(englishDockBox && englishDockBox.x >= 0 && englishDockBox.x + englishDockBox.width <= VIEWPORT_WIDTH, 'English Opportunities dock is outside the viewport.');
  assert(await page.locator('.provider-request-dock').filter({ hasText: /Opportunities/i }).count(), 'English Opportunities label is missing.');
  const providerDockContentFits = await page.locator('.provider-request-dock').evaluate(element => {
    const icon = element.querySelector(':scope > span:first-child')?.getBoundingClientRect();
    const copy = element.querySelector('.provider-request-dock-copy')?.getBoundingClientRect();
    if (!icon || !copy) return false;
    return copy.width > 80 && (copy.left >= icon.right - 1 || icon.left >= copy.right - 1);
  });
  assert(providerDockContentFits, 'Provider Opportunities icon overlaps its label.');
  await capture(page, '02a-provider-english');
  await page.locator('.provider-top-actions [data-action="toggleLang"]').click();
  await page.waitForTimeout(100);
  assert(await page.locator('html').getAttribute('dir') === 'rtl', 'Provider Arabic mode was not restored to RTL.');
  await page.locator('[data-action="openQuoteLibrary"]').first().click();
  assert(await page.locator('.modal-title').filter({ hasText: /عرض السعر|price and duration/i }).count(), 'Quote template sheet did not open.');
  await page.locator('[data-action="closeModal"]').click();

  await page.locator('.side-nav [data-action="providerTab"][data-tab="leads"]').click();
  await clickFirstAction(page, 'openRequestBoard');
  assert(await page.locator('.request-opportunity').count(), 'Provider request board is empty.');
  assert(await page.locator('.request-opportunity [data-action="providerAcceptRequest"]').count(), 'Matching provider cannot offer from the request board.');
  await capture(page, '02b-provider-opportunities');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="providerAcceptRequest"]').first().click();
  await page.locator('#offerPrice').fill('12');
  await page.locator('#offerDuration').fill('خلال ساعتين');
  await page.locator('#offerNote').fill('يشمل المعاينة والتنفيذ');
  await page.locator('[data-action="submitProviderOffer"]').click();
  await page.waitForSelector('#modalRoot .modal-backdrop', { state: 'detached' });
  await page.locator('[data-action="providerUserMode"]').click();
  await clickUserNav(page, 'myAccount');
  assert(await page.locator('.request-offer-summary').count(), 'Offer comparison summary is missing.');
  await page.waitForTimeout(600);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Unexpected modal blocked offer comparison.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  await page.locator('.requests-disclosure').first().locator('summary').click();
  await page.locator('[data-action="compareRequestOffers"]').first().click();
  assert(await page.locator('.offer-card').count(), 'Offer comparison card is missing.');
  await capture(page, '04-offer-comparison');
  await page.locator('[data-action="acceptRequestOffer"]').first().click();

  await page.waitForSelector('#contactAllowChat');
  assert(!(await page.locator('#contactAllowChat').isChecked()), 'Chat consent must start disabled.');
  assert(!(await page.locator('#contactAllowWhatsapp').isChecked()), 'WhatsApp consent must start disabled.');
  assert(!(await page.locator('#contactAllowCall').isChecked()), 'Call consent must start disabled.');
  await page.locator('[data-action="closeModal"]').click();

  await page.locator('.requests-disclosure').first().locator('summary').click();
  assert(await page.locator('[data-action="manageRequestContact"]').count(), 'Contact privacy control is missing after provider selection.');
  assert(await page.locator('[data-action="requestWhatsapp"]').count() === 0, 'WhatsApp must stay hidden before customer consent.');
  assert(await page.locator('[data-action="requestCall"]').count() === 0, 'Phone calls must stay hidden before customer consent.');
  await page.locator('[data-action="manageRequestContact"]').first().click();
  await page.locator('#contactAllowChat').check();
  await page.locator('#contactAllowWhatsapp').check();
  await page.locator('#contactAllowCall').check();
  await page.locator('[data-action="saveRequestContactConsent"]').click();
  await page.locator('.requests-disclosure').first().locator('summary').click();
  assert(await page.locator('[data-action="requestWhatsapp"]').count(), 'WhatsApp was not enabled after customer consent.');
  assert(await page.locator('[data-action="requestCall"]').count(), 'Phone calls were not enabled after customer consent.');
  await page.locator('[data-action="openRequestChat"]').first().click();
  const chatViewportFit = await page.locator('.chat-sheet').evaluate(sheet => {
    const rect = sheet.getBoundingClientRect();
    const composer = sheet.querySelector('.chat-composer')?.getBoundingClientRect();
    return rect.top <= 1 && rect.left <= 1 && rect.right >= innerWidth - 1 && rect.bottom >= innerHeight - 1 && composer && composer.left >= -1 && composer.right <= innerWidth + 1 && composer.bottom <= innerHeight + 1;
  });
  assert(chatViewportFit, 'Chat does not fill the active viewport or its composer overflows the phone screen.');
  assert(await page.locator('.chat-sheet [data-action="refreshRequestChat"]').count() === 0, 'Chat still exposes a manual refresh button.');
  assert(await page.evaluate(() => Boolean(window.__khadamatiChatPoll)), 'Chat automatic refresh did not start.');
  assert(await page.locator('.chat-quick-replies button').count() >= 3, 'Chat quick replies are missing.');
  await page.locator('.chat-quick-replies button').first().click();
  assert(Boolean(await page.locator('#chatText').inputValue()), 'Quick reply did not fill the chat composer.');
  await page.locator('#chatText').fill('تم تأكيد الموعد');
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine');
  await page.locator('#chatImage').setInputFiles(path.join(__dirname, '..', 'app-icon-192.png'));
  await page.locator('#chatText').fill('صورة توضيحية');
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine img');
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForTimeout(900);
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForSelector('.voice-ready:not(:empty)');
  assert(await page.locator('[data-action="cancelChatAudio"]').count() === 1, 'Voice recording cannot be discarded before sending.');
  await page.locator('[data-action="cancelChatAudio"]').click();
  assert(await page.locator('.voice-ready:not(:empty)').count() === 0, 'Discarded voice recording remained in the composer.');
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForTimeout(900);
  await page.locator('[data-action="toggleChatRecording"]').click();
  await page.waitForSelector('.voice-ready:not(:empty)');
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine audio');
  await capture(page, '05-request-chat');
  await page.locator('[data-action="closeModal"]').click();
  assert(await page.evaluate(() => !window.__khadamatiChatPoll), 'Chat automatic refresh continued after closing the conversation.');

  const downloadPromise = page.waitForEvent('download');
  await page.locator('[data-action="addRequestCalendar"]').first().click();
  const calendarDownload = await downloadPromise;
  assert((await calendarDownload.suggestedFilename()).endsWith('.ics'), 'Calendar export is not an ICS file.');

  await page.locator('.account-menu [data-action="nav"][data-view="provider"]').click();
  await page.locator('.provider-top-actions [data-action="openNotifications"]').click();
  assert(await page.locator('.notification-center-tab').count() === 3, 'Notification center must have chats, requests, and updates sections.');
  await page.locator('[data-action="notificationCenterTab"][data-value="messages"]').click();
  const providerChatNotice = page.locator('.chat-notification [data-action="notificationAction"]').first();
  assert(await providerChatNotice.count(), 'Provider chat notification is missing from the chats section.');
  assert((await page.locator('.chat-notification .notification-copy b').first().textContent()).includes('مستخدم الاختبار الآلي'), 'Provider chat notification does not identify the customer.');
  await providerChatNotice.locator('xpath=ancestor::details').locator('summary').click();
  await providerChatNotice.click();
  await page.waitForSelector('.chat-sheet #chatThread');
  assert(await page.locator('.chat-sheet .modal-title').filter({ hasText: /مستخدم الاختبار|الخدمة|صيانة/i }).count(), 'Provider notification did not open the correct chat directly.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('.side-nav [data-action="providerTab"][data-tab="leads"]').click();
  await page.locator('[data-action="providerLeadFilter"][data-value="mine"]').click();
  await page.locator('[data-action="openRequestChat"]').first().click();
  assert(await page.locator('[data-action="providerCustomerWhatsapp"]').count(), 'Selected provider cannot use customer-approved WhatsApp.');
  assert(await page.locator('[data-action="providerCustomerCall"]').count(), 'Selected provider cannot use customer-approved calls.');
  await page.locator('#chatText').fill('رسالة متابعة من سالم البلوشي');
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="openArrivalTracking"]').first().click();
  await page.locator('[data-action="updateProviderArrival"][data-status="onTheWay"]').click();
  await page.waitForSelector('.arrival-card');
  await capture(page, '06-arrival-tracking');
  await page.locator('[data-action="closeModal"]').click();

  await page.locator('.side-nav [data-action="providerTab"][data-tab="profile"]').click();
  await page.locator('#ppBeforeImage').setInputFiles(path.join(__dirname, '..', 'app-icon-192.png'));
  await page.locator('#ppAfterImage').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.locator('#ppBeforeAfterCaption').fill('نتيجة اختبار قبل وبعد');
  await page.locator('[data-action="saveBeforeAfterPair"]').click();
  await page.waitForSelector('.rich-media-editor .list-item');
  await page.locator('#ppIntroVideoUrl').fill('https://example.com/khadamati-intro.mp4');
  await page.locator('[data-action="saveProviderIntroVideo"]').click();
  await page.waitForSelector('.provider-media-preview');
  await capture(page, '07-provider-media');

  await page.locator('[data-action="providerUserMode"]').click();
  await page.locator('.app-top [data-action="openNotifications"]').click();
  await page.locator('[data-action="notificationCenterTab"][data-value="messages"]').click();
  const userChatNotice = page.locator('.chat-notification [data-action="notificationAction"]').first();
  assert(await userChatNotice.count(), 'User chat notification is missing from the chats section.');
  assert((await page.locator('.chat-notification .notification-copy b').first().textContent()).includes('سالم البلوشي'), 'User chat notification does not identify the provider.');
  await userChatNotice.locator('xpath=ancestor::details').locator('summary').click();
  await userChatNotice.click();
  await page.waitForSelector('.chat-sheet #chatThread');
  assert(await page.locator('.chat-sheet .modal-title').filter({ hasText: /سالم البلوشي/i }).count(), 'User notification did not open the correct chat directly.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'search');
  await page.waitForTimeout(600);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Unexpected modal blocked public provider profile.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  await page.locator('[data-action="providerDetails"][data-id="p1"]').first().click();
  assert(await page.locator('.provider-intro-video').count(), 'Provider introduction video is missing from the public profile.');
  assert(await page.locator('.before-after-card').count(), 'Before/after gallery is missing from the public profile.');
  await page.locator('.provider-detail-sheet [data-action="openProviderOnMap"]').click();
  await page.waitForSelector('.live-map-full .map-my-location');
  await page.locator('.live-map-full .map-my-location').click();
  await page.waitForTimeout(250);
  await page.locator('.live-map-full [data-action="closeModalSoft"]').click();
  assert(await page.locator('.provider-detail-sheet').count(), 'Closing the provider map must restore the same provider profile.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'myAccount');
  await page.locator('.account-menu [data-action="nav"][data-view="provider"]').click();
  await page.locator('[data-action="providerLogout"]').click();
  await page.locator('[data-action="goBack"]').click();
  await page.locator('[data-action="enterGuest"]').click();
  if (await page.locator('.role-onboarding').count()) {
    assert(/assets\/onboarding\/v49\/guest-browse\.webp/.test(await page.locator('.role-onboarding .onboarding-visual img').getAttribute('src')), 'Guest onboarding did not open its dedicated artwork.');
    await page.locator('[data-action="skipOnboarding"]').click();
  }
  assert(await page.locator('.app-top [data-action="openNotifications"] .notification-badge').count() === 0, 'Guest must not inherit the previous user notification badge.');
  await page.locator('.app-top [data-action="openNotifications"]').click();
  assert(await page.locator('.notification-center-sheet .guest-note').count(), 'Guest notification privacy note is missing.');
  assert(await page.locator('.notification-center-sheet .notification-disclosure').count() === 0, 'Guest can see notifications from the previous signed-in account.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="goBack"]').click();
  for (let i = 0; i < 6; i++) await page.locator('[data-action="brandHome"]').first().click();
  await page.waitForSelector('#adminCode');
  await page.locator('#adminCode').fill('0000');
  await page.locator('[data-action="adminLogin"]').click();
  await page.waitForSelector('.admin-shell');
  await page.locator('.side-nav [data-action="adminTab"][data-tab="subscriptions"]').click();
  assert(await page.locator('.subscription-command').count(), 'Subscription control center is missing.');
  assert(await page.locator('.package-admin-grid .package-admin-card').count() === 5, 'The production plan catalog must contain exactly five plans.');
  await page.locator('.package-admin-grid [data-action="packageForm"]').first().click();
  await page.waitForSelector('#pkgMaxWilayats');
  assert(await page.locator('#pkgLeadDelay').count(), 'Plan lead-delay entitlement is missing.');
  assert(await page.locator('#pkgMonthlyResponses').count(), 'Plan monthly-response entitlement is missing.');
  assert(await page.locator('#pkgSharedInbox').count(), 'Plan shared-inbox entitlement is missing.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('.side-nav [data-action="adminTab"][data-tab="finance"]').click();
  assert(await page.locator('.finance-command-grid').count(), 'Financial control center is missing.');
  await page.locator('.side-nav [data-action="adminTab"][data-tab="assistant"]').click();
  assert(await page.locator('.subscription-command h2').filter({ hasText: /ساحة الطلبات|Request marketplace/i }).count(), 'The obsolete assistant health page was not replaced by the request marketplace.');
  assert(await page.locator('[data-action="openAssistant"]').count() === 0, 'The obsolete assistant test control is still visible in administration.');
  await page.locator('.side-nav [data-action="adminTab"][data-tab="settings"]').click();
  assert(await page.locator('.operations-settings').count(), 'Platform operations settings are missing.');
  await page.locator('[data-action="adminTab"][data-tab="ads"]').click();
  await page.locator('#adImages').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.locator('[data-action="previewAdDraft"]').click();
  await page.waitForSelector('.ad-preview-device');
  assert(await page.locator('.ad-preview-device').count() === 2, 'Phone and desktop ad previews are missing.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="adminTab"][data-tab="quality"]').click();
  assert(await page.locator('.system-health').count(), 'System health monitoring panel is missing.');
  await capture(page, '03-admin-quality');

  await page.locator('.topbar [data-action="toggleLang"]').click();
  assert(await page.locator('html').getAttribute('dir') === 'ltr', 'English mode did not switch the document to LTR.');
  assert(await page.locator('.brand').filter({ hasText: /Administration/i }).count(), 'English administration title is missing.');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'English layout overflows horizontally.');

  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  assert(fits, 'Mobile layout overflows horizontally.');
  assert(errors.length === 0, `Browser errors: ${errors.join(' | ')}`);

  console.log(JSON.stringify({
    ok: true,
    userFlow: true,
    requestFlow: true,
    providerFlow: true,
    adminFlow: true,
    mobileFit: fits,
  }, null, 2));
  await browser.close();
  LOCAL_SERVER?.close();
})().catch(async error => {
  console.error(error);
  LOCAL_SERVER?.close();
  process.exit(1);
});
