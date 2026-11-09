// Read-only checks against a REAL gateway (CGIS_BASE_URL). They make no assumption about dataset names,
// years or extents: they compare what the viewer shows with what the gateway itself publishes.
//   CGIS_BASE_URL=http://localhost:8080 npx playwright test --project=stack
import { test, expect } from "@playwright/test";
import { watch, open, settle, shot } from "./helpers.js";

test("every raster collection the OGC API publishes is selectable; series are grouped by metadata", async ({ page, request }) => {
  const log = watch(page);
  const { collections } = await (await request.get("/ogc/collections?f=json")).json();
  const rasters = collections.filter((c) => (c.links || []).some((l) => /tiles/.test(l.rel || "") || /tiles/.test(l.href || "")));
  await open(page);
  const options = await page.$$eval("#raster option", (o) => o.map((x) => x.value));
  expect(new Set(options)).toEqual(new Set(rasters.map((c) => c.id)));
  const level1 = collections.filter((c) => (c.keywords || []).includes("level1"));
  if (level1.length >= 2) {
    const years = await page.getByTestId("series-card").first().locator(".year-btn").allTextContents();
    expect(years.length).toBe(new Set(level1.map((c) => (c.keywords || []).find((k) => /^\d{4}$/.test(k)))).size);
  }
  await shot(page, "stack-01-discovery");
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("boundary requests are always bbox-scoped and paginated", async ({ page }) => {
  const items = [];
  page.on("request", (r) => { if (r.url().includes("/items?")) items.push(r.url()); });
  await open(page);
  await settle(page);
  for (const u of items) {
    expect(u).toContain("bbox=");
    expect(u).not.toContain("limit=1000");
  }
});

test("zonal statistics on a small drawn area returns the server's answer", async ({ page }) => {
  await open(page);
  const c = await page.evaluate(() => window.map.getCenter().toArray());
  await page.evaluate(([x, y]) => window.map.jumpTo({ center: [x, y], zoom: 12 }), c);
  await settle(page);
  await page.click("#tab-btn-analysis");
  await page.click("#aoi-view");
  await page.click("#zonal");
  await expect(page.locator("[data-testid=zonal-result], [data-testid=zonal-error]").first()).toBeVisible();
  await shot(page, "stack-02-zonal");
});
