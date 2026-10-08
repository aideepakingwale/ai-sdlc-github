import { defineConfig } from '@playwright/test';

/**
 * Browser tests for the redesigned workspace. They run against a live stack:
 *   E2E_BASE_URL   where the frontend is served (default http://localhost:3000, the docker-compose port)
 *   E2E_EMAIL / E2E_PASSWORD   a SUPER_ADMIN (default: the seeded superadmin@sdlc.local)
 * Run with:  pnpm --filter @sdlc/frontend e2e
 * Files end in .pw.ts so Vitest does not pick them up.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.pw.ts',
  globalSetup: './e2e/global-setup.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
    storageState: 'e2e/.auth/state.json',
    trace: 'retain-on-failure',
    launchOptions: process.env.E2E_CHROMIUM ? { executablePath: process.env.E2E_CHROMIUM } : {},
  },
});
