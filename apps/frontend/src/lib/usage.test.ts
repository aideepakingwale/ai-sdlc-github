import { describe, expect, it } from 'vitest';
import { driftLabel, reconHeadline, type ReconList, activityLabel, barWidths, calls, fillDays, headline, percent, rangeFor, statusLabel, tokensFull, usageQuery, type ProjectUsageReport } from './usage';

describe('usage helpers', () => {
  it('bars are relative to the longest and anything above zero stays visible', () => {
    expect(barWidths([100, 50, 0])).toEqual([100, 50, 0]);
    expect(barWidths([1000, 1])).toEqual([100, 2]);
    expect(barWidths([0, 0])).toEqual([0, 0]);
    expect(barWidths([])).toEqual([]);
  });

  it('the days of the range are all present, in order, with zeros for quiet days', () => {
    const rows = fillDays({ from: '2026-10-01', to: '2026-10-05' }, [{ day: '2026-10-02', tokens: 5, calls: 1 }, { day: '2026-10-05', tokens: 9, calls: 2 }]);
    expect(rows.map((r) => r.day)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05']);
    expect(rows.map((r) => r.tokens)).toEqual([0, 5, 0, 0, 9]);
  });

  it('a preset range ends today and is inclusive', () => {
    expect(rangeFor(7, new Date('2026-10-31T10:00:00Z'))).toEqual({ from: '2026-10-25', to: '2026-10-31' });
    expect(rangeFor(1, new Date('2026-10-31T23:59:00Z'))).toEqual({ from: '2026-10-31', to: '2026-10-31' });
  });

  it('builds the query only with what was chosen', () => {
    expect(usageQuery({ from: 'a', to: 'b', billable: 'all' })).toBe('from=a&to=b&billable=all');
    expect(usageQuery({ from: 'a', to: 'b', billable: 'billable', person: 'me', status: 'error', limit: 50, offset: 100 })).toBe('from=a&to=b&billable=billable&person=me&status=error&limit=50&offset=100');
  });

  it('speaks in plain words', () => {
    expect(activityLabel('stage.rework')).toBe('Reworking after validation');
    expect(activityLabel('something.new')).toBe('something.new');
    expect(statusLabel('refused')).toBe('Refused by the model');
    expect(percent(0.004)).toBe('<1%');
    expect(percent(0)).toBe('0%');
    expect(percent(0.256)).toBe('26%');
    expect(tokensFull(1234567)).toBe('1,234,567');
  });

  it('counts calls in the singular too', () => {
    expect([calls(0), calls(1), calls(1234)]).toEqual(['0 calls', '1 call', '1,234 calls']);
  });

  it('the headline names the biggest use, and says so when nothing ran', () => {
    const base = { totals: { tokens: 0, calls: 0 }, byActivity: [] } as unknown as ProjectUsageReport;
    expect(headline(base)).toBe('No model calls in this period.');
    const r = { totals: { tokens: 1000, calls: 4 }, byActivity: [{ activity: 'stage.generate', tokens: 750, calls: 3, wasteTokens: 0 }] } as unknown as ProjectUsageReport;
    expect(headline(r)).toBe('1,000 tokens across 4 calls; 75% went to writing stage artefacts.');
    expect(headline({ ...r, totals: { tokens: 5, calls: 1 } } as unknown as ProjectUsageReport)).toContain('across 1 call;');
  });
});

describe('reconciliation helpers', () => {
  it('shows the drift as a signed percentage of what AWS reports', () => {
    expect([driftLabel(null), driftLabel(0), driftLabel(0.0004), driftLabel(0.0123), driftLabel(-0.034), driftLabel(0.5)]).toEqual(['n/a', '0%', '0%', '+1.2%', '−3.4%', '+50%']);
  });
  it('the headline says what the latest night found', () => {
    const run = { day: '2026-10-10', status: 'ok', drift: 0.004, threshold: 0.02, error: null } as unknown as ReconList['runs'][number];
    expect(reconHeadline({ configured: false, source: '', threshold: 0.02, runs: [] })).toMatch(/Not set up/);
    expect(reconHeadline({ configured: true, source: 'cloudwatch', threshold: 0.02, runs: [] })).toMatch(/first comparison/);
    expect(reconHeadline({ configured: true, source: 'cloudwatch', threshold: 0.02, runs: [run] })).toBe('2026-10-10: the ledger agrees with AWS (+0.4%).');
    expect(reconHeadline({ configured: true, source: 'cloudwatch', threshold: 0.02, runs: [{ ...run, status: 'drift', drift: 0.07 }] })).toBe('2026-10-10: the ledger differs from AWS by +7.0%, more than the 2% allowed.');
    expect(reconHeadline({ configured: true, source: 'cloudwatch', threshold: 0.02, runs: [{ ...run, status: 'no_data' }] })).toMatch(/no invocation log records/);
  });
});
