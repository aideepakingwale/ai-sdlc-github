/**
 * Stale-chunk recovery.
 *
 * Every frontend build gives its lazy chunks new content hashes and deletes the
 * old ones. A tab that was open across a deploy therefore asks for a filename
 * that no longer exists — the dynamic `import()` rejects with "Failed to fetch
 * dynamically imported module". The page isn't broken, it's simply out of date:
 * the fix is to reload so the browser fetches the new chunk manifest.
 *
 * The guard is TIME-based, not once-per-session: a once-ever flag stops
 * rescuing the user on the second and later deploys of a long-lived session,
 * which is exactly when they need it. A short cooldown still makes an infinite
 * reload loop impossible when a chunk is genuinely missing from the server.
 */

const KEY = 'sdlc:chunk-reload-at';
const COOLDOWN_MS = 15_000;

export function isStaleChunkError(err: unknown): boolean {
  const msg =
    err instanceof Error ? `${err.message} ${err.name}` : typeof err === 'string' ? err : String(err);
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported|Failed to fetch/i.test(
    msg,
  );
}

/**
 * Reload once to pick up the current build. Returns true when a reload was
 * started (the caller should stop rendering an error), false when the cooldown
 * says we already just tried — in which case the failure is real and should be
 * surfaced.
 */
export function recoverFromStaleChunk(): boolean {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(KEY)) || 0;
  } catch {
    /* storage disabled — fall through and allow one reload */
  }
  if (Date.now() - last < COOLDOWN_MS) return false;
  try {
    sessionStorage.setItem(KEY, String(Date.now()));
  } catch {
    /* ignore */
  }
  window.location.reload();
  return true;
}

/**
 * Install app-wide handlers. Vite emits `vite:preloadError` for a failed lazy
 * import; we also catch the raw rejection so a chunk loaded outside Vite's
 * preload helper still recovers.
 */
export function installStaleChunkRecovery(): void {
  // `vite:preloadError` is emitted by Vite's lazy-import helper; it is not in
  // the DOM lib's WindowEventMap, hence the cast.
  window.addEventListener('vite:preloadError', ((e: Event) => {
    e.preventDefault(); // we handle it by reloading
    recoverFromStaleChunk();
  }) as EventListener);
  window.addEventListener('unhandledrejection', (e) => {
    if (isStaleChunkError(e.reason)) recoverFromStaleChunk();
  });
}
