/** What document analysis found in an attachment (mirrors the server's record). */
export interface AttachmentExtraction {
  kind?: string;
  method?: string;
  chars?: number;
  stats?: Record<string, number>;
  warnings?: string[];
  outline?: string[];
}

/** e.g. "42 pages · 6 tables · 9 of 11 figures read" — empty for plain text. */
export function summariseExtraction(stats?: Record<string, number>): string {
  const s = stats ?? {};
  const bits: string[] = [];
  const count = (key: string, one: string, many: string) => {
    const n = Number(s[key] ?? 0);
    if (n) bits.push(`${n} ${n === 1 ? one : many}`);
  };
  count('pages', 'page', 'pages');
  count('slides', 'slide', 'slides');
  count('sheets', 'sheet', 'sheets');
  count('diagrams', 'diagram', 'diagrams');
  count('tables', 'table', 'tables');
  count('charts', 'chart', 'charts');
  count('diagram_edges', 'diagram connection', 'diagram connections');
  const found = Number(s.figures_found ?? 0);
  if (found) bits.push(`${Number(s.figures_described ?? 0)} of ${found} figure${found === 1 ? '' : 's'} read`);
  return bits.join(' · ');
}
