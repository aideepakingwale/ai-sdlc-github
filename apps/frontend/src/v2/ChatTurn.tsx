import { useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';

const CLAMP_PX = 240;

/** One turn of the stage conversation: your words in a soft bubble, the agent's as plain text; long turns fold. */
export default function ChatTurn({ role, who, time, content }: { role: 'user' | 'assistant' | 'system'; who: string; time: string; content: string }) {
  const body = useRef<HTMLDivElement>(null);
  const [tall, setTall] = useState(false);
  const [open, setOpen] = useState(false);
  useLayoutEffect(() => { if (body.current) setTall(body.current.scrollHeight > CLAMP_PX + 40); }, [content]);
  const mine = role === 'user';
  return (
    <div className={`flex ${mine ? 'justify-end' : ''}`} data-testid={`v2-turn-${role}`}>
      <div className={`min-w-0 ${mine ? 'max-w-[85%]' : 'w-full max-w-[52rem]'}`}>
        <div className={`mb-0.5 flex items-center gap-1.5 px-1 text-[11px] text-slate-500 ${mine ? 'justify-end' : ''}`}>
          {!mine && <span className="flex h-4 w-4 items-center justify-center rounded-full bg-brand-600 text-[9px] font-bold text-white" aria-hidden="true">{role === 'system' ? '!' : 'A'}</span>}
          <span className="font-semibold">{who}</span><span>·</span><span>{time}</span>
        </div>
        <div className={`relative rounded-2xl px-3.5 py-2 text-sm ${mine ? 'rounded-br-md bg-brand-100 text-navy' : 'rounded-bl-md bg-transparent px-1 text-slate-800'}`}>
          <div ref={body} className="prose-chat" style={tall && !open ? { maxHeight: CLAMP_PX, overflow: 'hidden' } : undefined}>
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
          {tall && !open && <div aria-hidden="true" className={`pointer-events-none absolute inset-x-0 bottom-7 h-12 bg-gradient-to-t ${mine ? 'from-brand-100' : 'from-slate-50'} to-transparent`} />}
          {tall && (
            <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="relative mt-1 text-xs font-semibold text-brand-600 hover:underline">
              {open ? 'Show less' : 'Show more'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
