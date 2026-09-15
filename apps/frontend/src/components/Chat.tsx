import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import { streamChat } from '../api/client';
import type { ChatMessage } from '../api/types';
import { useApp, type ActivityItem } from '../store';

const ACTIVITY_ICON: Record<ActivityItem['kind'], string> = {
  node: '⚙️',
  tool: '🔌',
  artifact: '📄',
  gate: '⛔',
};

function ActivityFeed({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mb-2 space-y-0.5 rounded-lg bg-slate-50 p-2">
      {items.map((a) => (
        <div key={a.id} className="flex items-center gap-1.5 text-xs text-slate-500">
          <span>{ACTIVITY_ICON[a.kind]}</span>
          <span className={a.status === 'error' ? 'text-red-600' : a.kind === 'artifact' ? 'text-emerald-700' : ''}>
            {a.label}
          </span>
        </div>
      ))}
    </div>
  );
}

function Bubble({ role, children }: { role: 'user' | 'assistant' | 'system'; children: React.ReactNode }) {
  const isUser = role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm shadow-sm ${
          isUser ? 'bg-brand-600 text-white' : 'border border-slate-200 bg-white text-slate-800'
        }`}
      >
        {children}
      </div>
    </div>
  );
}

interface Props {
  messages: ChatMessage[];
  locked: boolean;
  lockedReason?: string;
}

export default function Chat({ messages, locked, lockedReason }: Props) {
  const qc = useQueryClient();
  const { activeProjectId, streaming, activity, liveResponse, beginStream, pushEvent, endStream } = useApp();
  const [input, setInput] = useState('');
  const [localTurns, setLocalTurns] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, localTurns.length, activity.length, liveResponse]);

  // Server transcript replaces optimistic local turns once refetched.
  useEffect(() => setLocalTurns([]), [messages.length, activeProjectId]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const message = input.trim();
    if (!message || streaming || locked) return;
    setInput('');
    setLocalTurns((t) => [...t, { role: 'user', content: message }]);
    beginStream();
    try {
      await streamChat({ projectId: activeProjectId ?? undefined, message }, pushEvent);
    } finally {
      endStream();
      void qc.invalidateQueries({ queryKey: ['project'] });
      void qc.invalidateQueries({ queryKey: ['projects'] });
      void qc.invalidateQueries({ queryKey: ['artefacts'] });
      void qc.invalidateQueries({ queryKey: ['audit'] });
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && localTurns.length === 0 && !streaming && (
          <div className="mx-auto mt-16 max-w-md text-center">
            <div className="text-4xl">🚀</div>
            <div className="mt-3 text-lg font-semibold text-slate-700">Describe what you want to build</div>
            <div className="mt-1 text-sm text-slate-500">
              The Product Owner agent will turn your requirements into Jira epics, user stories with Gherkin
              acceptance criteria and a Confluence PRD — then pause at the human gate for review.
            </div>
            <div className="mt-4 rounded-lg bg-slate-100 p-3 text-left text-xs text-slate-600">
              <b>Try:</b> “Build a customer loyalty points API: earn points on purchases, redeem at checkout,
              tier upgrades at 1000/5000 points. Must handle 200 rps, p99 &lt; 400ms.”
            </div>
          </div>
        )}

        {messages.map((m) => (
          <Bubble key={m.id} role={m.role}>
            <div className="prose-chat">
              <ReactMarkdown>{m.content}</ReactMarkdown>
            </div>
          </Bubble>
        ))}
        {localTurns.map((m, i) => (
          <Bubble key={`local-${i}`} role={m.role}>
            <div className="prose-chat">
              <ReactMarkdown>{m.content}</ReactMarkdown>
            </div>
          </Bubble>
        ))}

        {streaming && (
          <Bubble role="assistant">
            <ActivityFeed items={activity} />
            {liveResponse ? (
              <div className="prose-chat">
                <ReactMarkdown>{liveResponse}</ReactMarkdown>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-sm text-slate-400">
                <span className="inline-block h-2 w-2 animate-ping rounded-full bg-brand-500" />
                Thinking…
              </div>
            )}
          </Bubble>
        )}
        {!streaming && liveResponse && localTurns.length > 0 && (
          <Bubble role="assistant">
            <div className="prose-chat">
              <ReactMarkdown>{liveResponse}</ReactMarkdown>
            </div>
          </Bubble>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={submit} className="border-t border-slate-200 bg-white p-3">
        {locked ? (
          <div className="rounded-lg bg-amber-50 px-3 py-2.5 text-center text-sm text-amber-800">
            🔒 {lockedReason ?? 'Chat is locked while the gate review is pending.'}
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              className="flex-1 rounded-xl border border-slate-300 px-4 py-2.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              placeholder={streaming ? 'Agents are working…' : 'Message the pipeline… (try "status")'}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={streaming}
            />
            <button
              disabled={streaming || input.trim().length === 0}
              className="rounded-xl bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
            >
              Send
            </button>
          </div>
        )}
      </form>
    </div>
  );
}
