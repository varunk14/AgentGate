import { test, expect } from "@playwright/test";
import path from "path";

// The slice gate (PRD section 10, Slice 7b): upload/paste a sample and see the
// correct decision, rendered from the wire truth (D39) — against the real
// backend over real CORS (D40/D41).

test("uploading a clean sample file returns an allow decision", async ({ page }) => {
  await page.goto("/");
  await page.setInputFiles(
    '[data-testid="upload"]',
    path.join(__dirname, "fixtures", "clean-request.json"),
  );
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("allow");
  // score is the exact wire string, never a reformatted number (D1/D35)
  await expect(page.getByTestId("score")).toHaveText("1.00");
  // all seven check rows (2 frame + 5 content) render
  await expect(page.locator('[data-testid="checks-table"] tbody tr')).toHaveCount(7);
  await expect(page.getByTestId("trace-id")).not.toBeEmpty();
});

test("a tampered amount blocks with the exact expected total", async ({ page }) => {
  await page.goto("/");
  await page.click('[data-testid="sample-tampered"]');
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("block");
  const reasons = page.getByTestId("reasons");
  await expect(reasons).toContainText("action_amount_matches_total");
  await expect(reasons).toContainText("agent_fixable");
  await expect(reasons).toContainText("1240.00 USD"); // exact wire string
  await expect(reasons).toContainText("proposed_action.amount");
});

test("a reject action escalates with the score shown as not computed", async ({ page }) => {
  await page.goto("/");
  await page.click('[data-testid="sample-reject"]');
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
  // null score means "nothing was measured" — never 0, never blank (D32/D39)
  await expect(page.getByTestId("score")).toHaveText("not computed");
  await expect(page.getByTestId("reasons")).toContainText("action_type_supported");
});

test("an ungrounded total escalates via the raw_text sample", async ({ page }) => {
  await page.goto("/");
  await page.click('[data-testid="sample-ungrounded"]');
  await page.click('[data-testid="verify"]');
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
  await expect(page.getByTestId("reasons")).toContainText("total_not_grounded");
});

test("garbage in the request box renders the gate's fail-closed escalate", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("request-body").fill("this is not json at all");
  await page.click('[data-testid="verify"]');
  // the UI submits verbatim and the GATE fail-closes — no client-side veto (D39)
  await expect(page.getByTestId("decision-banner")).toHaveText("escalate");
  await expect(page.getByTestId("reasons")).toContainText("fail_closed");
});
