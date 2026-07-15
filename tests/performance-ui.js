const { chromium } = require('playwright');

const BASE_URL = process.env.KHADAMATI_TEST_URL || 'http://127.0.0.1:8080/';
const CHROME_PATH = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: CHROME_PATH });
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    locale: 'ar-OM',
  });
  const startedAt = Date.now();
  await page.goto(BASE_URL, { waitUntil: 'load' });
  await page.waitForSelector('[data-action="openUserLogin"]');
  const metrics = await page.evaluate(() => {
    const navigation = performance.getEntriesByType('navigation')[0];
    const paint = performance.getEntriesByName('first-contentful-paint')[0];
    return {
      domContentLoadedMs: Math.round(navigation.domContentLoadedEventEnd),
      loadMs: Math.round(navigation.loadEventEnd),
      firstContentfulPaintMs: paint ? Math.round(paint.startTime) : null,
      transferredBytes: navigation.transferSize,
    };
  });
  metrics.interactiveReadyMs = Date.now() - startedAt;
  metrics.withinLocalTarget = metrics.interactiveReadyMs < 3500;
  console.log(JSON.stringify(metrics, null, 2));
  await browser.close();
  if (!metrics.withinLocalTarget) process.exit(1);
})().catch(error => {
  console.error(error);
  process.exit(1);
});
