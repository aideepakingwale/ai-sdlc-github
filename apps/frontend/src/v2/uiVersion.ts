/**
 * Which workspace the user sees. The redesigned workspace ("v2") is the default; `?ui=classic` switches to the
 * previous one and `?ui=v2` switches back. An explicit choice is remembered in this browser; the default is not
 * stored, so it can change later without being overridden by an old value.
 */
export type UiVersion = 'classic' | 'v2';
// A new key: the previous one auto-saved whichever workspace was shown, which would pin everyone to "classic".
const KEY = 'sdlc:ui-choice';

export function resolveUiVersion(search: string, stored: string | null): UiVersion {
  const q = new URLSearchParams(search).get('ui');
  if (q === 'v2' || q === 'classic') return q;
  return stored === 'classic' ? 'classic' : 'v2';
}

export function getUiVersion(): UiVersion {
  let stored: string | null = null;
  try { stored = localStorage.getItem(KEY); } catch { /* storage may be blocked */ }
  const search = typeof window === 'undefined' ? '' : window.location.search;
  const v = resolveUiVersion(search, stored);
  const explicit = new URLSearchParams(search).get('ui');
  if (explicit === 'v2' || explicit === 'classic') { try { localStorage.setItem(KEY, v); } catch { /* ignore */ } }
  return v;
}

/** Switch and reload, dropping the `ui` query parameter so the address stays clean. */
export function switchUiVersion(v: UiVersion): void {
  try { localStorage.setItem(KEY, v); } catch { /* ignore */ }
  const url = new URL(window.location.href);
  url.searchParams.delete('ui');
  window.location.href = url.toString();
}
