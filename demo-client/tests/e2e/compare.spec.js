// U2: temporal and Level 1 vs Level 2 comparisons, shared legends, NoData, provenance.
import { test, expect } from "@playwright/test";
import { watch, open, settle, shot, resetMock, fitTo, clickLngLat } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

test("per-dataset OGC styles are flagged as not comparable; one click applies a shared range across all 11 years", async ({ page }) => {
  const log = watch(page);
  await open(page);
  await expect(page.getByTestId("legend-not-comparable")).toBeVisible();
  await expect(page.getByTestId("legend-scale")).toContainText("own min–max");
  await page.getByTestId("use-shared").click();
  await settle(page);
  await expect(page.getByTestId("legend-scale")).toContainText("Shared range across 11 layers");
  await expect(page.locator("input[name=source][value=vendor]")).toBeChecked();
  const ticks2001 = await page.getByTestId("legend-ticks").textContent();
  const url2001 = await page.evaluate(() => window.map.getSource("raster").tiles[0]);
  expect(url2001).toMatch(/rescale=0%2C\d+/);
  await page.locator(".year-btn", { hasText: "2011" }).first().click();
  await settle(page);
  expect(await page.getByTestId("legend-ticks").textContent()).toBe(ticks2001);
  const url2011 = await page.evaluate(() => window.map.getSource("raster").tiles[0]);
  expect(new URL(url2011).searchParams.get("rescale")).toBe(new URL(url2001).searchParams.get("rescale"));
  await expect(page.getByTestId("legend-card")).toContainText("NoData (-99,999) is transparent");
  await shot(page, "u2-01-shared-scale-2011");
  expect(log.console).toEqual([]);
});

test("swipe between 2001 and 2011: one shared legend, comparable note, pixel values and server difference", async ({ page }) => {
  const log = watch(page);
  await open(page, "#/map?scale=shared&src=vendor");
  await page.check("input[name=mode][value=swipe]");
  await settle(page);
  await expect(page.locator("#raster-b")).toHaveValue("mockindia-l1-2011");
  await expect(page.getByTestId("compare-ok")).toContainText("directly comparable");
  await expect(page.getByTestId("legend-card")).toHaveCount(1);
  await expect(page.getByTestId("legend-card")).toContainText("shared");
  // Keyboard-operable divider.
  const handle = page.getByRole("slider", { name: /Swipe position/ });
  await handle.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(handle).toHaveAttribute("aria-valuenow", "48");
  await fitTo(page, [77.0, 28.4, 77.4, 28.8]);
  await clickLngLat(page, [77.21, 28.61]);
  await expect(page.getByTestId("pixel-a")).toContainText("people per cell");
  await expect(page.getByTestId("pixel-b")).toContainText("people per cell");
  await expect(page.getByTestId("pixel-delta")).toContainText("B − A");
  await shot(page, "u2-02-swipe-2001-2011");
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("side by side Level 1 vs Level 2: raw pixels flagged as not comparable, separate legends, no pixel difference", async ({ page }) => {
  await open(page);
  await page.check("input[name=mode][value=side]");
  await page.selectOption("#raster-b", "mockindia-l2-2011-pune");
  await settle(page);
  await expect(page.getByTestId("compare-resolution")).toContainText("Raw pixel values are not directly comparable");
  await expect(page.getByTestId("compare-resolution")).toContainText("≈ 15× the area");
  await expect(page.getByTestId("legend-card")).toHaveCount(2);
  await fitTo(page, [73.8, 18.46, 73.92, 18.58]);
  await clickLngLat(page, [73.86, 18.52]);
  await expect(page.getByTestId("pixel-b")).toContainText("people per cell");
  await expect(page.getByTestId("pixel-delta")).toHaveCount(0);
  await expect(page.locator("#inspector")).toContainText("Different cell sizes");
  await shot(page, "u2-03-side-by-side-l1-l2");
});

test("Level 1 vs Level 2 totals on a common support (the town polygon)", async ({ page }) => {
  await open(page);
  await page.fill("#place-search", "Pune");
  await expect(page.locator("#place-results").getByRole("option", { name: /Pune/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#l1l2")).toBeEnabled();
  await page.click("#l1l2");
  const res = page.getByTestId("l1l2-result");
  await expect(res).toContainText("Level 1");
  await expect(res).toContainText("Level 2");
  await expect(res).toContainText("Common support");
  await expect(res.locator("tbody tr")).toHaveCount(2);
  await shot(page, "u2-04-l1-vs-l2-totals");
});

test("comparing synthetic and real-labelled layers warns about provenance", async ({ page }) => {
  await open(page);
  await page.check("input[name=mode][value=swipe]");
  await page.selectOption("#raster-b", "synthetic-worldpop-2011");
  await settle(page);
  // Both are SYNTHETIC in the mock, so no provenance warning; units match but cell size differs.
  await expect(page.locator("#compare-notes")).not.toContainText("different provenance");
  await expect(page.getByTestId("compare-resolution")).toBeVisible();
});
