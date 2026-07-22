import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";

test("the home page presents the enterprise product landing", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("landing-page")).toBeVisible();
  await expect(page.getByTestId("hero-demo-cta")).toBeVisible();
  await expect(page.getByTestId("plan-self-host")).toBeVisible();
  await expect(page.getByTestId("home-scenario-acme-inv-001-clean")).toBeVisible();
});

test("a real Acme invoice allows when the agent proposal matches", async ({ page }) => {
  await page.goto("/demo?invoice=acme-inv-001");
  await expect(page.getByTestId("verify")).toBeEnabled();
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
  await expect(page.getByTestId("score")).toHaveText("1.00");
  await expect(page.locator('[data-testid="checks-table"] tbody tr')).toHaveCount(7);
});

test("a real invoice decimal slip blocks then fixes on resubmit", async ({ page }) => {
  await page.goto("/demo?invoice=acme-inv-001&mistake=decimal");
  await expect(page.getByTestId("verify")).toBeEnabled();
  await expect(page.getByTestId("decimal-slip")).toBeChecked();
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("block");
  const reasons = page.getByTestId("reasons");
  await expect(reasons).toContainText("action_amount_matches_total");
  await expect(reasons).toContainText("1240.00 USD");
  await page.click('[data-testid="apply-fix"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
});

test("a reject action on a real invoice escalates", async ({ page }) => {
  await page.goto("/demo?invoice=acme-inv-001");
  await expect(page.getByTestId("verify")).toBeEnabled();
  await page.selectOption('[data-testid="proposal-action-type"]', "reject");
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
  await expect(page.getByTestId("score")).toHaveText("not computed");
});

test("unrelated source text on a real invoice escalates grounding", async ({ page }) => {
  await page.goto("/demo?invoice=acme-inv-001");
  await expect(page.getByTestId("verify")).toBeEnabled();
  await page.click('[data-testid="bad-grounding"]');
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
  await expect(page.getByTestId("reasons")).toContainText("total_not_grounded");
});

test("a real $12,500 invoice escalates on policy ceiling", async ({ page }) => {
  await page.goto("/demo?invoice=northwind-inv-12500");
  await expect(page.getByTestId("verify")).toBeEnabled();
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
});

test("fetch mode uses the live system-of-record record", async ({ page }) => {
  await page.goto("/demo?invoice=fetch-inv-2026-0042");
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
  await expect(page.getByTestId("score")).toHaveText("1.00");
  await expect(page.locator("dd").filter({ hasText: "system_of_record" })).toBeVisible();
});

test("malformed JSON fail-closes to escalate", async ({ page }) => {
  await page.goto("/demo");
  // Wait for the default invoice's async load to settle: when it lands it
  // regenerates the request body and discards any advanced-editor override,
  // so filling before that point loses the fill (CI-speed race).
  await expect(page.getByTestId("verify")).toBeEnabled();
  await expect(page.getByTestId("proposal-amount")).toHaveValue("1240.00");
  await page.getByRole("button", { name: /Show API request JSON/i }).click();
  await page.getByTestId("request-body").fill("this is not json at all");
  await page.click('[data-testid="verify-raw-json"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
  await expect(page.getByTestId("reasons")).toContainText("fail_closed");
});

test("uploading a real invoice file parses and verifies", async ({ page }) => {
  await page.goto("/demo");
  await page.setInputFiles(
    '[data-testid="invoice-upload"]',
    path.join(__dirname, "..", "public", "invoices", "acme-inv-001.txt"),
  );
  await expect(page.getByTestId("verify")).toBeEnabled();
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
});

test("uploading a PDF invoice extracts its text layer, parses, and verifies", async ({ page }) => {
  await page.goto("/demo");
  await page.setInputFiles(
    '[data-testid="invoice-upload"]',
    path.join(__dirname, "fixtures", "brightpath-inv-2026-0788.pdf"),
  );
  await expect(page.getByTestId("verify")).toBeEnabled();
  await expect(page.getByTestId("proposal-amount")).toHaveValue("6200.00");
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
});

test("a Stripe-style PDF invoice (Invoice number / Amount due layout) parses and verifies", async ({ page }) => {
  await page.goto("/demo");
  await page.setInputFiles(
    '[data-testid="invoice-upload"]',
    path.join(__dirname, "fixtures", "meridian-stripe-style.pdf"),
  );
  await expect(page.getByTestId("verify")).toBeEnabled();
  await expect(page.getByTestId("proposal-amount")).toHaveValue("84.00");
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
});

test("pasting viewer-copied Stripe-style invoice text parses and verifies", async ({ page }) => {
  const copied = fs.readFileSync(
    path.join(__dirname, "fixtures", "meridian-viewer-copy.txt"),
    "utf8",
  );
  await page.goto("/demo");
  await page.getByTestId("invoice-paste").fill(copied);
  await page.getByTestId("invoice-paste").blur();
  await expect(page.getByTestId("proposal-amount")).toHaveValue("84.00");
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
});

test("/verify redirects to the live demo", async ({ page }) => {
  await page.goto("/verify?invoice=acme-inv-001");
  await expect(page).toHaveURL(/\/demo\?invoice=acme-inv-001/);
});
