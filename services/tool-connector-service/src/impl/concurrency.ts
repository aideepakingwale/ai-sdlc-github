/**
 * Map `items` through `fn` with at most `limit` calls in flight. Results keep
 * input order. The first rejection stops scheduling further work and is rethrown
 * after in-flight calls settle.
 */
export async function mapLimit<T, R>(items: readonly T[], limit: number, fn: (item: T, index: number) => Promise<R>): Promise<R[]> {
  if (!Number.isInteger(limit) || limit < 1) throw new RangeError('limit must be a positive integer');
  const results: R[] = new Array<R>(items.length);
  let next = 0;
  let failure: { error: unknown } | undefined;

  async function worker(): Promise<void> {
    while (failure === undefined) {
      const index = next++;
      if (index >= items.length) return;
      try {
        results[index] = await fn(items[index] as T, index);
      } catch (error) {
        failure ??= { error };
        return;
      }
    }
  }

  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()));
  if (failure) throw failure.error;
  return results;
}
