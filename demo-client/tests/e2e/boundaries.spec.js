// U1: 10k+ boundaries — bbox-scoped, paginated, budgeted; never one limit=1000 country-wide download.
import { test, expect } from "@playwright/test";
import { watch, open, settle, shot, resetMock, mockRequests, fitTo } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

const districtItems = (reqs) => reqs.filter((r) => r.path === "/ogc/collections/mock-india-districts/items");

test("national view: 12,000 districts exceed the budget — one bbox request, nothing partial drawn", async ({ page, request }) => {
  const log = watch(page);
  await open(page);
  const row = page.locator('[data-vector-row="mock-india-districts"]');
  await expect(row).toContainText("12,000 features in this view exceed the 5,000-feature limit");
  const reqs = districtItems(await mockRequests(request));
  expect(reqs.length).toBeGreaterThanOrEqual(1);
  for (const r of reqs) {
    expect(r.query).toContain("bbox=");
    expect(r.query).not.toContain("limit=1000");
    expect(r.query).not.toContain("offset=");   // stopped after the first page
  }
  const drawn = await page.evaluate(() => window.map.querySourceFeatures("v-mock-india-districts").length);
  expect(drawn).toBe(0);
  expect(log.failed).toEqual([]);
});

test("regional view: all matching districts load through several bbox-scoped pages", async ({ page, request }) => {
  await open(page);
  await request.get("/__mock/clear-requests");
  await fitTo(page, [74, 17, 84, 26]);
  const row = page.locator('[data-vector-row="mock-india-districts"]');
  await expect(row).toContainText(/features in view · \d+ pages/);
  const text = await row.locator(".boundary-state").textContent();
  const shown = Number(text.match(/([\d,]+) features/)[1].replaceAll(",", ""));
  expect(shown).toBeGreaterThan(1000);       // beyond the old 1000-feature truncation
  expect(shown).toBeLessThanOrEqual(5000);
  const reqs = districtItems(await mockRequests(request));
  expect(reqs.some((r) => r.query.includes("offset="))).toBeTruthy();
  expect(reqs.every((r) => r.query.includes("bbox="))).toBeTruthy();
  // Cross-check the count with the server's own numberMatched for the same bbox.
  const bbox = new URLSearchParams(reqs.at(-1).query).get("bbox");
  const body = await (await request.get(`/ogc/collections/mock-india-districts/items?f=json&limit=1&bbox=${bbox}`)).json();
  expect(shown).toBe(body.numberMatched);
  await shot(page, "u1-06-districts-paginated");
});

test("rapid panning aborts superseded loads and the final view wins", async ({ page }) => {
  const log = watch(page);
  await open(page);
  for (const b of [[74, 17, 84, 26], [86, 20, 92, 27], [72, 8, 80, 14], [76, 20, 79, 23]]) {
    await page.evaluate((bb) => window.map.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { duration: 0 }), b);
    await page.waitForTimeout(60);
  }
  await settle(page);
  await page.waitForTimeout(600);
  const inView = await page.evaluate(() => {
    const b = window.map.getBounds();
    const feats = window.map.getSource("v-mock-india-districts")._data.features || [];
    return { n: feats.length, ok: feats.every((f) => { const r = f.geometry.coordinates[0]; return r.some(([x, y]) => x >= b.getWest() - 0.5 && x <= b.getEast() + 0.5 && y >= b.getSouth() - 0.5 && y <= b.getNorth() + 0.5); }) };
  });
  expect(inView.n).toBeGreaterThan(0);
  expect(inView.ok).toBeTruthy();
  expect(log.console).toEqual([]);
});

test("place search finds a town by prefix and selects it as the AOI", async ({ page }) => {
  await open(page);
  await page.fill("#place-search", "Jai");
  const option = page.locator("#place-results").getByRole("option", { name: /Jaipur/ });
  await expect(option).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#aoi-label")).toHaveText("Jaipur");
  await expect.poll(() => page.evaluate(() => window.map.getZoom()), { timeout: 10_000 }).toBeGreaterThan(9);
  await settle(page);
  await shot(page, "u1-07-search-jaipur");
});
