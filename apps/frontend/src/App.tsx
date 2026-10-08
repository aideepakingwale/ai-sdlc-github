import { useQuery } from '@tanstack/react-query';
import { Navigate, Route, Routes } from 'react-router-dom';
import { api } from './api/client';
import type { User } from './api/types';
import Login from './pages/Login';
import Workspace from './pages/Workspace';
import V2Workspace from './v2/V2Workspace';
import { getUiVersion } from './v2/uiVersion';
import { useApp } from './store';

// Resolved once at load: `?ui=` is read (and remembered) before login redirects drop the query string.
const UI_VERSION = getUiVersion();

export default function App() {
  const { user, setUser } = useApp();

  const me = useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      const res = await api.get<{ user: User }>('/api/auth/me');
      setUser(res.user);
      return res.user;
    },
    retry: false,
  });

  if (me.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-slate-400">
        <div className="animate-pulse text-sm">Loading DevMind…</div>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/*" element={user ? (UI_VERSION === 'v2' ? <V2Workspace /> : <Workspace />) : <Navigate to="/login" replace />} />
    </Routes>
  );
}
