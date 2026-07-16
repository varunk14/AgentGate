import { defineConfig } from "@playwright/test";

// The e2e gate boots the REAL stack (D41): uvicorn serving the actual gate
// (in-memory duplicate store, CORS granted to the test origin only) plus the
// Next dev server. Deterministic end to end — structured samples, no LLM.
// 127.0.0.1 everywhere: localhost would be a DIFFERENT origin for CORS (D40).

const FRONTEND_PORT = 3987;
const BACKEND_PORT = 8731;
const FRONTEND_ORIGIN = `http://127.0.0.1:${FRONTEND_PORT}`;
const BACKEND_ORIGIN = `http://127.0.0.1:${BACKEND_PORT}`;

// Local runs use the repo venv; CI overrides with a plain interpreter.
const BACKEND_COMMAND =
  process.env.E2E_BACKEND_COMMAND ??
  `../.venv/bin/python -m uvicorn app.main:app --port ${BACKEND_PORT}`;

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: FRONTEND_ORIGIN,
  },
  webServer: [
    {
      command: BACKEND_COMMAND,
      cwd: "../backend",
      url: `${BACKEND_ORIGIN}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      env: {
        AGENTGATE_CORS_ORIGINS: FRONTEND_ORIGIN,
      },
    },
    {
      command: `npx next dev --port ${FRONTEND_PORT}`,
      url: FRONTEND_ORIGIN,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_AGENTGATE_API: BACKEND_ORIGIN,
      },
    },
  ],
});
