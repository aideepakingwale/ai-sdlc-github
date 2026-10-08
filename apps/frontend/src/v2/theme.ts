/** Theme for the redesigned workspace: light, dark, or follow the system. The classic workspace stays light. */
export type ThemeChoice = 'light' | 'dark' | 'system';
const KEY = 'sdlc:theme';

export function resolveTheme(choice: ThemeChoice, systemDark: boolean): 'light' | 'dark' {
  return choice === 'system' ? (systemDark ? 'dark' : 'light') : choice;
}

export function getThemeChoice(): ThemeChoice {
  try { const v = localStorage.getItem(KEY); if (v === 'light' || v === 'dark' || v === 'system') return v; } catch { /* blocked */ }
  return 'system';
}

const systemDark = (): boolean => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches;

export function applyTheme(choice: ThemeChoice = getThemeChoice()): void {
  document.documentElement.classList.toggle('dark', resolveTheme(choice, systemDark()) === 'dark');
}

export function setThemeChoice(choice: ThemeChoice): void {
  try { localStorage.setItem(KEY, choice); } catch { /* ignore */ }
  applyTheme(choice);
}

/** Keep "system" in step with the OS while the page is open. */
export function watchSystemTheme(): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const on = () => { if (getThemeChoice() === 'system') applyTheme('system'); };
  mq.addEventListener('change', on);
  return () => mq.removeEventListener('change', on);
}
