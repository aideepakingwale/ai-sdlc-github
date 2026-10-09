import MiddleText from './MiddleText';
import type { CodeNode } from '../lib/codeTree';

/** The filled folder of the design. */
export function FolderGlyph() {
  return <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" className="shrink-0"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2c.4 0 .8.2 1.1.5L11 7h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" fill="#f4b73f" /></svg>;
}

export function TreeNodes({ node, open, toggle, selected, onSelect, searching, lines, checked, onCheck, badge }: {
  node: CodeNode; open: Set<string>; toggle: (p: string) => void; selected: string | null; onSelect: (p: string) => void; searching: boolean; lines?: Map<string, number>;
  checked?: Set<string>; onCheck?: (p: string) => void; badge?: (n: CodeNode) => React.ReactNode;
}) {
  const tick = (p: string) => onCheck && (
    <input type="checkbox" checked={checked?.has(p) ?? false} onChange={() => onCheck(p)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${p} for the code assistant`} data-check={p}
      className="ml-1 h-3.5 w-3.5 shrink-0 cursor-pointer accent-brand-600 opacity-0 focus:opacity-100 group-hover:opacity-100 checked:opacity-100" title="Select for the code assistant" />
  );
  return (
    <ul className="text-sm">
      {(node.children ?? []).map((c) => c.type === 'dir' ? (
        <li key={c.path}>
          <div className="group flex items-center gap-1 rounded px-1.5 hover:bg-slate-100">
            <button type="button" onClick={() => toggle(c.path)} className="flex min-w-0 flex-1 items-center gap-1.5 py-0.5 text-left text-[15px]">
              <span aria-hidden="true" className="w-3 text-center text-[10px] text-slate-400">{searching || open.has(c.path) ? '▾' : '▸'}</span>
              <FolderGlyph />
              <span className="truncate">{c.name}</span>
            </button>
            {tick(c.path)}
          </div>
          {(searching || open.has(c.path)) && <div className="ml-3 border-l border-slate-200 pl-1"><TreeNodes node={c} open={open} toggle={toggle} selected={selected} onSelect={onSelect} searching={searching} lines={lines} checked={checked} onCheck={onCheck} badge={badge} /></div>}
        </li>
      ) : (
        <li key={c.path}>
          <div className={`group flex items-center gap-1 rounded px-1.5 hover:bg-slate-100 ${selected === c.path ? 'bg-brand-100 text-brand-700' : ''}`}>
            <button type="button" onClick={() => onSelect(c.path)} aria-current={selected === c.path} data-file={c.path}
              className="flex min-w-0 flex-1 items-center gap-1.5 py-0.5 pl-5 text-left text-[15px]">
              <span aria-hidden="true" className="inline-block h-2.5 w-2.5 shrink-0 rounded-[2px] border border-slate-500" /><MiddleText text={c.name} title={c.path} className="flex-1" />
              {badge ? badge(c) : (lines?.get(c.path) ?? 0) > 0 && <span className="shrink-0 rounded-full bg-slate-100 px-1.5 text-[10px] text-slate-500" title="Lines">{lines!.get(c.path)}</span>}
            </button>
            {tick(c.path)}
          </div>
        </li>
      ))}
    </ul>
  );
}
