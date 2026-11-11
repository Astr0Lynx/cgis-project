// U1: national discovery of all Level 1 years and the Level 2 group, without knowing dataset names.
import { test, expect } from "@playwright/test";
import { watch, open, settle, shot, resetMock, mockRequests } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

test("initial view fits India and the 11-year Level 1 series is discovered from metadata", async ({ page }) => {
  const log = watch(page);
  await open(page);
  const bounds = await page.evaluate(() => window.map.getBounds().toArray());
  expect(bounds[0][0]).toBeLessThan(69);   // west of Gujarat
  expect(bounds[1][0]).toBeGreaterThan(97); // east of Arunachal
  expect(bounds[0][1]).toBeLessThan(7.5);
  expect(bounds[1][1]).toBeGreaterThan(35);

  const card = page.getByTestId("series-card").filter({ hasText: "Level 1" });
  await expect(card).toHaveCount(1);
  const years = await card.locator(".year-btn").allTextContents();
  expect(years).toEqual(["2001", "2002", "2003", "2004", "2005", "2006", "2007", "2008", "2009", "2010", "2011"]);
  await expect(card).toContainText("11 years 2001–2011");
  await expect(page.getByTestId("timeline")).toBeVisible();
  await expect(page.getByTestId("timeline-year")).toHaveText("2001");
  await expect(page.getByTestId("provenance-badge")).toContainText("SYNTHETIC");
  await shot(page, "u1-01-india-level1-2001");
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("changing the year swaps the tile requests to that year's collection", async ({ page, request }) => {
  await open(page);
  await request.get("/__mock/clear-requests");
  await page.locator(".year-btn", { hasText: "2007" }).first().click();
  await settle(page);
  await expect(page.getByTestId("timeline-year")).toHaveText("2007");
  const tile = await page.evaluate(() => window.map.getSource("raster").tiles[0]);
  expect(tile).toContain("/collections/mockindia-l1-2007/tiles/");
  const reqs = await mockRequests(request);
  expect(reqs.some((r) => r.path.startsWith("/ogc/collections/mockindia-l1-2007/tiles/WebMercatorQuad/"))).toBeTruthy();
  // Keyboard: the year slider moves one year per arrow press.
  await page.getByTestId("year-slider").focus();
  await page.keyboard.press("ArrowRight");
  await settle(page);
  await expect(page.getByTestId("timeline-year")).toHaveText("2008");
  await shot(page, "u1-02-year-2008");
});

test("rapid year switching ends on the last choice with no stale layer or leaked state", async ({ page, request }) => {
  const log = watch(page);
  await open(page);
  await request.get("/__mock/delay?ms=0");
  for (const y of ["2003", "2009", "2004", "2011", "2002", "2010"]) await page.locator(".year-btn", { hasText: y }).first().click();
  await settle(page);
  await expect(page.getByTestId("timeline-year")).toHaveText("2010");
  expect(await page.evaluate(() => window.map.getSource("raster").tiles[0])).toContain("mockindia-l1-2010");
  expect(await page.evaluate(() => window.map.getStyle().layers.filter((l) => l.id === "raster").length)).toBe(1);
  expect(page.url()).toContain("a=mockindia-l1-2010");
  await expect(page.locator(".year-btn[aria-pressed=true]")).toHaveText("2010");
  expect(log.console).toEqual([]);
});

test("Level 2 towns are one logical group with overlays, extents and honest resolution note", async ({ page }) => {
  const log = watch(page);
  await open(page);
  const group = page.getByTestId("level2-group");
  await expect(group).toHaveCount(1);
  await expect(group).toContainText("6 towns");
  await expect(group).toContainText("does not create observed detail");
  await page.getByTestId("l2-overlay").check();
  await expect.poll(() => page.evaluate(() => window.map.getStyle().layers.filter((l) => /^l2-\d+$/.test(l.id)).length)).toBe(6);
  await settle(page);
  await expect(page.getByTestId("l2-marker")).toHaveCount(6);
  await expect(page.getByTestId("legend-card").filter({ hasText: "Level 2 overlay" })).toBeVisible();
  await shot(page, "u1-03-level2-overlay-markers");
  await page.getByTestId("l2-marker").filter({ hasText: "Pune" }).click();
  await expect.poll(() => page.evaluate(() => window.map.getZoom()), { timeout: 10_000 }).toBeGreaterThan(10);
  await settle(page);
  await shot(page, "u1-04-level2-pune-over-level1");
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("dataset metadata shows source, version, year, units, CRS, NoData and license", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: /Metadata for MOCK India Level 1 population 2001/ }).click();
  const meta = page.getByTestId("dataset-metadata").first();
  for (const text of ["mockindia-l1-2001", "v1", "2001", "people_per_cell", "EPSG:4326", "-99,999", "MOCK fixture generator", "Level 1"]) {
    await expect(meta).toContainText(text);
  }
  await expect(meta).toContainText("Cell size");
  await expect(meta).toContainText("License");
  await shot(page, "u1-05-metadata");
});

test("facet and text filters narrow the dataset browser", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: /^Level 2 6$/ }).click();
  await expect(page.getByTestId("series-card")).toHaveCount(0);
  await expect(page.getByTestId("level2-group")).toBeVisible();
  await page.getByRole("button", { name: /^All/ }).click();
  await page.fill("#catalog-filter", "jaipur");
  await expect(page.locator("#catalog .ds-item")).toHaveCount(1);
  await page.fill("#catalog-filter", "nothing-matches-this");
  await expect(page.locator("#catalog")).toContainText("No datasets match");
});
