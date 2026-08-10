// Walks every page of a running app, screenshots it, and fails on the two
// things the Python tests cannot see: an exception block, and markup that
// leaked into the rendered copy.
//
// The leak check is the point. Streamlit renders `$…$` as LaTeX, so any
// sentence carrying two money figures — "$75.07 comes off what you owe;
// $174.93 goes to the lender" — collapses into a run of maths glyphs. Nothing
// throws, the AppTest suite stays green, and the page is unreadable. The only
// reliable detector is a real browser: the mangled text lands in a `.katex`
// node. Same for `**bold**` reaching the screen as literal asterisks.
//
//   pip install -r requirements.txt && npm i playwright && npx playwright install chromium
//   DEBTMANAGER_DB=data/preview.db streamlit run app.py --server.port 8765 &
//   node tools/screenshot.mjs out/            # exits non-zero on any finding
//
// Expects an account seeded as demo@example.com / hunter2hunter2.
import { chromium } from 'playwright';
import { mkdirSync } from 'fs';

const BASE = process.env.APP_URL || 'http://localhost:8765';
const EMAIL = process.env.APP_EMAIL || 'demo@example.com';
const PASSWORD = process.env.APP_PASSWORD || 'hunter2hunter2';
const out = process.argv[2] || 'shots';
const only = process.argv.slice(3);

// Indexed, not named: each nav link's accessible name includes the Material
// icon ligature, so labels never match exactly. Order follows st.navigation()
// in app.py. Account settings is intentionally hidden from primary navigation.
const PAGES = ['home', 'debts', 'plan', 'ledger'];
mkdirSync(out, { recursive: true });

const settle = async (page, ms = 2500) => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(ms);
  // Streamlit paints charts only after the websocket delivers them, so wait for
  // the "running" indicator to clear rather than guessing at a fixed delay.
  await page.waitForSelector('[data-testid="stStatusWidget"]',
                             { state: 'detached', timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(700);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 },
                                       deviceScaleFactor: 2 });
const page = await ctx.newPage();

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await settle(page);
await page.screenshot({ path: `${out}/00-login.png`, fullPage: true });

await page.getByRole('textbox', { name: 'Email' }).first().fill(EMAIL);
await page.getByRole('textbox', { name: 'Password' }).first().fill(PASSWORD);
await page.getByRole('button', { name: /^Sign in$/ }).click();
await settle(page, 4000);

// The session token rides in the query string; if it isn't there, we are still
// looking at the login form and every "page" below would be the same shot.
if (!new URL(page.url()).searchParams.get('s')) {
  console.error('!! sign-in failed — check the seeded account');
  await browser.close();
  process.exit(1);
}

let bad = 0;
for (const [i, slug] of PAGES.entries()) {
  if (only.length && !only.includes(slug)) continue;

  // Navigate by clicking the sidebar rather than by URL: a direct hit on
  // /ledger arrives without the token, renders the login form, and drops a
  // "page not found" dialog over whatever we were about to shoot.
  const close = page.locator('[data-testid="stDialog"] button[aria-label="Close"]');
  if (await close.count()) { await close.first().click(); await page.waitForTimeout(500); }
  await page.locator('[data-testid="stSidebarNavLink"]').nth(i).click();
  await settle(page, 3500);
  await page.screenshot({ path: `${out}/${slug}.png`, fullPage: true });

  const errs = await page.locator('[data-testid="stException"]').count();
  if (errs) {
    bad += errs;
    console.error(`!! ${slug}: ${errs} exception(s)\n` +
      (await page.locator('[data-testid="stException"]').first().innerText()).slice(0, 900));
  }

  const leaks = await page.evaluate(() => {
    const found = [];
    if (document.querySelector('.katex')) found.push('KaTeX — money read as LaTeX');
    for (const m of document.body.innerText.matchAll(/\*\*?[^*\n]{1,40}\*\*?/g)) {
      found.push(`literal markup: ${m[0]}`);
    }
    return [...new Set(found)];
  });
  if (leaks.length) { bad += leaks.length; console.error(`!! ${slug}: ${leaks.join(' | ')}`); }

  if (!errs && !leaks.length) console.log(`ok  ${slug}`);
}

await browser.close();
console.log(bad ? `FAILED — ${bad} finding(s)` : `clean — ${PAGES.length} pages`);
process.exit(bad ? 1 : 0);
