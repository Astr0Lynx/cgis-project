// Area analysis: AOI by boundary/search/draw/view, server zonal stats, year series, errors, progress.
import { test, expect } from "@playwright/test";
import { watch, open, settle, shot, resetMock, fitTo, drawRect, mockRequests } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

test("drawn rectangle -> server zonal statistics with units, NoData rule and pixel-centre semantics", async ({ page, request }) => {
  const log = watch(page);
  await open(page);
  await fitTo(page, [77.0, 28.4, 77.4, 28.8]);
  await drawRect(page, [77.1, 28.5, 77.3, 28.7]);
  await expect(page.locator("#aoi-label")).toHaveText("drawn rectangle");
  await request.get("/__mock/clear-requests");
  await page.click("#zonal");
  const card = page.getByTestId("zonal-result");
  await expect(card).toBeVisible();
  await expect(page.getByTestId("zonal-hero")).toHaveText(/^[\d,]+(\.\d+)?$/);
  await expect(card).toContainText("people · sum of people per cell");
  await expect(card).toContainText("Pixel centre inside area (all_touched=false)");
  await expect(card).toContainText("NoData excluded");
  // The number is the server's: exactly one zonal call, and the UI shows its sum.
  const reqs = (await mockRequests(request)).filter((r) => r.path === "/api/operations/zonal-statistics");
  expect(reqs.length).toBe(1);
  const out = JSON.parse(await page.locator("#output").textContent());
  const shown = Number((await page.getByTestId("zonal-hero").textContent()).replaceAll(",", ""));
  expect(Math.abs(shown - out.response.sum)).toBeLessThan(1);
  await shot(page, "u2-05-zonal-drawn");
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("totals 2001–2011 for an AOI: 11 server calls with progress, chart and table", async ({ page }) => {
  await open(page);
  await page.fill("#place-search", "Madurai");
  await expect(page.locator("#place-results").getByRole("option", { name: /Madurai/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await page.click("#series");
  await expect(page.getByTestId("op-running")).toBeVisible();
  const res = page.getByTestId("series-result");
  await expect(res).toBeVisible({ timeout: 60_000 });
  await expect(res.locator("svg circle.dot")).toHaveCount(11);
  await res.getByRole("button", { name: "Show table" }).click();
  await expect(res.locator("tbody tr")).toHaveCount(11);
  await expect(res).toContainText("not interpolated in the browser");
  await res.locator("rect.hit").nth(4).hover();
  await expect(page.locator(".chart-tip")).toContainText("2005");
  await shot(page, "u2-06-series-2001-2011");
});

test("whole-India view as AOI: the server's size limit is explained, not hidden", async ({ page }) => {
  await open(page);
  await page.click("#tab-btn-analysis");
  await page.click("#aoi-view");
  await page.click("#zonal");
  const err = page.getByTestId("zonal-error");
  await expect(err).toContainText("too large for an interactive operation");
  await expect(err).toContainText("AOI_TOO_LARGE");
  await shot(page, "u2-07-aoi-too-large");
});

test("an operation can be cancelled while running", async ({ page, request }) => {
  await open(page);
  await page.fill("#place-search", "Pune");
  await expect(page.locator("#place-results").getByRole("option", { name: /Pune/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await page.click("#series");
  await page.getByTestId("op-cancel").click();
  await expect(page.locator("#results")).toContainText(/Stopped after \d+ of 11 years|No results|series/);
  await expect(page.locator("#zonal")).toBeEnabled();
  void request;
});

test("buttons are disabled until an AOI exists and explain why", async ({ page }) => {
  await open(page);
  await page.click("#tab-btn-analysis");
  await expect(page.locator("#zonal")).toBeDisabled();
  await expect(page.locator("#clip")).toBeDisabled();
  await expect(page.locator("#ops-hint")).toContainText("Choose an area of interest");
  await page.click("#draw-poly");
  await expect(page.locator("#draw-hint")).toContainText("Click to add vertices");
  await page.keyboard.press("Escape");
  await expect(page.locator("#draw-hint")).toBeHidden();
});
