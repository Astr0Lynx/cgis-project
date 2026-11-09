// U3: login/logout, admin upload -> validate -> publish -> visible on map, read-only user, 401/403, expiry.
import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { watch, open, settle, shot, resetMock, login, ADMIN, USER } from "./helpers.js";

test.beforeEach(async ({ request }) => { await resetMock(request); });

async function fixtureTiff(request) {
  const buf = await (await request.get("/__mock/geotiff")).body();
  const file = path.join(os.tmpdir(), `cgis-upload-${Date.now()}.tif`);
  fs.writeFileSync(file, buf);
  return file;
}

test("wrong password shows an error; correct login shows role; logout returns to anonymous", async ({ page }) => {
  await open(page);
  await page.click("#login-btn");
  await page.fill("#login-username", "admin");
  await page.fill("#login-password", "not-the-password");
  await page.click("#login-submit");
  await expect(page.locator("#login-error")).toHaveText("Wrong username or password.");
  await page.fill("#login-password", ADMIN.pass);
  await page.click("#login-submit");
  await expect(page.getByTestId("account")).toContainText("Administrator");
  await expect(page.locator("#nav-admin")).toBeVisible();
  await page.click("#logout-btn");
  await expect(page.locator("#login-btn")).toBeVisible();
  await expect(page.locator("#nav-admin")).toBeHidden();
});

test("admin: upload -> validate -> publish -> new layer is listed on the map", async ({ page, request }) => {
  const log = watch(page);
  const file = await fixtureTiff(request);
  await open(page);
  await login(page, ADMIN);
  await page.click("#nav-admin");
  await expect(page.getByTestId("admin-table")).toBeVisible();
  await shot(page, "u3-01-admin-catalog");

  // Client-side validation before anything is sent.
  await page.click("#upl-submit");
  await expect(page.locator("#err-file")).toBeVisible();
  await expect(page.locator("#err-dataset_id")).toBeVisible();

  await page.setInputFiles("#upl-file", file);
  await page.fill("#upl-id", "admin-upload-fixture");
  await page.fill("#upl-title", "Admin upload fixture (synthetic)");
  await page.selectOption("#upl-level", "other");
  await page.fill("#upl-units", "people_per_cell");
  await page.fill("#upl-license", "Test fixture");
  await page.check("#upl-synthetic");
  await page.click("#upl-submit");
  await expect(page.getByTestId("upload-job")).toContainText("upload completed", { timeout: 15_000 });
  await page.getByRole("button", { name: "Review and publish" }).click();
  const versions = page.getByTestId("admin-versions");
  await expect(versions).toContainText("validated");
  await shot(page, "u3-02-admin-validated");
  await page.locator("[data-action=publish]").click();
  await expect(versions.locator(".badge-real")).toContainText("Current", { timeout: 15_000 });
  await expect(versions).toContainText("ready");
  await shot(page, "u3-03-admin-published");

  await page.click("#nav-map");
  await expect(page.locator("#raster option[value=admin-upload-fixture]")).toHaveCount(1, { timeout: 15_000 });
  await page.selectOption("#raster", "admin-upload-fixture");
  await page.evaluate(() => window.map.fitBounds([[77.05, 28.45], [77.55, 28.85]], { duration: 0 }));
  await settle(page);
  await shot(page, "u3-04-published-layer-on-map");
  expect(log.console).toEqual([]);
  expect(log.failed).toEqual([]);
});

test("admin: duplicate version and corrupt file are rejected with clear errors", async ({ page, request }) => {
  await open(page);
  await login(page, ADMIN);
  await page.click("#nav-admin");
  const bad = path.join(os.tmpdir(), "cgis-corrupt.tif");
  fs.writeFileSync(bad, "this is not a tiff");
  await page.setInputFiles("#upl-file", bad);
  await page.fill("#upl-id", "corrupt-fixture");
  await page.fill("#upl-title", "Corrupt");
  await page.fill("#upl-units", "people_per_cell");
  await page.click("#upl-submit");
  await expect(page.getByTestId("upload-job")).toContainText("failed", { timeout: 15_000 });
  await expect(page.getByTestId("upload-job")).toContainText("VALIDATION_FAILED");
  // Same id + version again => 409 VERSION_EXISTS.
  await page.click("#upl-submit");
  await expect(page.locator("#upl-job")).toContainText("already exists");
  void request;
});

test("read-only user: no admin navigation, #/admin explains 403, and the server refuses direct writes", async ({ page }) => {
  await open(page);
  await login(page, USER);
  await expect(page.getByTestId("account")).toContainText("Read-only user");
  await expect(page.locator("#nav-admin")).toBeHidden();
  await page.goto("/viewer/#/admin");
  await expect(page.getByTestId("admin-blocked")).toContainText("Administrator role required (403)");
  await shot(page, "u3-05-user-403");
  // Direct HTTP bypass attempts with the user's own session cookie.
  const status = await page.evaluate(async () => {
    const me = await (await fetch("/auth/me")).json();
    const form = new FormData();
    form.append("file", new Blob(["x"]), "x.tif");
    form.append("metadata", JSON.stringify({ dataset_id: "hack", version: 1, title: "x", kind: "raster", product_level: "other" }));
    const up = await fetch("/admin/datasets/uploads", { method: "POST", body: form, headers: { "X-CSRF-Token": me.csrf_token } });
    const list = await fetch("/admin/datasets");
    const pub = await fetch("/admin/datasets/mockindia-l1-2001/versions/1/publish", { method: "POST", headers: { "X-CSRF-Token": me.csrf_token } });
    return [up.status, list.status, pub.status];
  });
  expect(status).toEqual([403, 403, 403]);
});

test("anonymous direct requests get 401; a signed-in request without the CSRF token is refused", async ({ page, request }) => {
  expect((await request.get("/admin/datasets")).status()).toBe(401);
  expect((await request.post("/api/exports", { data: { dataset_id: "x", kind: "full", format: "GTiff" } })).status()).toBe(401);
  await open(page);
  await login(page, ADMIN);
  const status = await page.evaluate(async () => (await fetch("/admin/datasets/mockindia-l1-2001/versions/1/validate", { method: "POST" })).status);
  expect(status).toBe(403);
});

test("session expiry while signed in: banner, login prompt, and retry after re-authentication", async ({ page, request }) => {
  await open(page);
  await login(page, ADMIN);
  await page.click("#nav-admin");
  await expect(page.getByTestId("admin-table")).toBeVisible();
  await request.get("/__mock/expire-sessions");
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.locator("#banner")).toContainText("session has expired");
  await expect(page.locator("#login-dialog")).toBeVisible();
  await shot(page, "u3-06-session-expired");
  await page.fill("#login-username", ADMIN.user);
  await page.fill("#login-password", ADMIN.pass);
  await page.click("#login-submit");
  await expect(page.locator("#banner")).toBeHidden();
  await expect(page.getByTestId("admin-table")).toBeVisible();
});
