// Screenshot the viewer from every render viewpoint (or the ones named) for the visual comparison loop.
// Needs the dev server running (npm run dev). Uses the installed Microsoft Edge through playwright-core.
// Usage: node tools/snap.mjs [room | room:cam ...]   ->  debug/compare/<room>-<cam>-ours.png
// Screenshots use the render-model ceiling (variant=render) so the geometry can be checked against the renders;
// set VARIANT=owner to shoot the owner's ceiling height instead.
import fs from 'node:fs';
import { chromium } from 'playwright-core';

const base = process.env.VIEWER_URL ?? 'http://localhost:5173';
const variant = process.env.VARIANT ?? 'render';
const extra = process.env.EXTRA ?? ''; // extra URL parameters, e.g. "&ao=0"
const rooms = JSON.parse(fs.readFileSync('data/rooms.json', 'utf8')).rooms;
const want = process.argv.slice(2);
fs.mkdirSync('debug/compare', { recursive: true });

const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--ignore-gpu-blocklist', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ deviceScaleFactor: 1 });
page.on('console', (m) => m.type() === 'error' && console.log('console error:', m.text()));
page.on('pageerror', (e) => console.log('page error:', e.message));
for (const room of rooms) {
  for (const cam of room.cameras) {
    const key = `${room.id}:${cam.id}`;
    if (want.length && !want.some((w) => w === room.id || w === key)) continue;
    const aspect = cam.aspect ?? 4 / 3;
    await page.setViewportSize({ width: 1280, height: Math.round(1280 / aspect) });
    await page.goto(`${base}/?room=${room.id}&cam=${cam.id}&shot=1&variant=${variant}${extra}`, { timeout: 240000 });
    await page.waitForFunction(() => window.__ready === true, null, { timeout: 240000 });
    await page.waitForTimeout(500);
    const out = `debug/compare/${room.id}-${cam.id}-ours.png`;
    await page.screenshot({ path: out });
    console.log('saved', out);
  }
}
await browser.close();
