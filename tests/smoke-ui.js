const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE_URL = process.env.KHADAMATI_TEST_URL || 'http://127.0.0.1:8080/';
const CHROME_PATH = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const SCREENSHOT_DIR = process.env.KHADAMATI_SCREENSHOT_DIR || '';
const VIEWPORT_WIDTH = Number(process.env.KHADAMATI_VIEWPORT_WIDTH || 390);
const VIEWPORT_HEIGHT = Number(process.env.KHADAMATI_VIEWPORT_HEIGHT || 844);
const IS_MOBILE = VIEWPORT_WIDTH <= 760;

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
    locale: 'ar-OM',
    permissions: ['geolocation', 'microphone', 'notifications'],
    geolocation: { latitude: 23.61, longitude: 58.24 },
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error' && !/favicon|tile|Failed to load resource/.test(message.text())) {
      errors.push(message.text());
    }
  });

  // Keep the smoke test local and deterministic without writing test accounts to the server.
  await page.route('**/api/**', route => route.abort());
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => localStorage.clear());
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-action="openUserLogin"]');
  await capture(page, '00-entry');

  await page.locator('[data-action="openUserLogin"]').click();
  await page.locator('#customerLoginPhone').fill('95550001');
  await page.locator('#customerLoginName').fill('مستخدم الاختبار الآلي');
  await page.locator('[data-action="customerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();

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
  await capture(page, '01b-progressive-search');
  await clickUserNav(page, 'home');

  await clickFirstAction(page, 'openRequestBoard');
  assert(await page.locator('.request-board-sheet').count(), 'Request board did not open.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="quickRequestForm"]').first().click();
  await page.waitForSelector('.request-wizard');
  await capture(page, '01c-direct-service');
  await page.locator('#qrCategory').selectOption({ index: 1 });
  await page.waitForSelector('#qrService');
  await page.locator('#qrService').selectOption({ index: 1 });
  await page.locator('[data-action="requestWizardNext"][data-step="2"]:visible').click();
  assert(await page.locator('.request-location-stage').count(), 'Location step is missing from direct request.');
  await capture(page, '01d-direct-location');
  const selectedServiceBeforeMap = await page.locator('#qrService').inputValue();
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
  await page.locator('[data-action="openAppearance"]').click();
  await page.locator('[data-action="setDisplayScale"][data-value="large"]').click();
  assert(await page.locator('body').getAttribute('data-scale') === 'large', 'Large text mode was not applied.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'home');

  await page.locator('[data-action="goBack"]').click();
  await page.locator('[data-action="enterProvider"]').click();
  await page.locator('#loginPhone').fill('91234567');
  await page.locator('#loginOtp').fill('1234');
  await page.locator('[data-action="providerLogin"]').click();
  await page.waitForSelector('.role-onboarding');
  await page.locator('[data-action="skipOnboarding"]').click();
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
  const dockBox = await page.locator('.provider-request-dock').boundingBox();
  assert(dockBox && dockBox.x >= 0 && dockBox.x + dockBox.width <= VIEWPORT_WIDTH, 'Provider request dock is outside the viewport.');
  await capture(page, '02-provider-dashboard');
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

  await page.locator('.requests-disclosure').first().locator('summary').click();
  assert(await page.locator('[data-action="manageRequestContact"]').count(), 'Contact privacy control is missing after provider selection.');
  assert(await page.locator('[data-action="requestWhatsapp"]').count() === 0, 'WhatsApp must stay hidden before customer consent.');
  assert(await page.locator('[data-action="requestCall"]').count() === 0, 'Phone calls must stay hidden before customer consent.');
  await page.locator('[data-action="manageRequestContact"]').first().click();
  await page.locator('#contactAllowWhatsapp').check();
  await page.locator('#contactAllowCall').check();
  await page.locator('[data-action="saveRequestContactConsent"]').click();
  await page.locator('.requests-disclosure').first().locator('summary').click();
  assert(await page.locator('[data-action="requestWhatsapp"]').count(), 'WhatsApp was not enabled after customer consent.');
  assert(await page.locator('[data-action="requestCall"]').count(), 'Phone calls were not enabled after customer consent.');
  await page.locator('[data-action="openRequestChat"]').first().click();
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
  await page.locator('[data-action="sendChatMessage"]').click();
  await page.waitForSelector('.chat-message.mine audio');
  await capture(page, '05-request-chat');
  await page.locator('[data-action="closeModal"]').click();

  const downloadPromise = page.waitForEvent('download');
  await page.locator('[data-action="addRequestCalendar"]').first().click();
  const calendarDownload = await downloadPromise;
  assert((await calendarDownload.suggestedFilename()).endsWith('.ics'), 'Calendar export is not an ICS file.');

  await page.locator('.account-menu [data-action="nav"][data-view="provider"]').click();
  await page.locator('.side-nav [data-action="providerTab"][data-tab="leads"]').click();
  await page.locator('[data-action="providerLeadFilter"][data-value="mine"]').click();
  await page.locator('[data-action="openRequestChat"]').first().click();
  assert(await page.locator('[data-action="providerCustomerWhatsapp"]').count(), 'Selected provider cannot use customer-approved WhatsApp.');
  assert(await page.locator('[data-action="providerCustomerCall"]').count(), 'Selected provider cannot use customer-approved calls.');
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
  await clickUserNav(page, 'search');
  await page.waitForTimeout(600);
  if (await page.locator('#modalRoot .modal-backdrop.show').count()) {
    assert(await page.locator('#modalRoot .notification-disclosure').count(), 'Unexpected modal blocked public provider profile.');
    await page.locator('#modalRoot [data-action="closeModal"]').first().click();
  }
  await page.locator('[data-action="providerDetails"][data-id="p1"]').first().click();
  assert(await page.locator('.provider-intro-video').count(), 'Provider introduction video is missing from the public profile.');
  assert(await page.locator('.before-after-card').count(), 'Before/after gallery is missing from the public profile.');
  await page.locator('[data-action="closeModal"]').click();
  await clickUserNav(page, 'myAccount');
  await page.locator('.account-menu [data-action="nav"][data-view="provider"]').click();
  await page.locator('[data-action="providerLogout"]').click();
  for (let i = 0; i < 6; i++) await page.locator('[data-action="brandHome"]').first().click();
  await page.waitForSelector('#adminCode');
  await page.locator('#adminCode').fill('0000');
  await page.locator('[data-action="adminLogin"]').click();
  await page.waitForSelector('.admin-shell');
  await page.locator('[data-action="adminTab"][data-tab="ads"]').click();
  await page.locator('#adImages').setInputFiles(path.join(__dirname, '..', 'app-icon-512.png'));
  await page.locator('[data-action="previewAdDraft"]').click();
  await page.waitForSelector('.ad-preview-device');
  assert(await page.locator('.ad-preview-device').count() === 2, 'Phone and desktop ad previews are missing.');
  await page.locator('[data-action="closeModal"]').click();
  await page.locator('[data-action="adminTab"][data-tab="quality"]').click();
  assert(await page.locator('.system-health').count(), 'System health monitoring panel is missing.');
  await capture(page, '03-admin-quality');

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
})().catch(async error => {
  console.error(error);
  process.exit(1);
});
