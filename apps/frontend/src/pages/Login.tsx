import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { AuthConfig, User } from '../api/types';
import { useApp } from '../store';

const DEMO_USERS = [
  ['superadmin@sdlc.local', 'Super Admin — platform'],
  ['pm@sdlc.local', 'Project Manager — projects & teams'],
  ['po@sdlc.local', 'Product Owner — Phase 1'],
  ['sa@sdlc.local', 'Solution Architect — Phase 2'],
  ['ta@sdlc.local', 'Technical Architect — Phase 3'],
  ['qa@sdlc.local', 'QA Lead — Phase 4'],
  ['devops@sdlc.local', 'DevOps Engineer — Phase 5'],
  ['dev@sdlc.local', 'Developer — Phase 6'],
] as const;

export default function Login() {
  const setUser = useApp((s) => s.setUser);
  const authConfig = useQuery({
    queryKey: ['auth-config'],
    queryFn: () => api.get<AuthConfig>('/api/auth/config'),
    staleTime: Infinity,
  });
  const [email, setEmail] = useState('superadmin@sdlc.local');
  const [password, setPassword] = useState('Password123!');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const res = await api.post<{ user: User }>('/api/auth/login', { email, password });
      setUser(res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-gradient-to-br from-brand-900 via-slate-900 to-slate-950 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
        <div className="mb-6 text-center">
          <div className="text-2xl font-bold text-brand-700">AI-SDLC Platform</div>
          <div className="mt-1 text-sm text-slate-500">
            Agentic six-phase delivery pipeline with human gates
          </div>
        </div>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Email</label>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              autoComplete="username"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">Password</label>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="current-password"
              required
            />
          </div>
          {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          <button
            disabled={busy}
            className="w-full rounded-lg bg-brand-600 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        {authConfig.data?.mode === 'keycloak' && authConfig.data.ssoLoginUrl && (
          <div className="mt-3">
            <a
              href={authConfig.data.ssoLoginUrl}
              className="block w-full rounded-lg border border-brand-200 bg-brand-50 py-2.5 text-center text-sm font-semibold text-brand-700 hover:bg-brand-100"
            >
              🔐 Sign in with SSO (Keycloak)
            </a>
            <div className="mt-1 text-center text-[11px] text-slate-400">
              Identity &amp; roles managed in Keycloak · both options use the same accounts
            </div>
          </div>
        )}
        <div className="mt-6 border-t border-slate-100 pt-4">
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Demo accounts (password: Password123!)
          </div>
          <div className="grid grid-cols-2 gap-1">
            {DEMO_USERS.map(([mail, label]) => (
              <button
                key={mail}
                type="button"
                onClick={() => setEmail(mail)}
                className="rounded px-2 py-1 text-left text-xs text-slate-600 hover:bg-brand-50 hover:text-brand-700"
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
