import { test, expect } from "@playwright/test";

// End-to-end smoke against a deployed environment: log in, run a search that
// the seeded corpus answers, and confirm a click is reported. Institutionalises
// the M3 lesson — verify the real deployed stack, not just unit behaviour.

const EMAIL = process.env.SMOKE_EMAIL ?? "";
const PASSWORD = process.env.SMOKE_PASSWORD ?? "";
// A query the staging corpus answers (BRF roof-renovation protocols).
const QUERY = process.env.SMOKE_QUERY ?? "takrenovering";

test("login, search, and report a click", async ({ page }) => {
  test.skip(!EMAIL || !PASSWORD, "SMOKE_EMAIL / SMOKE_PASSWORD not set");

  await page.goto("/login");
  await page.getByLabel(/e-?post|email/i).fill(EMAIL);
  await page.getByLabel(/lösenord|password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /logga in|sign in/i }).click();

  // The session resolver lands a single-org user on their search page.
  await page.waitForURL(/\/search/);

  await page.getByRole("searchbox").fill(QUERY);
  await page.getByRole("button", { name: /^(sök|search)$/i }).click();

  const results = page.locator("main ol > li");
  await expect(results.first()).toBeVisible({ timeout: 20_000 });

  // Clicking a result reports the click before navigating to the document view.
  const clickReported = page.waitForResponse(
    (response) => response.url().includes("/click") && response.request().method() === "POST",
  );
  await results.first().getByRole("link").first().click();
  const response = await clickReported;
  expect(response.status()).toBe(200);
});
