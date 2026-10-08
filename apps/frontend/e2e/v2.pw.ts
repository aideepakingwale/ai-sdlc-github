import { expect, test, type Page } from '@playwright/test';
import { crc32 } from 'node:zlib';

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

/** A tiny stored (uncompressed) zip, so the codebase upload can be tested without a zip library. */
function makeZip(files: Record<string, string>): Buffer {
  const parts: Buffer[] = []; const central: Buffer[] = []; let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const n = Buffer.from(name), d = Buffer.from(text), crc = crc32(d);
    const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt32LE(crc, 14); local.writeUInt32LE(d.length, 18); local.writeUInt32LE(d.length, 22); local.writeUInt16LE(n.length, 26);
    const cen = Buffer.alloc(46); cen.writeUInt32LE(0x02014b50, 0); cen.writeUInt16LE(20, 4); cen.writeUInt16LE(20, 6); cen.writeUInt32LE(crc, 16); cen.writeUInt32LE(d.length, 20); cen.writeUInt32LE(d.length, 24); cen.writeUInt16LE(n.length, 28); cen.writeUInt32LE(offset, 42);
    parts.push(local, n, d); central.push(cen, n); offset += 30 + n.length + d.length;
  }
  const cd = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(Object.keys(files).length, 8); end.writeUInt16LE(Object.keys(files).length, 10); end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, cd, end]);
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
  test('is a conversation: slim header, next step on top, composer pinned at the bottom', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-stage-1').click();
    await expect(page.getByTestId('v2-stagechat')).toBeVisible();
    await expect(page.getByTestId('v2-stage-header')).toBeVisible();
    await expect(page.getByTestId('v2-minimap')).toBeVisible();
    await expect(page.getByTestId('v2-next')).toBeVisible();
    const composer = page.getByTestId('v2-composer');
    await expect(composer).toBeVisible();
    const box = await composer.boundingBox();
    expect(box!.y + box!.height).toBeGreaterThan(780); // pinned to the bottom of the window
    await expect(page.getByTestId('v2-primary')).toHaveText('Review plan');
  });

  test('a stage whose earlier stage is not approved cannot be planned or run', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { id, name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-stage-2').click();
    await expect(page.getByTestId('v2-stagechat')).toHaveAttribute('data-blocked', 'true');
    await expect(page.getByText(/waiting for an earlier one/i)).toBeVisible();
    await expect(page.getByTestId('v2-primary')).toBeDisabled();
    await expect(page.getByTestId('v2-composer').getByRole('textbox').first()).toHaveAttribute('contenteditable', /false|^$/);
    // and the server refuses it too, whatever the browser does
    for (const [method, path, data] of [['post', 'plan/trigger', {}], ['post', 'clarify', { answers: [] }], ['post', 'discuss', { userMessage: 'hi' }]] as const) {
      const res = await page.request[method](`/api/projects/${id}/phase/2/${path}`, { data });
      expect(res.status()).toBeGreaterThanOrEqual(400);
      expect(await res.text()).toContain('waiting for an earlier one');
    }
  });

  test('plan, questions and generation run through the conversation', async ({ page }) => {
    test.setTimeout(150_000);
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-stage-1').click();
    await page.getByTestId('v2-composer').getByRole('textbox').first().click();
    await page.keyboard.type('Build a loyalty points API for a retail bank. Python 3.12 and FastAPI on AWS.');
    await page.getByTestId('v2-primary').click();
    await expect(page.getByTestId('v2-stagechat')).toHaveAttribute('data-mode', 'plan', { timeout: 60_000 });
    await expect(page.getByTestId('plan-card')).toBeVisible();
    await expect(page.getByTestId('v2-primary')).toHaveText('Generate');
    await page.getByTestId('v2-primary').click();
    // the agent may ask questions first: answer them one at a time
    for (let i = 0; i < 4; i++) {
      const q = page.getByTestId('v2-questions');
      if (!(await q.isVisible().catch(() => false))) { await page.waitForTimeout(1500); if (!(await q.isVisible().catch(() => false))) break; }
      await q.locator('button[aria-pressed]').first().click();
      await page.getByTestId('v2-primary').click();
      await page.waitForTimeout(800);
    }
    await expect(page.getByTestId('v2-stagechat')).toHaveAttribute('data-mode', /review|escalated/, { timeout: 90_000 });
    await expect(page.getByTestId('v2-composer')).toBeVisible();
  });
});

test.describe('sidebar', () => {
  test('New project sits outside the project dropdown, and admin pages are reachable', async ({ page }) => {
    await login(page, '/?ui=v2');
    await expect(page.getByTestId('v2-new-project')).toBeVisible();          // visible without opening the switcher
    await page.getByTestId('v2-project-switcher').click();
    await expect(page.getByRole('option', { name: /New project/ })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await page.getByTestId('v2-new-project').click();
    await expect(page.getByRole('dialog').or(page.getByText(/New project/i).first())).toBeVisible();
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
    for (const [nav, crumb] of [['models', 'Model routes'], ['context', 'Project Context'], ['quality', 'Quality'], ['governance', 'Governance'], ['observability', 'Observability'], ['designer', 'Workflow designer']] as const) {
      await page.getByTestId(`v2-nav-${nav}`).click();
      await expect(page.getByTestId('v2-crumb')).toContainText(crumb);
      await expect(page.locator('.fixed.inset-0.z-50')).toHaveCount(0);
    }
    await page.getByTestId('v2-stage-1').click();
    await expect(page.getByTestId('v2-crumb')).toContainText('Stage 1');
  });
});

test.describe('resizing', () => {
  async function drag(page: Page, testid: string, dx: number) {
    const box = (await page.getByTestId(testid).boundingBox())!;
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x + dx / 2, y, { steps: 4 }); await page.mouse.move(x + dx, y, { steps: 4 }); await page.mouse.up();
  }
  test('the sidebar and the details pane can be dragged to a new width, which is remembered', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    const side = page.getByTestId('v2-sidebar');
    const w0 = (await side.boundingBox())!.width;
    await drag(page, 'v2-split-side', 80);
    const w1 = (await side.boundingBox())!.width;
    expect(w1).toBeGreaterThan(w0 + 60);
    await page.getByTestId('v2-open-project-panel').click();
    const pane = page.getByTestId('v2-pane');
    await drag(page, 'v2-split-pane', 150);                  // dragging the divider right narrows the pane
    const p0 = (await pane.boundingBox())!.width;
    expect(p0).toBeLessThan(560);
    await drag(page, 'v2-split-pane', -100);                 // dragging it left widens the pane again
    const p1 = (await pane.boundingBox())!.width;
    expect(p1).toBeGreaterThan(p0 + 70);
    await page.reload();
    await expect(page.getByTestId('v2-workspace')).toBeVisible();
    expect((await page.getByTestId('v2-sidebar').boundingBox())!.width).toBeCloseTo(w1, -1);
    await page.getByTestId('v2-split-side').dblclick();      // double-click resets
    expect((await page.getByTestId('v2-sidebar').boundingBox())!.width).toBeCloseTo(288, -1);
  });

  test('the details pane can go full screen and Escape brings it back', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-open-project-panel').click();
    const pane = page.getByTestId('v2-pane');
    await page.getByTestId('v2-pane-full').click();
    await expect(pane).toHaveAttribute('data-full', 'true');
    const box = (await pane.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(1430);
    expect(box.x).toBeLessThanOrEqual(1);
    await page.keyboard.press('Escape');
    await expect(pane).toHaveAttribute('data-full', 'false');
    expect((await pane.boundingBox())!.width).toBeLessThan(1000);
    await page.getByTestId('v2-pane-full').click();
    await page.getByTestId('v2-pane-full').click();         // the same button leaves full screen
    await expect(pane).toHaveAttribute('data-full', 'false');
  });
});

test.describe('codebase', () => {
  test('the file tree and the file viewer can be resized, and Remove clears the codebase', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { id, name } = await newProject(page);
    const up = await page.request.post(`/api/projects/${id}/codebase`, { multipart: { file: { name: 'app.zip', mimeType: 'application/zip', buffer: makeZip({ 'src/main/App.java': 'class App {}', 'src/main/Util.java': 'class Util {}', 'pom.xml': '<project/>' }) } } });
    expect(up.ok()).toBeTruthy();
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-open-project-panel').click();
    await page.getByTestId('v2-ptab-codebase').click();
    const left = page.getByTestId('v2-split-left');
    await expect(left).toBeVisible();
    const w0 = (await left.boundingBox())!.width;
    const sp = (await page.getByTestId('v2-split-inner').boundingBox())!;
    await page.mouse.move(sp.x + sp.width / 2, sp.y + 30); await page.mouse.down(); await page.mouse.move(sp.x + 60, sp.y + 30, { steps: 6 }); await page.mouse.up();
    const w1 = (await left.boundingBox())!.width;
    expect(w1).toBeGreaterThan(w0 + 40);
    await page.getByTestId('v2-split-inner').dblclick();
    expect((await left.boundingBox())!.width).toBeCloseTo(260, -1);
    page.once('dialog', (d) => void d.accept());
    await page.getByTestId('v2-codebase-remove').click();
    await expect(page.getByText('No codebase uploaded')).toBeVisible();
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
