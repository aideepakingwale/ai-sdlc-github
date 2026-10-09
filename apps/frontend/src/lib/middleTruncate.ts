/**
 * Shorten `text` to at most `visible` characters by replacing the middle with an ellipsis, keeping more of the end than the start
 * (for a path the file name matters most): `src/main/java/…/OrderService.java`.
 */
export function middleTruncate(text: string, visible: number): string {
  if (visible >= text.length) return text;
  if (visible <= 1) return '…';
  const keep = visible - 1;
  const tail = Math.ceil(keep * 0.62);
  const head = keep - tail;
  return `${text.slice(0, head)}…${text.slice(text.length - tail)}`;
}

/** The largest `visible` for which `fits(middleTruncate(text, visible))` holds (binary search); the full text when it fits. */
export function fitMiddle(text: string, fits: (candidate: string) => boolean): string {
  if (fits(text)) return text;
  let lo = 1, hi = text.length - 1, best = 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (fits(middleTruncate(text, mid))) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return middleTruncate(text, best);
}
