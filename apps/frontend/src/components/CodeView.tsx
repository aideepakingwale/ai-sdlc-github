import { useEffect, useMemo, useState } from 'react';
import 'highlight.js/styles/github-dark.css';

/**
 * Language-aware code viewer (, extended): syntax colouring via
 * highlight.js, line numbers, and inline diagnostics that flag syntax errors
 * for the formats we can actually parse in the browser (JSON, XML/HTML, and
 * structural YAML checks). The library + grammars are lazy-loaded so the main
 * bundle stays lean (same pattern as the mermaid viewer).
 */

/** Stored file extension → highlight.js language id. */
export function langForExt(ext: string): string {
  const map: Record<string, string> = {
    '.ts': 'typescript', '.tsx': 'typescript', '.js': 'javascript', '.jsx': 'javascript',
    '.mjs': 'javascript', '.cjs': 'javascript',
    '.py': 'python', '.json': 'json', '.jsonc': 'json', '.yaml': 'yaml', '.yml': 'yaml',
    '.sql': 'sql', '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
    '.java': 'java', '.cs': 'csharp', '.go': 'go', '.rb': 'ruby', '.rs': 'rust',
    '.php': 'php', '.kt': 'kotlin', '.kts': 'kotlin', '.scala': 'java', '.groovy': 'java',
    '.gradle': 'java', '.c': 'cpp', '.h': 'cpp', '.cpp': 'cpp', '.hpp': 'cpp',
    '.html': 'xml', '.htm': 'xml', '.xml': 'xml', '.xsd': 'xml', '.jmx': 'xml',
    '.pom': 'xml', '.svg': 'xml',
    '.css': 'css', '.scss': 'css', '.less': 'css',
    '.md': 'markdown', '.markdown': 'markdown',
    '.dockerfile': 'dockerfile', '.tf': 'plaintext', '.tfvars': 'plaintext',
    '.toml': 'ini', '.ini': 'ini', '.cfg': 'ini', '.properties': 'ini', '.env': 'ini',
    '.mmd': 'plaintext', '.puml': 'plaintext', '.dsl': 'plaintext', '.dbml': 'plaintext',
    '.txt': 'plaintext', '.csv': 'plaintext', '.log': 'plaintext',
  };
  return map[ext.toLowerCase()] ?? 'plaintext';
}

/** Language id for a whole filename, honouring extension-less conventions. */
export function langForFile(name: string): string {
  const base = name.slice(name.lastIndexOf('/') + 1).toLowerCase();
  if (base === 'dockerfile' || base.startsWith('dockerfile.')) return 'dockerfile';
  if (base === 'makefile') return 'plaintext';
  const dot = base.lastIndexOf('.');
  return dot > 0 ? langForExt(base.slice(dot)) : 'plaintext';
}

export interface Diagnostic {
  line: number; // 1-indexed; 0 when unknown
  message: string;
}

/** Offset into a string → 1-indexed line number. */
function lineAt(text: string, offset: number): number {
  return text.slice(0, Math.max(0, offset)).split('\n').length;
}

/**
 * Best-effort syntax check. Only reports for formats with a real parser
 * available in the browser — we never invent errors for languages we cannot
 * actually parse (a false "error" is worse than none).
 */
export function lintSource(source: string, lang: string): Diagnostic[] {
  if (!source.trim()) return [];
  if (lang === 'json') {
    try {
      JSON.parse(source);
      return [];
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Invalid JSON';
      const pos = /position (\d+)/i.exec(msg);
      const lineMatch = /line (\d+)/i.exec(msg);
      const line = lineMatch ? Number(lineMatch[1]) : pos ? lineAt(source, Number(pos[1])) : 0;
      return [{ line, message: msg }];
    }
  }
  if (lang === 'xml') {
    try {
      const doc = new DOMParser().parseFromString(source, 'application/xml');
      const err = doc.querySelector('parsererror');
      if (!err) return [];
      const text = (err.textContent ?? 'Malformed XML').replace(/\s+/g, ' ').trim();
      const lineMatch = /line[: ]+(\d+)/i.exec(text);
      return [{ line: lineMatch ? Number(lineMatch[1]) : 0, message: text.slice(0, 200) }];
    } catch {
      return [];
    }
  }
  if (lang === 'yaml') {
    // No YAML parser is bundled; report only the unambiguous structural fault.
    const out: Diagnostic[] = [];
    source.split('\n').forEach((l, i) => {
      if (/^\t+/.test(l)) out.push({ line: i + 1, message: 'YAML forbids tab characters for indentation' });
    });
    return out.slice(0, 20);
  }
  return [];
}

let hljsPromise: Promise<typeof import('highlight.js/lib/core').default> | null = null;

async function loadHljs() {
  if (!hljsPromise) {
    hljsPromise = (async () => {
      const hljs = (await import('highlight.js/lib/core')).default;
      const langs: Array<[string, () => Promise<{ default: any }>]> = [
        ['typescript', () => import('highlight.js/lib/languages/typescript')],
        ['javascript', () => import('highlight.js/lib/languages/javascript')],
        ['python', () => import('highlight.js/lib/languages/python')],
        ['json', () => import('highlight.js/lib/languages/json')],
        ['yaml', () => import('highlight.js/lib/languages/yaml')],
        ['sql', () => import('highlight.js/lib/languages/sql')],
        ['bash', () => import('highlight.js/lib/languages/bash')],
        ['java', () => import('highlight.js/lib/languages/java')],
        ['csharp', () => import('highlight.js/lib/languages/csharp')],
        ['go', () => import('highlight.js/lib/languages/go')],
        ['ruby', () => import('highlight.js/lib/languages/ruby')],
        ['rust', () => import('highlight.js/lib/languages/rust')],
        ['php', () => import('highlight.js/lib/languages/php')],
        ['kotlin', () => import('highlight.js/lib/languages/kotlin')],
        ['cpp', () => import('highlight.js/lib/languages/cpp')],
        ['ini', () => import('highlight.js/lib/languages/ini')],
        ['xml', () => import('highlight.js/lib/languages/xml')],
        ['css', () => import('highlight.js/lib/languages/css')],
        ['markdown', () => import('highlight.js/lib/languages/markdown')],
        ['dockerfile', () => import('highlight.js/lib/languages/dockerfile')],
        ['plaintext', () => import('highlight.js/lib/languages/plaintext')],
      ];
      await Promise.all(langs.map(async ([name, load]) => hljs.registerLanguage(name, (await load()).default)));
      return hljs;
    })();
  }
  return hljsPromise;
}

export default function CodeView({
  source,
  lang,
  filename,
  showDiagnostics = true,
}: {
  source: string;
  lang: string;
  filename?: string;
  showDiagnostics?: boolean;
}) {
  const [lines, setLines] = useState<string[] | null>(null);
  const [copied, setCopied] = useState(false);

  const diagnostics = useMemo(
    () => (showDiagnostics ? lintSource(source, lang) : []),
    [source, lang, showDiagnostics],
  );
  const badLines = useMemo(() => new Set(diagnostics.map((d) => d.line).filter(Boolean)), [diagnostics]);
  const rawLines = useMemo(() => source.replace(/\n$/, '').split('\n'), [source]);

  useEffect(() => {
    let cancelled = false;
    setLines(null);
    void loadHljs()
      .then((hljs) => {
        const language = hljs.getLanguage(lang) ? lang : 'plaintext';
        // Highlight the whole document (so multi-line tokens are correct), then
        // split for per-line numbering/marking.
        const { value } = hljs.highlight(source.replace(/\n$/, ''), { language });
        if (!cancelled) setLines(value.split('\n'));
      })
      .catch(() => {
        if (!cancelled) setLines(null); // fall back to plain text
      });
    return () => {
      cancelled = true;
    };
  }, [source, lang]);

  const rendered = lines ?? rawLines;
  const isHighlighted = lines !== null;

  return (
    <div className="overflow-hidden rounded-lg border border-slate-700 bg-slate-900">
      <div className="flex items-center gap-2 border-b border-slate-700 px-3 py-1.5">
        {filename && <span className="truncate font-mono text-[11px] text-slate-300">{filename}</span>}
        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
          {lang}
        </span>
        <span className="text-[10px] text-slate-500">{rawLines.length} lines</span>
        {diagnostics.length > 0 && (
          <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-red-300">
            ✗ {diagnostics.length} syntax {diagnostics.length === 1 ? 'error' : 'errors'}
          </span>
        )}
        <button
          onClick={() => {
            void navigator.clipboard?.writeText(source).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            });
          }}
          className="ml-auto rounded px-1.5 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-slate-200"
        >
          {copied ? '✓ copied' : 'copy'}
        </button>
      </div>

      {diagnostics.length > 0 && (
        <div className="space-y-0.5 border-b border-red-500/30 bg-red-500/10 px-3 py-1.5">
          {diagnostics.slice(0, 8).map((d, i) => (
            <div key={i} className="font-mono text-[11px] text-red-300">
              {d.line ? `line ${d.line}: ` : ''}{d.message}
            </div>
          ))}
        </div>
      )}

      <div className="hljs max-h-[65vh] overflow-auto !bg-slate-900 text-xs leading-relaxed">
        <table className="w-full border-collapse">
          <tbody>
            {rendered.map((html, i) => {
              const bad = badLines.has(i + 1);
              return (
                <tr key={i} className={bad ? 'bg-red-500/15' : undefined}>
                  <td className="w-10 select-none border-r border-slate-800 px-2 text-right align-top font-mono text-[10px] text-slate-600">
                    {i + 1}
                  </td>
                  <td className="whitespace-pre px-3 font-mono text-slate-100">
                    {bad && <span className="mr-1 select-none text-red-400">✗</span>}
                    {isHighlighted ? (
                      <span dangerouslySetInnerHTML={{ __html: html || ' ' }} />
                    ) : (
                      <span>{html || ' '}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
