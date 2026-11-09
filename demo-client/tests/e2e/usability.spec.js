// Usability: 1366x768, small screens, keyboard, accessible names, no console errors, small payloads.
import { test, expect } from "@playwright/test";
import { watch, open, shot, resetMock } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

async function unlabeled(page) {
  return page.evaluate(() => {
    const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
    const name = (el) => {
      if (el.getAttribute("aria-label") || el.getAttribute("aria-labelledby") || el.getAttribute("title")) return true;
      if (el.labels && el.labels.length) return true;
      if (el.closest("label")) return true;
      if ((el.tagName === "BUTTON" || el.tagName === "A") && el.textContent.trim()) return true;
      return false;
    };
    return [...document.querySelectorAll("button, input, select, textarea, a[href]")]
      .filter((el) => visible(el) && el.type !== "hidden" && !name(el))
      .map((el) => el.outerHTML.slice(0, 120));
  });
}

test("1366x768: panel, map and overlays fit without horizontal scrolling; every control is named", async ({ page }) => {
  const log = watch(page);
  await open(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const legend = await page.getByTestId("legend-card").first().boundingBox();
  const timeline = await page.getByTestId("timeline").boundingBox();
  expect(legend.x + legend.width).toBeLessThanOrEqual(timeline.x);   // no overlap
  expect(await unlabeled(page)).toEqual([]);
  for (const tab of ["analysis", "download"]) {
    await page.click(`#tab-btn-${tab}`);
    expect(await unlabeled(page)).toEqual([]);
  }
  expect(log.console).toEqual([]);
});

test("keyboard: skip link, tab arrows, and visible focus", async ({ page }) => {
  await open(page);
  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.locator("#tab-btn-layers").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("#tab-btn-analysis")).toBeFocused();
  await expect(page.locator("#tab-btn-analysis")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#tab-analysis")).toBeVisible();
  const ring = await page.evaluate(() => getComputedStyle(document.activeElement).boxShadow);
  expect(ring).not.toBe("none");
});

test("375 px phone width: stacked layout, no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 760 });
  await open(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  const panel = await page.locator("#panel").boundingBox();
  const mapBox = await page.locator("#map").boundingBox();
  expect(mapBox.y + mapBox.height).toBeLessThanOrEqual(panel.y + 1);
  await shot(page, "usability-375px");
});

test("map state is deterministic: a copied link restores layer, comparison and camera", async ({ page, context }) => {
  await open(page, "#/map?a=mockindia-l1-2006&b=mockindia-l1-2011&mode=swipe&scale=shared&src=vendor&c=77.2000,28.6000,8.00");
  await expect(page.locator("#raster")).toHaveValue("mockindia-l1-2006");
  await expect(page.locator("#raster-b")).toHaveValue("mockindia-l1-2011");
  await expect(page.locator("input[name=mode][value=swipe]")).toBeChecked();
  const c = await page.evaluate(() => [window.map.getCenter().lng, window.map.getCenter().lat, window.map.getZoom()]);
  expect(c[0]).toBeCloseTo(77.2, 2);
  expect(c[2]).toBeCloseTo(8, 1);
  const other = await context.newPage();
  await other.goto(page.url());
  await other.waitForFunction(() => document.getElementById("output").textContent.includes("collections"));
  await expect(other.locator("#raster")).toHaveValue("mockindia-l1-2006");
});

test("nominal session downloads no large GeoJSON: boundary payloads stay bounded", async ({ page }) => {
  let biggest = 0;
  page.on("response", async (r) => {
    if (r.url().includes("/items?")) {
      const len = (await r.body().catch(() => Buffer.alloc(0))).length;
      biggest = Math.max(biggest, len);
    }
  });
  await open(page);
  await page.evaluate(() => window.map.fitBounds([[74, 17], [84, 26]], { duration: 0 }));
  await page.waitForTimeout(3000);
  expect(biggest).toBeGreaterThan(0);
  expect(biggest).toBeLessThan(600_000);   // one 500-feature page, not a country
});

test("discovery failure shows a recoverable error state", async ({ page }) => {
  await page.route("**/ogc/collections?f=json", (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"error":{"code":"CATALOG_UNAVAILABLE","message":"down"}}' }));
  await page.goto("/viewer/");
  await expect(page.locator("#map-empty")).toContainText("The map cannot load its layers");
  await expect(page.locator("#catalog")).toContainText("Could not reach the OGC API");
  await page.unroute("**/ogc/collections?f=json");
  await page.locator("#map-empty").getByRole("button", { name: "Try again" }).click();
  await expect(page.locator("#map-empty")).toBeHidden();
  await expect(page.getByTestId("series-card").first()).toBeVisible();
});
