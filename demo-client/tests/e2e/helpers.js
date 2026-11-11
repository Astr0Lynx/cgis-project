import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

export const EVIDENCE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "evidence", "screenshots");
fs.mkdirSync(EVIDENCE, { recursive: true });

export const ADMIN = { user: "admin", pass: "admin-fixture-pass" };
export const USER = { user: "analyst", pass: "user-fixture-pass" };

/** Collect console errors and HTTP failures for the whole test. */
export function watch(page) {
  const log = { console: [], failed: [], requests: [] };
  page.on("console", (m) => { if (m.type() === "error") log.console.push(m.text()); });
  page.on("pageerror", (e) => log.console.push(`pageerror: ${e.message}`));
  page.on("response", (r) => { if (r.status() >= 400) log.failed.push(`${r.status()} ${r.url()}`); });
  page.on("request", (r) => log.requests.push(r.url()));
  return log;
}

/** Wait until every visible map has loaded its tiles and the busy bar is gone. */
export async function settle(page, timeout = 60_000) {
  await page.waitForFunction(() => {
    const ok = (m) => m.loaded() && m.areTilesLoaded();
    const b = window.mapB && !document.getElementById("pane-b").hidden ? window.mapB : null;
    return window.map && ok(window.map) && (!b || ok(b)) && document.getElementById("map-progress").hidden;
  }, null, { timeout });
  await page.waitForTimeout(250);
}

export async function open(page, hash = "") {
  await page.goto(`/viewer/${hash}`);
  await page.waitForFunction(() => document.getElementById("output").textContent.includes("collections"), null, { timeout: 30_000 });
  await settle(page);
}

export async function resetMock(request) {
  await request.get("/__mock/reset");
  await request.get("/__mock/clear-requests");
}

export async function mockRequests(request) {
  return (await request.get("/__mock/requests")).json();
}

export async function login(page, { user, pass }) {
  await page.click("#login-btn");
  await page.fill("#login-username", user);
  await page.fill("#login-password", pass);
  await page.click("#login-submit");
  await page.waitForSelector("#logout-btn");
}

export async function shot(page, name) {
  await page.screenshot({ path: path.join(EVIDENCE, `${name}.png`) });
}

/** Click a lon/lat on map A. */
export async function clickLngLat(page, lngLat, selector = "#map") {
  const box = await page.locator(selector).boundingBox();
  const pt = await page.evaluate(({ ll, b }) => (b ? window.mapB : window.map).project(ll), { ll: lngLat, b: selector === "#map-b" });
  await page.mouse.click(box.x + pt.x, box.y + pt.y);
}

/**
 * Draw an AOI rectangle with the Analysis tab's rectangle tool (two clicks). The map is first framed
 * so the rectangle sits in the middle fifth of the view, clear of the legend, timeline and inspector.
 */
export async function drawRect(page, [w, s, e, n]) {
  const cx = (w + e) / 2; const cy = (s + n) / 2; const hx = (e - w) * 2.5; const hy = (n - s) * 2.5;
  await fitTo(page, [cx - hx, cy - hy, cx + hx, cy + hy]);
  await page.click("#tab-btn-analysis");
  await page.click("#draw-rect");
  await clickLngLat(page, [w, s]);
  await clickLngLat(page, [e, n]);
}

export async function fitTo(page, bbox) {
  await page.evaluate((b) => window.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { duration: 0, padding: 20 }), bbox);
  await settle(page);
}
