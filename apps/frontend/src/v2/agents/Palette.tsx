import { SNIPPETS, VALUE_TYPES, encodePalette, type PaletteKind } from '../../lib/agents';

export interface PaletteAgent { id: string; name: string }

/** Blocks to drag onto the editor or the inspector. Every block can also be clicked, so nothing depends on dragging. */
export default function Palette({ kind, variables, agents, onUse }: { kind: 'agent' | 'skill'; variables: string[]; agents: PaletteAgent[]; onUse: (k: PaletteKind, v: string) => void }) {
  const chip = (k: PaletteKind, value: string, label: string, title: string) => (
    <button key={`${k}${value}`} type="button" draggable data-pal={encodePalette({ kind: k, value })} title={title} data-testid={`v2-pal-${k}-${value.slice(0, 20)}`}
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', encodePalette({ kind: k, value })); e.dataTransfer.effectAllowed = 'copy'; }}
      onClick={() => onUse(k, value)}
      className="cursor-grab rounded-full border border-dashed border-slate-300 bg-white px-2.5 py-0.5 text-xs font-semibold text-slate-700 hover:border-brand-400 active:cursor-grabbing">{label}</button>
  );
  const group = (title: string, children: React.ReactNode) => <div className="mb-3"><div className="mb-1 text-[11px] font-semibold text-slate-500">{title}</div><div className="flex flex-wrap gap-1.5">{children}</div></div>;
  return (
    <div data-testid="v2-palette" aria-label="Palette">
      <div className="mb-1 mt-4 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Palette</div>
      <p className="mb-2 text-[11px] text-slate-500">Drag a block onto the editor or the inspector, or click it.</p>
      {kind === 'agent' && group('Inputs', VALUE_TYPES.map((t) => chip('input', t, `● ${t}`, 'An input the agent is given')))}
      {kind === 'agent' && group('Outputs', VALUE_TYPES.map((t) => chip('output', t, `${t} ●`, 'An output the agent writes')))}
      {variables.length > 0 && group('Variables', variables.map((v) => chip('var', v, `{${v}}`, 'Drop on the prompt to insert it')))}
      {group('Prompt blocks', SNIPPETS.map((s) => chip('snippet', s.text, s.label, 'Adds this sentence to the prompt')))}
      {kind === 'agent' && agents.length > 0 && group('Delegates', agents.map((a) => chip('agent', a.id, `↳ ${a.name}`, 'Drop on the inspector to delegate to it')))}
    </div>
  );
}
