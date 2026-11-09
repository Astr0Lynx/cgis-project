// Port of tests/e2e/browser/check_viewer.py (Agent 7) — every Phase 2 viewer behaviour must survive.
// Runs against the Phase-2-shaped mock (:8098) and, with CGIS_BASE_URL, against a real seeded stack.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import { watch, open, settle, shot, drawRect } from "./helpers.js";

test("Phase 2 viewer regression: discovery, tiles, Town A AOI, zonal 100/200, clip download, vendor switch", async ({ page }) => {
  const log = watch(page);
  await open(page);
  await shot(page, "legacy-01-phase2-fixtures");

  const rasters = await page.$$eval("#raster option", (opts) => opts.map((o) => o.value));
  expect(rasters.length).toBeGreaterThanOrEqual(2);
  await expect(page.locator("#vectors")).toContainText("2 features");

  const tile = await page.evaluate(() => window.map.getSource("raster").tiles[0]);
  expect(tile).toContain("/ogc/collections/");
  expect(tile).toContain("{z}/{y}/{x}");
  const zoom = await page.evaluate(() => [window.map.getSource("raster").minzoom, window.map.getSource("raster").maxzoom]);
  expect(zoom[1]).toBe(13);

  // The camera follows the data only because nothing national is published (Phase 2 fixtures).
  const box = await page.locator("#map").boundingBox();
  const pt = await page.evaluate(() => window.map.project([78.015, 17.985]));
  await page.mouse.click(box.x + pt.x, box.y + pt.y);
  await expect(page.locator("#aoi-label")).toContainText("Town A");
  await expect(page.locator("#pixel")).toContainText("[1");

  await page.click("#zonal");
  await page.waitForFunction(() => document.getElementById("output").textContent.includes('"count_valid"'));
  const zonal = JSON.parse(await page.locator("#output").textContent()).response;
  expect(zonal.sum).toBe(100);
  expect(zonal.count_valid).toBe(100);
  await expect(page.getByTestId("zonal-hero")).toHaveText("100");

  await page.selectOption("#raster", rasters[rasters.length - 1]);
  await settle(page);
  await page.click("#zonal");
  await page.waitForFunction((id) => document.getElementById("output").textContent.includes(`"dataset_id": "${id}"`), rasters[rasters.length - 1]);
  await page.waitForFunction(() => document.getElementById("output").textContent.includes('"count_valid"'));
  const zonal2 = JSON.parse(await page.locator("#output").textContent()).response;
  expect(zonal2.sum).toBe(200);

  await page.click("#clip");
  const link = page.getByTestId("download-link");
  await expect(link).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  const file = await download.path();
  const magic = fs.readFileSync(file).subarray(0, 4);
  expect(["II*\0", "MM\0*"]).toContain(magic.toString("latin1"));
  expect(download.suggestedFilename()).toMatch(/^clip_.*\.tif$/);

  await page.check("input[name=source][value=vendor]");
  await settle(page);
  const vendorUrl = await page.evaluate(() => window.map.getSource("raster").tiles[0]);
  expect(vendorUrl).toContain("/tiles/datasets/");
  await shot(page, "legacy-02-after-clip-vendor");

  // Without the application API nothing probes /auth (no 404 noise on the Phase 2 stack).
  expect(log.requests.filter((u) => /\/(auth|admin)\//.test(u))).toEqual([]);
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("NoData-only AOI reports no total instead of zero", async ({ page }) => {
  const log = watch(page);
  await open(page);
  const rasters = await page.$$eval("#raster option", (opts) => opts.map((o) => o.value));
  test.skip(!rasters.some((r) => r.includes("synthetic")), "needs the Phase 2 synthetic fixture (NoData strip)");
  // Entirely inside the 4-cell NoData strip of the synthetic grid, drawn with the rectangle tool.
  await drawRect(page, [78.0005, 17.9965, 78.0035, 17.9995]);
  await expect(page.locator("#aoi-label")).toContainText("drawn rectangle");
  await page.click("#zonal");
  await expect(page.getByTestId("zonal-hero")).toHaveText("No valid cells");
  await expect(page.getByTestId("zonal-result")).toContainText("not zero");
  expect(log.console).toEqual([]);
});
