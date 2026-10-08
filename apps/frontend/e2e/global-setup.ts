import { request, type FullConfig } from '@playwright/test';
import { mkdirSync } from 'node:fs';

/** Sign in once through the API and share the session, so the suite does not trip the login rate limit. */
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0]!.use.baseURL as string;
  const ctx = await request.newContext({ baseURL });
  const res = await ctx.post('/api/auth/login', {
    data: { email: process.env.E2E_EMAIL ?? 'superadmin@sdlc.local', password: process.env.E2E_PASSWORD ?? 'Password123!' },
  });
  if (!res.ok()) throw new Error(`E2E sign-in failed (${res.status()}): ${await res.text()}`);
  mkdirSync('e2e/.auth', { recursive: true });
  await ctx.storageState({ path: 'e2e/.auth/state.json' });
  await ctx.dispose();
}
