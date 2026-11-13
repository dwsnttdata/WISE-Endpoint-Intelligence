import { describe, expect, it } from '@jest/globals';
import { executionTime } from './model';
import { csvCell, DeviceRecord, exportCsv, filterIssueRecords, getIssuePercentage, getIssueRecords, healthIssues, healthMetric, IssueKey, issues, normalizeDate, pageRecords, resetTableFilters, searchable, Snapshot, statusLabel, tabs } from './model';

function fixture(): Snapshot {
  const keys = Object.keys(issues).filter(key => key !== 'autopilot') as IssueKey[];
  return {
    metadata: { collectionTimestamp: '2026-09-15T00:00:00Z' }, coverage: {},
    summary: {
      totalDevices: 100, evaluatedDevices: 40, exceptionDevices: keys.length,
      health: [{ key: 'compliance', label: 'Compliance', numerator: 30, denominator: 40, percentage: 75, denominatorLabel: 'Intune compliance cohort' }],
      exceptionCounts: keys.reduce((result, key) => ({ ...result, [key]: 1 }), {}),
      percentages: { lowStorage: { numerator: 1, denominator: 20, percentage: 5, denominatorLabel: 'Devices with valid storage measurements' } },
      metrics: { storageMeasured: 20 }, complianceCoverage: { numerator: 40, denominator: 100, percentage: 40, denominatorLabel: 'All source devices' },
      autopilotRegistered: 10, autopilotEnrolled: 9, autopilotExceptions: 1
    },
    devices: keys.map(key => ({
      deviceName: key, operatingSystem: 'Windows', manufacturer: 'Contoso', issues: [key],
      derived: { freeStoragePercent: null, daysInactive: null, lastSyncDate: null, staleFlag: null, threatStatus: 'unknown', patchStatus: 'Unknown' },
      complianceDetail: null, updateDetail: null, usedStorageSpaceGB: null
    } as DeviceRecord)),
    autopilot: [{ serialNumber: 'AP1', enrollmentState: 'notContacted' }], updateRings: [], distributions: {}, policySummary: null
  };
}

describe('EndpointIQ management exception contract', () => {
  it('shows execution completion when recorded and collection time for older reports', () => {
    const snapshot = fixture();
    expect(executionTime(snapshot)).toEqual({ timestamp: '2026-09-15T00:00:00.000Z', label: 'Last collection (UTC)' });
    snapshot.metadata.executionCompletedAtUtc = '2026-10-23T15:42:18Z';
    expect(executionTime(snapshot)).toEqual({ timestamp: '2026-10-23T15:42:18.000Z', label: 'Last execution (UTC)' });
  });
  it('requires an issue and reconciles every device issue count with the summary', () => {
    const snapshot = fixture();
    (Object.keys(issues) as IssueKey[]).filter(key => key !== ('autopilot' as string)).forEach(key => {
      const records = getIssueRecords(snapshot, key);
      expect(records.length).toBe(snapshot.summary.exceptionCounts[key]);
      expect(records.every(record => (record.issues as string[]).indexOf(key) >= 0)).toBe(true);
    });
    expect(getIssueRecords(snapshot, 'all' as IssueKey)).toEqual([]);
    expect(getIssueRecords(snapshot, undefined as unknown as IssueKey)).toEqual([]);
  });
  it('preserves the selected issue after reset and never returns the full exception union', () => {
    const snapshot = fixture();
    expect(filterIssueRecords(snapshot, 'noncompliant', { ...resetTableFilters(), query: 'absent' })).toEqual([]);
    const reset = filterIssueRecords(snapshot, 'noncompliant', resetTableFilters());
    expect(reset).toEqual(getIssueRecords(snapshot, 'noncompliant'));
    expect(reset.length).toBe(1);
    expect(reset.length).toBeLessThan(snapshot.devices.length);
    expect(pageRecords(Array.from({ length: 80 }, (_, index) => ({ index })), 0, 25)).toHaveLength(25);
    expect(pageRecords(Array.from({ length: 80 }, (_, index) => ({ index })), 0, 50)).toHaveLength(50);
  });
  it('uses supplied percentages and denominators and routes health bars only to exceptions', () => {
    const snapshot = fixture();
    expect(healthMetric(snapshot, 'compliance')).toMatchObject({ numerator: 30, denominator: 40, percentage: 75, denominatorLabel: 'Intune compliance cohort' });
    expect(getIssuePercentage(snapshot, 'lowStorage')).toMatchObject({ denominator: 20, percentage: 5 });
    delete snapshot.summary.exceptionCounts.criticalStorage;
    expect(getIssuePercentage(snapshot, 'criticalStorage')).toMatchObject({ numerator: 0, denominator: 20, percentage: 0 });
    snapshot.summary.metrics.storageMeasured = 0;
    expect(getIssuePercentage(snapshot, 'criticalStorage').percentage).toBeNull();
    expect(healthIssues).toEqual({ compliance: 'noncompliant', encryption: 'unencrypted', patch: 'update', threat: 'threat', activity: 'stale', management: 'management' });
  });
  it('exposes only not-enrolled Autopilot exceptions with a registration denominator', () => {
    const snapshot = fixture();
    snapshot.autopilot.push({ serialNumber: 'hidden', enrollmentState: 'Enrolled' });
    expect(getIssueRecords(snapshot, 'autopilot')).toEqual([{ serialNumber: 'AP1', enrollmentState: 'notContacted' }]);
    expect(getIssuePercentage(snapshot, 'autopilot')).toEqual({ numerator: 1, denominator: 10, percentage: 10, denominatorLabel: 'All Autopilot registrations' });
    expect(tabs.find(tab => tab.key === 'enrollment')?.issue).toBe('autopilot');
    expect(statusLabel('approvalPending')).toBe('approvalPending');
  });
  it('searches nested evidence, normalizes dates and leaves missing measurements unknown', () => {
    expect(searchable({ complianceDetail: { evidence: ['setting failure'] } })).toContain('setting failure');
    expect(normalizeDate('/Date(0)/')).toBe('1970-01-01T00:00:00.000Z');
    expect(normalizeDate('0001-01-01T00:00:00Z')).toBeNull();
    expect(normalizeDate('2026-02-30T00:00:00Z')).toBeNull();
    expect(normalizeDate('unknown')).toBeNull();
    expect(fixture().devices[0].derived.freeStoragePercent).toBeNull();
  });
  it('quotes CSV and neutralizes spreadsheet formulas including leading whitespace', () => {
    ['=1+1', '+1', '-1', '@SUM(A1)', ' =1', '\t=1', '\r=1'].forEach(value => expect(csvCell(value).indexOf('"\'')).toBe(0));
    expect(csvCell('a"b')).toBe('"a""b"');
    expect(exportCsv([{ '=column': '=1', evidence: { reason: 'not supplied' } }])).toContain('"\'=column"');
    expect(exportCsv([{ name: 'ordinary' }])).toBe('\uFEFF"name"\r\n"ordinary"');
  });
});