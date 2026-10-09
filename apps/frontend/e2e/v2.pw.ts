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

    // each artefact says how it was made: the agent, its model, the context it was given
    await page.getByTestId('v2-stagechat').getByRole('button').filter({ hasText: 'Open ↗' }).first().click();
    const how = page.getByTestId('v2-artefact-run');
    await expect(how).toBeVisible();
    await how.getByRole('button').first().click();
    await expect(how.getByLabel('Context this agent was given')).toContainText('instructions');
    await how.getByText('Show the prompt').click();
    await expect(page.getByTestId('v2-run-prompt')).toContainText('SYSTEM');
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
  test('opens the seven tabs, each with an explainer', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-open-project-panel').click();
    await expect(page.getByTestId('v2-project-panel')).toBeVisible();
    for (const [tab, word] of [['team', 'Team.'], ['artefacts', 'Artefacts.'], ['files', 'Files.'], ['codebase', 'Codebase.'], ['memory', 'Memory.'], ['audit', 'Audit.'], ['skills', 'Skills.']] as const) {
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

test.describe('memory', () => {
  test('a memory is added, reaches the stage context and can be archived', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { id, name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-open-project-panel').click();
    await page.getByTestId('v2-ptab-memory').click();
    await expect(page.getByTestId('v2-memory-tab')).toBeVisible();
    await expect(page.getByTestId('v2-memory-active')).toContainText('Nothing remembered yet');

    await page.getByTestId('v2-memory-add').click();
    await page.getByLabel('Title').fill('Queues use SQS FIFO');
    await page.getByLabel('Memory', { exact: true }).fill('Name them <env>-<service>-q');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByTestId('v2-memory-card').first()).toContainText('Queues use SQS FIFO');

    // it is now a layer of what the stage knows
    const ctx = await page.request.get(`/api/projects/${id}/phase/1/context`);
    const layers = ((await ctx.json()) as { preview: { layers: Array<{ id: string; items: unknown[] }> } }).preview.layers;
    expect(layers.find((l) => l.id === 'memory')?.items).toHaveLength(1);

    await page.getByRole('button', { name: 'Archive' }).first().click();
    await expect(page.getByText('Archived · 1')).toBeVisible();
  });
});

test.describe('connections', () => {
  test('each project has its own Git, Jira, Confluence and knowledge-base settings, with a test for each', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { id, name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-nav-connections').click();
    await expect(page.getByTestId('v2-connections')).toBeVisible();
    for (const k of ['github', 'jira', 'confluence', 'kb']) await expect(page.getByTestId(`v2-conn-${k}`)).toBeVisible();
    await expect(page.getByTestId('v2-conn-status-github')).toContainText('Not set up');

    // a repository without a token of its own keeps using the shared connection, and the test says so
    await page.getByTestId('v2-conn-github-repo').fill('acme/payments');
    await page.getByTestId('v2-conn-save-github').click();
    await expect(page.getByTestId('v2-conn-status-github')).toContainText('Using the shared connection');
    await page.getByTestId('v2-conn-test-github').click();
    await expect(page.getByTestId('v2-conn-result-github')).toContainText('Own access token');

    // a token is saved but never shown again
    await page.getByTestId('v2-conn-github-secret').fill('ghp_not_a_real_token');
    await page.getByTestId('v2-conn-save-github').click();
    await expect(page.getByTestId('v2-conn-github-secret')).toHaveAttribute('placeholder', /saved/);
    expect(await page.content()).not.toContain('ghp_not_a_real_token');
    const api = await page.request.get(`/api/projects/${id}/connections`);
    expect(JSON.stringify(await api.json())).not.toContain('ghp_not_a_real_token');

    // the repository shows up as the project's publishing target
    const detail = await page.request.get(`/api/projects/${id}`);
    expect(JSON.stringify(await detail.json())).toContain('acme/payments');

    // the knowledge base can be tested and narrowed
    await page.getByTestId('v2-conn-kb-codebase').uncheck();
    await page.getByTestId('v2-conn-save-kb').click();
    await page.getByTestId('v2-conn-test-kb').click();
    await expect(page.getByTestId('v2-conn-result-kb')).toContainText('Organisation standards');
    await expect(page.getByTestId('v2-conn-result-kb')).toContainText('switched off');
  });
});

test.describe('portfolio and pipeline chips', () => {
  test('All projects is a table with progress and Open, and the pipeline flags stages that get a security review', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await page.getByText('All projects', { exact: true }).first().click();
    await expect(page.getByTestId('v2-portfolio')).toBeVisible();
    const row = page.getByTestId('v2-portfolio-row').filter({ hasText: name });
    await expect(row).toContainText('Not started');
    await expect(row).toContainText('0 of');
    await row.getByRole('button', { name: 'Open' }).click();
    await expect(page.getByTestId('v2-stage-1')).toBeVisible();
    await page.getByText('Pipeline', { exact: true }).first().click();
    await expect(page.getByTestId('v2-pipe-security-2')).toContainText('Security');
    await expect(page.getByTestId('v2-pipe-security-1')).toHaveCount(0);
  });
});

test.describe('agents', () => {
  test('Governance lists every agent with the file it lives in and its instructions', async ({ page }) => {
    await login(page, '/?ui=v2');
    await page.getByTestId('v2-nav-governance').click();
    await page.getByTestId('v2-gov-agents').click();
    const cat = page.getByTestId('v2-agent-catalog');
    await expect(cat).toBeVisible();
    await expect(cat.getByRole('button', { name: 'Specialist (29)', exact: true })).toBeVisible();
    await cat.getByRole('button', { name: 'Proposed (3)', exact: true }).click();
    await expect(page.getByTestId('v2-agent-row')).toHaveCount(3);
    await cat.getByRole('button', { name: 'All (50)', exact: true }).click();
    await page.getByTestId('v2-agent-row').filter({ hasText: 'prd' }).first().click();
    await expect(cat).toContainText('agents/generators/stage-1-requirements/prd.md');
    await expect(cat).toContainText('Product Requirements Document');
    await expect(page.getByTestId('v2-agent-tools').first()).toContainText('confluence_publish_prd');
    await page.getByTestId('v2-agent-row').filter({ hasText: 'clarifier' }).first().click();
    await expect(cat).toContainText('clarify.system');
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
    // it opens on something: the archive's name, who uploaded it, and the first file already shown with Copy / Download
    await expect(page.getByTestId('v2-codebase-title')).toHaveText('app.zip');
    await expect(page.getByTestId('v2-codebase-sub')).toContainText('3 files indexed · uploaded');
    await expect(page.getByTestId('v2-codebase-copy')).toBeVisible();
    // a long path is shortened in the middle and the whole path is on hover
    const pathEl = page.getByTestId('v2-splitpair').locator('[data-full="src/main/App.java"]');
    await expect(pathEl).toHaveAttribute('title', 'src/main/App.java');
    await expect(page.getByTestId('v2-codebase-tab')).toContainText('class App');
    const left = page.getByTestId('v2-split-left');
    await expect(left).toBeVisible();
    const w0 = (await left.boundingBox())!.width;
    const sp = (await page.getByTestId('v2-split-inner').boundingBox())!;
    await page.mouse.move(sp.x + sp.width / 2, sp.y + 30); await page.mouse.down(); await page.mouse.move(sp.x + 60, sp.y + 30, { steps: 6 }); await page.mouse.up();
    const w1 = (await left.boundingBox())!.width;
    expect(w1).toBeGreaterThan(w0 + 40);
    await page.getByTestId('v2-split-inner').dblclick();
    // a wider pane gives the tree more room, so names show in full
    const narrow = (await left.boundingBox())!.width;
    await page.getByTestId('v2-pane-full').click();
    await expect.poll(async () => (await left.boundingBox())!.width).toBeGreaterThan(narrow);
    await expect(page.getByTestId('v2-code-tree').locator('[data-full="Util.java"]')).toHaveText('Util.java');
    await page.getByTestId('v2-pane-full').click();
    expect((await left.boundingBox())!.width).toBeCloseTo(300, -1);
    page.once('dialog', (d) => void d.accept());
    await page.getByTestId('v2-codebase-remove').click();
    await expect(page.getByText('No codebase uploaded')).toBeVisible();
  });
});

test.describe('code assistant', () => {
  test('select a file, ask for a change, review the diff, apply it, then undo', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { id, name } = await newProject(page);
    const up = await page.request.post(`/api/projects/${id}/codebase`, { multipart: { file: { name: 'app.zip', mimeType: 'application/zip', buffer: makeZip({ 'app/service.py': 'def total(items):\n    return sum(items)\n', 'app/util.py': 'X = 1\n' }) } } });
    expect(up.ok()).toBeTruthy();
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-open-project-panel').click();
    await page.getByTestId('v2-ptab-codebase').click();
    const box = page.getByTestId('code-assistant');
    await expect(box).toBeVisible();
    // tick one file: the request is limited to it
    await page.locator('[data-check="app/service.py"]').check();
    await expect(page.getByTestId('assistant-targets')).toContainText('app/service.py');
    await expect(page.getByTestId('assistant-send')).toBeDisabled();
    await page.getByTestId('assistant-input').fill('Make total ignore None values');
    await page.getByTestId('assistant-send').click();
    // it works in steps, then summarises, and the change arrives as a diff nothing has applied yet
    await expect(page.getByTestId('assistant-step').first()).toBeVisible();
    await expect(page.getByTestId('assistant-summary')).toBeVisible({ timeout: 30_000 });
    const changes = page.getByTestId('assistant-changes');
    await expect(changes).toContainText('1 file changed');
    await changes.getByRole('button', { name: /app\/service\.py/ }).click();
    await expect(page.getByTestId('diff-view')).toContainText('+# DevMind: Make total ignore None values');
    const fileOnServer = async () => {
      const list = (await (await page.request.get(`/api/projects/${id}/codebase`)).json()) as { files: Array<{ id: string; path: string }> };
      const f = list.files.find((x) => x.path === 'app/service.py')!;
      return ((await (await page.request.get(`/api/projects/${id}/codebase/${f.id}`)).json()) as { file: { content: string } }).file.content;
    };
    expect(await fileOnServer()).not.toContain('DevMind');
    await page.getByTestId('assistant-apply').click();
    await expect(page.getByTestId('assistant-applied')).toBeVisible();
    expect(await fileOnServer()).toContain('# DevMind: Make total ignore None values');
    await page.getByTestId('assistant-undo').click();
    await expect(page.getByTestId('assistant-applied')).toBeHidden();
    expect(await fileOnServer()).not.toContain('DevMind');
  });
});

test.describe('context graph', () => {
  test('"What this stage knows" is a layered hierarchy with the earlier stages chained, not a hub', async ({ page }) => {
    await login(page, '/?ui=v2');
    const { name } = await newProject(page);
    await page.reload();
    await openProject(page, name);
    await page.getByTestId('v2-stage-3').click();                      // stage 3 builds on stages 1 and 2, whatever their state
    await page.getByTestId('v2-nav-stage-context').click();
    await page.getByRole('button', { name: 'Open full view' }).click();
    const svg = page.getByTestId('context-graph');
    await expect(svg).toBeVisible();
    // the stages it builds on are boxes of their own, in dependency order
    await expect(svg.locator('[data-group="group:stage:1"]')).toBeVisible();
    await expect(svg.locator('[data-group="group:stage:2"]')).toBeVisible();
    const x = async (id: string) => (await svg.locator(`[data-group="${id}"]`).boundingBox())!.x;
    expect(await x('group:stage:1')).toBeLessThan(await x('group:stage:2'));
    // stage -> stage links exist (the hierarchy), alongside the links into the prompt
    await expect(svg.locator('[data-edge][data-kind="depends"]').first()).toBeAttached();
    await expect(svg.locator('[data-edge][data-kind="feeds"]').first()).toBeAttached();
    // no spoke from every single item straight to the prompt
    const itemSpokes = await svg.locator('[data-edge^="u"][data-edge$="->prompt"], [data-edge*=":"][data-edge$="->prompt"]:not([data-edge^="group:"])').count();
    expect(itemSpokes).toBe(0);
    // selecting a box shows what it holds
    await svg.locator('[data-group="group:stage:2"]').click();
    await expect(page.getByRole('dialog').getByLabel('Details')).toContainText('Solution Architecture');
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
