import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

/**
 * Stage-ready notifications. Gate approval never auto-runs the next
 * stage (pull-based control); this bell provides the push-based awareness —
 * the GateController writes a durable notification per newly-ready stage the
 * moment a level's last gate is approved, and clicking one deep-links straight
 * to that stage in the workspace.
 */

interface AppNotification {
  id: string;
  projectId: string;
  phase: number | null;
  kind: 'stage_ready' | 'project_completed';
  title: string;
  body: string;
  read: boolean;
  createdAt: string;
}

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationBell({
  projectId,
  onGoToStage,
}: {
  projectId: string;
  onGoToStage: (seq: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ['notifications', projectId],
    queryFn: () => api.get<{ notifications: AppNotification[] }>(`/api/projects/${projectId}/notifications`),
    refetchInterval: 10_000,
  });

  const markRead = useMutation({
    mutationFn: (id: string) => api.post(`/api/projects/${projectId}/notifications/${id}/read`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['notifications', projectId] }),
  });

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const items = q.data?.notifications ?? [];
  const unread = items.filter((n) => !n.read);

  const openItem = (n: AppNotification) => {
    if (!n.read) markRead.mutate(n.id);
    if (n.phase != null) onGoToStage(n.phase);
    setOpen(false);
  };

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={`relative rounded-lg border px-2.5 py-1 text-xs font-semibold ${
          open
            ? 'border-brand-300 bg-brand-50 text-brand-700'
            : 'border-slate-200 text-slate-600 hover:border-brand-300 hover:text-brand-700'
        }`}
        title="Notifications — stages ready to run"
      >
        🔔
        {unread.length > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {unread.length > 9 ? '9+' : unread.length}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-40 w-80 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
            <span className="text-xs font-bold uppercase tracking-wide text-slate-500">Notifications</span>
            {unread.length > 0 && (
              <button
                onClick={() => unread.forEach((n) => markRead.mutate(n.id))}
                className="text-[11px] font-semibold text-brand-600 hover:text-brand-700"
              >
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-80 overflow-auto">
            {items.length === 0 && (
              <div className="px-3 py-6 text-center text-xs text-slate-400">Nothing yet — approvals that unlock a stage will show here.</div>
            )}
            {items.map((n) => (
              <button
                key={n.id}
                onClick={() => openItem(n)}
                className={`block w-full border-b border-slate-50 px-3 py-2.5 text-left hover:bg-slate-50 ${
                  n.read ? 'opacity-60' : ''
                }`}
              >
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 text-sm">{n.kind === 'project_completed' ? '🎉' : '🟢'}</span>
                  <div className="min-w-0">
                    <div className="truncate text-xs font-semibold text-slate-800">{n.title}</div>
                    {n.body && <div className="mt-0.5 line-clamp-2 text-[11px] text-slate-500">{n.body}</div>}
                    <div className="mt-0.5 text-[10px] text-slate-400">
                      {timeAgo(n.createdAt)}
                      {n.phase != null && ' · click to open the stage'}
                    </div>
                  </div>
                  {!n.read && <span className="ml-auto mt-1 h-2 w-2 shrink-0 rounded-full bg-brand-500" />}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
