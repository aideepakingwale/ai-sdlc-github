import type { ReactElement } from 'react';

/**
 * Inline vector icon set (24×24, stroke-based, currentColor) — no emoji, no external font or
 * network request (strict CSP friendly). Colour comes from the surrounding text colour, so a
 * red Callout gives a red icon. Decorative by default (aria-hidden); pass `label` when the icon
 * is the only content of a control.
 */
type Shape =
  | { d: string }
  | { c: [number, number, number] }
  | { r: [number, number, number, number, number] }
  | { l: [number, number, number, number] }
  | { pg: string }
  | { pl: string }
  | { e: [number, number, number, number] };

const P = (d: string): Shape => ({ d });
const C = (cx: number, cy: number, r: number): Shape => ({ c: [cx, cy, r] });
const R = (x: number, y: number, w: number, h: number, rx = 0): Shape => ({ r: [x, y, w, h, rx] });
const L = (x1: number, y1: number, x2: number, y2: number): Shape => ({ l: [x1, y1, x2, y2] });
const PG = (pg: string): Shape => ({ pg });
const PL = (pl: string): Shape => ({ pl });
const E = (cx: number, cy: number, rx: number, ry: number): Shape => ({ e: [cx, cy, rx, ry] });
const dot = (cx: number, cy: number): Shape => L(cx, cy, cx + 0.01, cy);

export const ICONS = {
  maximize: [PL('15 3 21 3 21 9'), PL('9 21 3 21 3 15'), L(21, 3, 14, 10), L(3, 21, 10, 14)],
  minimize: [PL('4 14 10 14 10 20'), PL('20 10 14 10 14 4'), L(14, 10, 21, 3), L(3, 21, 10, 14)],
  info: [C(12, 12, 10), L(12, 16, 12, 12), dot(12, 8)],
  warning: [P('M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z'), L(12, 9, 12, 13), dot(12, 17)],
  error: [C(12, 12, 10), L(15, 9, 9, 15), L(9, 9, 15, 15)],
  success: [P('M22 11.08V12a10 10 0 1 1-5.93-9.14'), PL('22 4 12 14.01 9 11.01')],
  check: [PL('20 6 9 17 4 12')],
  advice: [P('M9 18h6'), P('M10 22h4'), P('M12 2a7 7 0 0 0-4 12.74V17h8v-2.26A7 7 0 0 0 12 2z')],
  lock: [R(3, 11, 18, 11, 2), P('M7 11V7a5 5 0 0 1 10 0v4')],
  clock: [C(12, 12, 10), PL('12 6 12 12 16 14')],
  loader: [P('M21 12a9 9 0 1 1-6.219-8.56')],
  circle: [C(12, 12, 9)],
  play: [PG('6 3 20 12 6 21')],
  refresh: [PL('23 4 23 10 17 10'), PL('1 20 1 14 7 14'), P('M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15')],
  paperclip: [P('M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48')],
  file: [P('M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'), PL('14 2 14 8 20 8'), L(16, 13, 8, 13), L(16, 17, 8, 17), L(10, 9, 8, 9)],
  layers: [PG('12 2 2 7 12 12 22 7 12 2'), PL('2 17 12 22 22 17'), PL('2 12 12 17 22 12')],
  shield: [P('M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z')],
  chart: [L(12, 20, 12, 10), L(18, 20, 18, 4), L(6, 20, 6, 16)],
  map: [PG('1 6 1 22 8 18 16 22 23 18 23 2 16 6 8 2 1 6'), L(8, 2, 8, 18), L(16, 6, 16, 22)],
  book: [P('M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z'), P('M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z')],
  users: [P('M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'), C(9, 7, 4), P('M23 21v-2a4 4 0 0 0-3-3.87'), P('M16 3.13a4 4 0 0 1 0 7.75')],
  user: [P('M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2'), C(12, 7, 4)],
  folder: [P('M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z')],
  download: [P('M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'), PL('7 10 12 15 17 10'), L(12, 15, 12, 3)],
  upload: [P('M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'), PL('17 8 12 3 7 8'), L(12, 3, 12, 15)],
  copy: [R(9, 9, 13, 13, 2), P('M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1')],
  'chevron-down': [PL('6 9 12 15 18 9')],
  'chevron-right': [PL('9 18 15 12 9 6')],
  'chevron-left': [PL('15 18 9 12 15 6')],
  'arrow-right': [L(5, 12, 19, 12), PL('12 5 19 12 12 19')],
  search: [C(11, 11, 8), L(21, 21, 16.65, 16.65)],
  send: [L(22, 2, 11, 13), PG('22 2 15 22 11 13 2 9 22 2')],
  sparkles: [P('M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z'), P('M19 3v4M17 5h4'), P('M5 17v4M3 19h4')],
  sliders: [L(4, 21, 4, 14), L(4, 10, 4, 3), L(12, 21, 12, 12), L(12, 8, 12, 3), L(20, 21, 20, 16), L(20, 12, 20, 3), L(1, 14, 7, 14), L(9, 8, 15, 8), L(17, 16, 23, 16)],
  help: [C(12, 12, 10), P('M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3'), dot(12, 17)],
  trash: [PL('3 6 5 6 21 6'), P('M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6'), L(10, 11, 10, 17), L(14, 11, 14, 17), P('M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2')],
  bell: [P('M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9'), P('M13.73 21a2 2 0 0 1-3.46 0')],
  eye: [P('M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'), C(12, 12, 3)],
  edit: [P('M12 20h9'), P('M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z')],
  tasks: [PL('9 11 12 14 22 4'), P('M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11')],
  zap: [PG('13 2 3 14 12 14 11 22 21 10 12 10 13 2')],
  database: [E(12, 5, 9, 3), P('M21 12c0 1.66-4 3-9 3s-9-1.34-9-3'), P('M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5')],
  cloud: [P('M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z')],
  server: [R(2, 2, 20, 8, 2), R(2, 14, 20, 8, 2), L(6, 6, 6.01, 6), L(6, 18, 6.01, 18)],
  monitor: [R(2, 3, 20, 14, 2), L(8, 21, 16, 21), L(12, 17, 12, 21)],
  code: [PL('16 18 22 12 16 6'), PL('8 6 2 12 8 18')],
  box: [P('M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z'), PL('3.27 6.96 12 12.01 20.73 6.96'), L(12, 22.08, 12, 12)],
  'log-out': [P('M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'), PL('16 17 21 12 16 7'), L(21, 12, 9, 12)],
  plus: [L(12, 5, 12, 19), L(5, 12, 19, 12)],
  x: [L(18, 6, 6, 18), L(6, 6, 18, 18)],
  minus: [L(5, 12, 19, 12)],
  'external-link': [P('M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'), PL('15 3 21 3 21 9'), L(10, 14, 21, 3)],
  history: [PL('3 3 3 8 8 8'), P('M3.05 13A9 9 0 1 0 6 5.3L3 8'), PL('12 7 12 12 16 14')],
  ban: [C(12, 12, 10), L(4.93, 4.93, 19.07, 19.07)],
  message: [P('M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z')],
  target: [C(12, 12, 10), C(12, 12, 6), C(12, 12, 2)],
  flag: [P('M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z'), L(4, 22, 4, 15)],
  gitbranch: [L(6, 3, 6, 15), C(18, 6, 3), C(6, 18, 3), P('M18 9a9 9 0 0 1-9 9')],
  pencil: [P('M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z')],
  inbox: [PL('22 12 16 12 14 15 10 15 8 12 2 12'), P('M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z')],
} satisfies Record<string, Shape[]>;

export type IconName = keyof typeof ICONS;

function shapeEl(s: Shape, i: number): ReactElement {
  if ('d' in s) return <path key={i} d={s.d} />;
  if ('c' in s) return <circle key={i} cx={s.c[0]} cy={s.c[1]} r={s.c[2]} />;
  if ('r' in s) return <rect key={i} x={s.r[0]} y={s.r[1]} width={s.r[2]} height={s.r[3]} rx={s.r[4]} />;
  if ('l' in s) return <line key={i} x1={s.l[0]} y1={s.l[1]} x2={s.l[2]} y2={s.l[3]} />;
  if ('pg' in s) return <polygon key={i} points={s.pg} />;
  if ('pl' in s) return <polyline key={i} points={s.pl} />;
  return <ellipse key={i} cx={s.e[0]} cy={s.e[1]} rx={s.e[2]} ry={s.e[3]} />;
}

export function Icon({
  name, size = 16, className = '', label, spin = false, strokeWidth = 2,
}: { name: IconName; size?: number; className?: string; label?: string; spin?: boolean; strokeWidth?: number }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round"
      className={`inline-block shrink-0 ${spin ? 'animate-spin' : ''} ${className}`}
      role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} focusable="false"
    >
      {ICONS[name].map(shapeEl)}
    </svg>
  );
}
