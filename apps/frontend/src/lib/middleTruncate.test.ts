import { describe, expect, it } from 'vitest';
import { fitMiddle, middleTruncate } from './middleTruncate';

describe('middle truncation', () => {
  it('keeps short text as it is', () => {
    expect(middleTruncate('a/b.ts', 20)).toBe('a/b.ts');
  });
  it('replaces the middle and keeps the file name', () => {
    const t = 'src/main/java/com/acme/amos/OrderService.java';
    const out = middleTruncate(t, 28);
    expect(out.length).toBe(28);
    expect(out).toContain('…');
    expect(out.endsWith('OrderService.java')).toBe(true);
    expect(out.startsWith('src/')).toBe(true);
  });
  it('finds the longest form that fits', () => {
    const t = 'src/main/java/com/acme/amos/OrderService.java';
    const out = fitMiddle(t, (c) => c.length <= 24);
    expect(out.length).toBe(24);
    expect(fitMiddle(t, () => true)).toBe(t);
  });
});
