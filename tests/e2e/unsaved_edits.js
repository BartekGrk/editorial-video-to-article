// Niezapisane edycje przeżywają przełączenie zakładek, a zatwierdzenie ich wymaga zapisu.
const { chromium } = require('playwright');
const SHOTS = process.argv[2] || '.';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const list = await (await fetch('http://localhost:8765/api/materials')).json();
  const target = list.find((m) => m.status === 'APPROVED' || m.status === 'DRAFTED');
  await page.goto(`http://localhost:8765/#/m/${target.id}`);
  await page.click('[data-tab=draft]');
  await page.waitForSelector('#draft-form');
  await page.fill('#lead', (await page.inputValue('#lead')) + ' EDYCJA');
  await page.click('[data-tab=export]');
  await page.waitForSelector('text=Masz niezapisane zmiany');
  await page.click('[data-tab=draft]');
  const lead = await page.inputValue('#lead');
  if (!lead.endsWith('EDYCJA')) errors.push('edycja zgubiona po zmianie zakładki');
  await page.click('#approve').catch(() => {});
  await page.waitForTimeout(500);
  const toast = await page.textContent('#toast');
  if (!toast.includes('niezapisane zmiany')) errors.push('zatwierdzenie bez zapisu nie zostało zablokowane: ' + toast);
  await page.screenshot({ path: SHOTS + '/unsaved.png' });
  console.log('ERRORS:', JSON.stringify(errors));
  await browser.close();
})();
