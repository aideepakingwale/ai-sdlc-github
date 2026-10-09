import { classifyDiffLine, type DiffLineKind } from '../lib/codeEdit';

const CLS: Record<DiffLineKind, string> = {
  add: 'bg-emerald-50 text-emerald-900', del: 'bg-red-50 text-red-900', hunk: 'bg-slate-100 text-slate-500', ctx: 'text-slate-700', meta: 'hidden',
};

/** A unified diff, line by line: additions green, removals red. For a new file every line is an addition. */
export default function DiffView({ diff }: { diff: string }) {
  const lines = diff.split('\n');
  return (
    <pre className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-white font-mono text-[11px] leading-5" data-testid="diff-view">
      {lines.map((l, i) => {
        const k = classifyDiffLine(l);
        if (k === 'meta') return null;
        return <div key={i} className={`whitespace-pre px-2 ${CLS[k]}`}>{l || ' '}</div>;
      })}
    </pre>
  );
}
