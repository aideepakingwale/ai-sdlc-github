/**
 * Which workspace the user sees. The redesigned workspace ("v2") is opt-in while it settles:
 * `?ui=v2` turns it on, `?ui=classic` turns it off, and the choice is remembered in this browser.
 */
export type UiVersion = 'classic' | 'v2';
const KEY = 'sdlc:ui';

export function resolveUiVersion(search: string, stored: string | null): UiVersion {
  const q = new URLSearchParams(search).get('ui');
  if (q === 'v2' || q === 'classic') return q;
  return stored === 'v2' ? 'v2' : 'classic';
}

export function getUiVersion(): UiVersion {
  let stored: string | null = null;
  try { stored = localStorage.getItem(KEY); } catch { /* storage may be blocked */ }
  const v = resolveUiVersion(typeof window === 'undefined' ? '' : window.location.search, stored);
  try { localStorage.setItem(KEY, v); } catch { /* ignore */ }
  return v;
}

/** Switch and reload, dropping the `ui` query parameter so the address stays clean. */
export function switchUiVersion(v: UiVersion): void {
  try { localStorage.setItem(KEY, v); } catch { /* ignore */ }
  const url = new URL(window.location.href);
  url.searchParams.delete('ui');
  window.location.href = url.toString();
}
