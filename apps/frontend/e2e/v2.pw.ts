import { expect, test, type Page } from '@playwright/test';

const EMAIL = process.env.E2E_EMAIL ?? 'superadmin@sdlc.local';
const PASSWORD = process.env.E2E_PASSWORD ?? 'Password123!';
const created: string[] = [];

/** The session comes from global-setup; this just opens the page. */
async function login(page: Page, start = '/') {
  await page.goto(start);
  await expect(page.getByTestId('v2-workspace')).toBeVisible();
}

/** A fresh project so the tests never depend on what is already in the database. */
async function newProject(page: Page): Promise<{ id: string; name: string }> {
  const name = `E2E v2 ${Date.now()}`;
  const res = await page.request.post('/api/projects', { data: { name } });
  expect(res.ok()).toBeTruthy();
  const id = ((await res.json()) as { project: { id: string } }).project.id;
  created.push(id);
  return { id, name };
}

async function openProject(page: Page, name: string) {
  await page.getByTestId('v2-project-switcher').click();
  await page.getByRole('option', { name: new RegExp(name) }).click();
  await expect(page.getByTestId('v2-stage-1')).toBeVisible();
}

test.afterAll(async ({ browser }) => {
  const page = await (await browser.newContext({ storageState: 'e2e/.auth/state.json' })).newPage();
  for (const id of created) await page.request.delete(`/api/projects/${id}`).catch(() => undefined);
  await page.close();
});

test.describe('sign in', () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test('the sign-in form leads to the new workspace', async ({ page }) => {
    await page.goto('/');
    await page.fill('input[type=email]', EMAIL);
    await page.fill('input[type=password]', PASSWORD);
    await page.click('form button');
    await expect(page.getByTestId('v2-workspace')).toBeVisible();
  });
});

test.describe('which workspace', () => {
  test('the new workspace is the default and classic is a remembered fallback', async ({ page }) => {
    await page.goto('/?ui=classic');
    await expect(page.getByTestId('try-v2')).toBeVisible();
    await page.goto('/');
    await expect(page.getByTestId('try-v2')).toBeVisible(); // classic is remembered
    await page.getByTestId('try-v2').click();
    await expect(page.getByTestId('v2-workspace')).toBeVisible();
    await page.reload();
    await expect(page.getByTestId('v2-workspace')).toBeVisible();
  });

  test('the account menu switches back to classic', async ({ page }) => {
    await login(page, '/?ui=v2');
    await page.getByTestId('v2-user').click();
    await page.getByTestId('v2-back-classic').click();
    await expect(page.getByTestId('try-v2')).toBeVisible();
    await page.goto('/?ui=v2');
  });
});

test.describe('stage view', () => {
  test('shows the conversation with the composer and next step docked', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-stage-1').click();
    const dock = page.getByTestId('v2-dock');
    await expect(dock).toBeVisible();
    await expect(page.getByTestId('v2-composer')).toBeVisible();
    // the dock sits below the scrolling conversation
    const box = await dock.boundingBox();
    expect(box!.y + box!.height).toBeGreaterThan(800);
  });
});

test.describe('project panel', () => {
  test('opens the six tabs, each with an explainer', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-open-project-panel').click();
    await expect(page.getByTestId('v2-project-panel')).toBeVisible();
    for (const [tab, word] of [['team', 'Team.'], ['artefacts', 'Artefacts.'], ['files', 'Files.'], ['codebase', 'Codebase.'], ['audit', 'Audit.'], ['skills', 'Skills.']] as const) {
      await page.getByTestId(`v2-ptab-${tab}`).click();
      await expect(page.getByTestId('v2-explain')).toContainText(word);
    }
    await page.getByTestId('v2-ptab-audit').click();
    await expect(page.getByTestId('v2-audit-tab')).toBeVisible();
    await page.getByTestId('v2-audit-cat-Security').click();
    await expect(page.getByTestId('v2-audit-cat-Security')).toHaveAttribute('aria-pressed', 'true');
    await page.getByTestId('v2-pane-close').click();
    await expect(page.getByTestId('v2-pane')).toHaveCount(0);
  });
});

test.describe('pipeline and configuration pages', () => {
  test('the pipeline page lists every stage and opens one', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-nav-pipeline').click();
    await expect(page.getByTestId('v2-pipeline')).toBeVisible();
    await expect(page.getByTestId('v2-attention')).toBeVisible();
    await page.getByTestId('v2-pipe-stage-1').click();
    await expect(page.getByTestId('v2-composer')).toBeVisible();
  });

  test('configuration screens open as pages in the centre, not as overlays', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    for (const [nav, crumb] of [['context', 'Project Context'], ['quality', 'Quality'], ['governance', 'Governance'], ['observability', 'Observability'], ['designer', 'Workflow designer']] as const) {
      await page.getByTestId(`v2-nav-${nav}`).click();
      await expect(page.getByTestId('v2-crumb')).toContainText(crumb);
      await expect(page.locator('.fixed.inset-0.z-50')).toHaveCount(0);
    }
    await page.getByTestId('v2-stage-1').click();
    await expect(page.getByTestId('v2-crumb')).toContainText('Stage 1');
  });
});

test.describe('theme', () => {
  test('dark is applied from the account menu, persists, and classic stays light', async ({ page }) => {
    await login(page, '/?ui=v2');
    const html = page.locator('html');
    await page.getByTestId('v2-user').click();
    await page.getByTestId('v2-theme-dark').click();
    await expect(html).toHaveClass(/dark/);
    await page.reload();
    await expect(page.getByTestId('v2-workspace')).toBeVisible();
    await expect(html).toHaveClass(/dark/);
    await page.getByTestId('v2-user').click();
    await page.getByTestId('v2-theme-light').click();
    await expect(html).not.toHaveClass(/dark/);
    await page.getByTestId('v2-theme-dark').click();
    await page.goto('/?ui=classic');
    await expect(page.getByTestId('try-v2')).toBeVisible();
    await expect(html).not.toHaveClass(/dark/);
    await page.goto('/?ui=v2');
    await page.getByTestId('v2-user').click();
    await page.getByTestId('v2-theme-light').click();
  });
});

test.describe('narrow screens', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('the sidebar is a drawer, the pane covers the page, and nothing scrolls sideways', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await expect(page.getByTestId('v2-sidebar')).toHaveCount(0);
    await page.getByTestId('v2-side-toggle').click();
    await expect(page.getByTestId('v2-drawer')).toBeVisible();
    await page.getByTestId('v2-project-switcher').click();
    await page.getByRole('option', { name: new RegExp(name) }).click();
    await expect(page.getByTestId('v2-drawer')).toHaveCount(0); // closes when you pick something
    await page.getByTestId('v2-side-toggle').click();
    await expect(page.getByTestId('v2-stage-1')).toBeVisible();
    await page.getByTestId('v2-stage-1').click();
    await expect(page.getByTestId('v2-drawer')).toHaveCount(0);
    await page.getByTestId('v2-open-project-panel').click();
    const pane = await page.getByTestId('v2-pane').boundingBox();
    expect(pane!.width).toBeGreaterThanOrEqual(385);
    await page.getByTestId('v2-pane-close').click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  });
});
