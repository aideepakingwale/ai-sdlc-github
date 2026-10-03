import { Icon, type IconName } from './ui/Icon';
import { Callout, type Tone } from './ui/Callout';

const STEPS: { icon: IconName; title: string; body: string }[] = [
  { icon: 'pencil', title: 'Describe', body: 'Tell the agent what you need for this stage. Attach documents with the paperclip, or type @ to reference earlier outputs.' },
  { icon: 'eye', title: 'Review the plan', body: 'The AI shows what it understood and which files it will make. Untick what you don’t want, or ask for changes.' },
  { icon: 'play', title: 'Generate', body: 'Files are written in parallel, one tab each. You can leave the page — progress is saved and resumes.' },
  { icon: 'shield', title: 'Approve', body: 'A reviewer approves, or asks for amendments. Approval unlocks the next stage.' },
];

const COLOURS: { tone: Tone; title: string; body: string }[] = [
  { tone: 'error', title: 'Red — something is wrong or blocked', body: 'A failure, a permission you lack, or an escalation. Needs action.' },
  { tone: 'warning', title: 'Yellow — attention', body: 'Out-of-date plan, risks, or items awaiting a review. Check before continuing.' },
  { tone: 'advice', title: 'Yellow note — advice', body: 'Guidance from the AI, such as artifacts it left out as not applicable.' },
  { tone: 'info', title: 'Blue — in progress / information', body: 'Work is running, or a hint about what to do next.' },
  { tone: 'success', title: 'Green — done', body: 'A step finished, a file generated, a stage approved.' },
];

export default function HelpPanel({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/40" onClick={onClose} role="dialog" aria-label="How this works">
      <div className="h-full w-full max-w-md overflow-auto bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-slate-800"><Icon name="help" size={18} />How this works</h2>
          <button type="button" aria-label="Close help" onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-slate-100"><Icon name="x" size={16} /></button>
        </div>
        <p className="mb-3 text-sm text-slate-600">Every stage follows the same four steps. The guide at the top of a stage always tells you which one is next.</p>
        <ol className="mb-5 space-y-2">
          {STEPS.map((s, i) => (
            <li key={s.title} className="flex gap-3 rounded-lg border border-slate-200 p-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-bold text-white">{i + 1}</span>
              <span className="text-sm"><span className="flex items-center gap-1.5 font-semibold text-slate-800"><Icon name={s.icon} size={14} />{s.title}</span><span className="text-slate-600">{s.body}</span></span>
            </li>
          ))}
        </ol>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">What the colours mean</h3>
        <div className="space-y-2">
          {COLOURS.map((c) => <Callout key={c.title} tone={c.tone} compact title={c.title}>{c.body}</Callout>)}
        </div>
      </div>
    </div>
  );
}
