// U3 downloads: full vs clip, dataset/version, AOI, output CRS, format, progress/cancel, file validity.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import { decodeGeoTiff } from "../mock-gateway/encoders.mjs";
import { open, shot, resetMock, login, USER, watch } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

async function selectTown(page, name) {
  await page.fill("#place-search", name);
  await expect(page.locator("#place-results").getByRole("option", { name: new RegExp(name) })).toBeVisible();
  await page.keyboard.press("Enter");
}

test("signed out: the download tab asks for sign-in instead of failing", async ({ page }) => {
  await open(page);
  await page.click("#tab-btn-download");
  await expect(page.getByTestId("export-panel")).toContainText("Sign in to export");
});

test("user: full GeoTIFF export with progress, size, provenance, and a valid georeferenced file", async ({ page }) => {
  const log = watch(page);
  await open(page);
  await login(page, USER);
  await page.click("#tab-btn-download");
  await expect(page.locator("#exp-dataset")).toHaveValue("mockindia-l1-2001");
  await expect(page.locator("input[name=exp-kind][value=clip]")).toBeDisabled();   // no AOI yet
  await page.selectOption("#exp-format", "GTiff");
  await page.click("#exp-submit");
  await expect(page.getByTestId("export-job")).toBeVisible();
  await expect(page.locator("#export-job [role=progressbar]")).toBeVisible();
  const result = page.getByTestId("export-result");
  await expect(result).toContainText("ready", { timeout: 20_000 });
  await expect(result).toContainText(/\d+(\.\d)? (KB|MB)/);
  await expect(result).toContainText("mockindia-l1-2001 v1");
  await expect(result).toContainText("EPSG:4326");
  await shot(page, "u3-07-export-full-ready");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-download").click()]);
  const tif = decodeGeoTiff(fs.readFileSync(await download.path()));
  expect(tif.epsg).toBe(4326);
  expect(tif.nodata).toBe(-99999);
  expect(tif.width).toBeGreaterThan(10);
  expect(tif.originX).toBeCloseTo(68.18, 1);
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("user: clipped COG export reprojected to EPSG:3857 carries a resampling warning", async ({ page }) => {
  await open(page);
  await login(page, USER);
  await selectTown(page, "Jaipur");
  await page.click("#tab-btn-download");
  await page.check("input[name=exp-kind][value=clip]");
  await page.selectOption("#exp-crs", "EPSG:3857");
  await expect(page.locator("#exp-reproject-note")).toContainText("no longer sum to the original totals");
  await page.selectOption("#exp-format", "COG");
  await page.click("#exp-submit");
  const result = page.getByTestId("export-result");
  await expect(result).toContainText("EPSG:3857", { timeout: 20_000 });
  await expect(result).toContainText("totals not preserved");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-download").click()]);
  const tif = decodeGeoTiff(fs.readFileSync(await download.path()));
  expect(tif.epsg).toBe(3857);
  expect(tif.originX).toBeGreaterThan(8.4e6);    // Jaipur in Web Mercator metres
  await shot(page, "u3-08-export-clip-3857");
});

test("vector dataset exports as GeoJSON clipped to the AOI", async ({ page }) => {
  await open(page);
  await login(page, USER);
  await selectTown(page, "Pune");
  await page.click("#tab-btn-download");
  await page.selectOption("#exp-dataset", "mock-india-districts");
  await expect(page.locator("#exp-format option")).toHaveText(["GeoJSON (GeoJSON)"]);
  await page.check("input[name=exp-kind][value=clip]");
  await page.click("#exp-submit");
  await expect(page.getByTestId("export-result")).toContainText("ready", { timeout: 20_000 });
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-download").click()]);
  const fc = JSON.parse(fs.readFileSync(await download.path(), "utf8"));
  expect(fc.type).toBe("FeatureCollection");
  expect(fc.features.length).toBeGreaterThan(0);
  expect(fc.features.length).toBeLessThan(50);
});

test("invalid custom CRS is caught in the form; server-side CRS refusal is shown with its code", async ({ page }) => {
  await open(page);
  await login(page, USER);
  await page.click("#tab-btn-download");
  await page.selectOption("#exp-crs", "custom");
  await page.fill("#exp-crs-custom", "file:///etc/passwd");
  await page.click("#exp-submit");
  await expect(page.locator("#exp-errors")).toContainText("Enter an EPSG code");
  await page.fill("#exp-crs-custom", "EPSG:32644");
  await page.click("#exp-submit");
  await expect(page.getByTestId("export-result")).toContainText("UNSUPPORTED_CRS", { timeout: 20_000 });
});

test("a running export can be cancelled", async ({ page, request }) => {
  await request.get("/__mock/delay?ms=1500");
  await open(page);
  await login(page, USER);
  await page.click("#tab-btn-download");
  await page.click("#exp-submit");
  await page.locator("#exp-cancel").click();
  await expect(page.getByTestId("export-result")).toContainText("cancelled");
  await request.get("/__mock/delay?ms=0");
});
