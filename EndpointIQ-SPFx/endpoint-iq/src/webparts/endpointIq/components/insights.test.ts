import { describe, expect, it } from '@jest/globals';
import { activeInsights, evidenceWarnings, executiveSummary, riskEntry, riskRegister, validAiIntelligence } from './insights';
import { DeviceRecord, Snapshot } from './model';

function device(flags: string[], extra: Partial<DeviceRecord> = {}): DeviceRecord {
  return { deviceName: 'Test device', issues: flags, derived: { freeStoragePercent: null, daysInactive: null, lastSyncDate: null, staleFlag: null, threatStatus: 'unknown', patchStatus: 'Unknown' }, complianceDetail: null, updateDetail: null, usedStorageSpaceGB: null, ...extra };
}
function snapshot(): Snapshot {
  return { metadata: {}, coverage: { collectionStatus: { status: 'complete' } }, devices: [], autopilot: [], updateRings: [], distributions: {}, policySummary: null,
    summary: { totalDevices: 100, health: [], exceptionCounts: { lowStorage: 4, criticalStorage: 1, threatGap: 20 }, percentages: {}, metrics: { storageMeasured: 10 }, evaluatedDevices: 50, exceptionDevices: 0, complianceCoverage: { numerator: 50, denominator: 100, percentage: 50, denominatorLabel: 'All source devices' }, autopilotRegistered: 10, autopilotEnrolled: 8, autopilotExceptions: 2 } };
}
function addAiIntelligence(value: Snapshot, text = 'Review the measured category.'): void {
  const keys = (Object.keys(value.summary.exceptionCounts) as Array<keyof typeof value.summary.exceptionCounts>)
    .filter(key => (value.summary.exceptionCounts[key] || 0) > 0) as Array<keyof typeof value.summary.exceptionCounts>;
  if (value.summary.autopilotExceptions > 0) keys.push('autopilot' as keyof typeof value.summary.exceptionCounts);
  value.aiIntelligence = {
    status: 'available',
    executive_summary: { introduction: 'Summary', findings: 'Evidence', bottomLine: 'Review' },
    key_insights: keys.map(key => ({ key, title: String(key), observation: 'Observed', implication: 'Review', priority: 'high' })),
    recommendations: keys.map(key => ({ key, title: String(key), action: text, owner: 'Endpoint operations', priority: 'medium' }))
  };
}
describe('Evidence-based insights and review priorities', () => {
  it('accepts only complete bounded AI narrative and ignores invalid optional output', () => {
    const value = snapshot();
    addAiIntelligence(value, 'Review measured storage');
    expect(validAiIntelligence(value)?.key_insights[0].priority).toBe('high');
    value.aiIntelligence!.recommendations[0].priority = 'P1' as 'medium';
    expect(validAiIntelligence(value)).toBeUndefined();
  });
  it('accepts product names with digits in AI narrative', () => {
    const value = snapshot();
    addAiIntelligence(value);
    value.aiIntelligence!.executive_summary.introduction = 'Windows 11 release needs review';
    expect(validAiIntelligence(value)?.executive_summary.introduction).toContain('Windows 11');
  });
  it('uses the keyed AI recommendation while retaining Python metric counts', () => {
    const value = snapshot();
    addAiIntelligence(value, 'Prioritize approved cleanup after checking user files.');
    value.aiIntelligence!.key_insights.filter(item => item.key === 'lowStorage')[0].title = 'Storage risk';
    value.aiIntelligence!.key_insights.filter(item => item.key === 'lowStorage')[0].observation = 'Measured storage is low';
    value.aiIntelligence!.key_insights.filter(item => item.key === 'lowStorage')[0].implication = 'Review the affected cohort';
    value.aiIntelligence!.recommendations.filter(item => item.key === 'lowStorage')[0].title = 'Storage action';
    value.aiIntelligence!.recommendations.filter(item => item.key === 'lowStorage')[0].priority = 'high';
    const insight = activeInsights(value).find(item => item.key === 'lowStorage');
    expect(insight?.metric.numerator).toBe(4);
    expect(insight?.action).toContain('approved cleanup');
    expect(validAiIntelligence(value)?.recommendations[0].key).toBe('lowStorage');
  });
  it('uses measured storage and registration denominators, with overlapping storage explicit', () => {
    const result = activeInsights(snapshot());
    expect(result.find(item => item.key === 'criticalStorage')?.metric).toMatchObject({ numerator: 1, denominator: 10, percentage: 10 });
    expect(result.find(item => item.key === 'autopilot')?.metric.denominator).toBe(10);
    expect(result.find(item => item.key === 'lowStorage')?.implication).toBe('Less than 10% free storage includes devices below 2%; do not add these groups together.');
    expect(result.find(item => item.key === 'criticalStorage')?.observation).toBe('1 of 10 (10.0%). Devices with valid storage measurements.');
    expect(result.some(item => item.key === 'threat')).toBe(false);
  });
  it('does not turn a reporting gap or missing identity into a security finding', () => {
    expect(riskEntry(device(['threatGap', 'ownership'])).priority).toBe('Reconciliation');
    expect(riskEntry(device(['unencrypted'])).priority).toBe('Remediation review');
    expect(riskEntry(device(['unencrypted', 'stale'])).priority).toBe('Priority review');
  });
  it('escalates high threats, jailbreak and failed wipes without requiring a second signal', () => {
    const threat = device(['threat']); threat.derived.threatStatus = 'high';
    expect(riskEntry(threat).priority).toBe('Urgent investigation');
    expect(riskEntry(device(['jailbroken'])).priority).toBe('Urgent investigation');
    expect(riskEntry(device(['management'], { managementState: 'wipeFailed' })).priority).toBe('Urgent investigation');
    expect(riskEntry(device(['management', 'unencrypted', 'noncompliant'], { managementState: 'wipeFailed' })).action).toContain('Escalate the failed wipe');
    expect(riskEntry(device(['certificate'])).priority).toBe('Priority review');
  });
  it('never counts nested storage twice or invents inactivity from an invalid sync', () => {
    const entry = riskEntry(device(['lowStorage', 'criticalStorage', 'stale']));
    expect(entry.evidence.filter(text => text.indexOf('storage') >= 0)).toHaveLength(1);
    expect(entry.evidence.join(' ')).toContain('time inactive is unknown');
    expect(entry.evidence.join(' ')).not.toContain('null inactive');
  });
  it('retains all exception rows, orders priorities and does not mutate the snapshot', () => {
    const value = snapshot(); value.devices = [device(['threatGap']), device(['jailbroken'])];
    expect(riskRegister(value).map(entry => entry.priority)).toEqual(['Urgent investigation', 'Reconciliation']);
    expect(value.devices[0].issues).toEqual(['threatGap']);
  });
  it('surfaces incomplete sources and missing measurements without asserting a clean fleet', () => {
    const value = snapshot(); value.coverage.collectionStatus = { status: 'partial' }; value.summary.metrics.encryptionUnknown = 3;
    expect(evidenceWarnings(value).join(' ')).toContain('partial');
    expect(evidenceWarnings(value).join(' ')).toContain('3 devices lack a known encryption status');
    value.summary.exceptionCounts = {}; value.summary.autopilotExceptions = 0;
    expect(activeInsights(value)).toEqual([]);
  });
  it('writes a tenant-specific narrative with measured denominators and no invented score', () => {
    const value = snapshot();
    value.summary.health.push({ key: 'encryption', label: 'Encryption', numerator: 7, denominator: 10, percentage: 70, denominatorLabel: 'Known state' });
    const summary = executiveSummary(value);
    expect(summary.introduction).toContain('70.0% (7 / 10)');
    expect(summary.introduction).toContain('Each percentage covers a different group; these are not an overall health score');
    expect(summary.findings).toContain('1 (10.0%) devices have less than 2% free storage, out of 10 devices with valid storage measurements');
    expect(summary.findings).toContain('20 (20.0%) devices have unknown threat status, including unknown or deactivated reporting; this does not mean a threat was detected');
    expect(summary.findings).toContain('Separately, 0 devices report low, medium or high threat severity');
    expect(summary.bottomLine).toContain('No devices meet the urgent investigation rules, but unreported problems may still exist');
    expect(JSON.stringify(summary)).not.toMatch(/95\.7|P[1-4]|3644/);
  });
  it('uses plain titles and keeps unknown threat status separate from reported threats', () => {
    const value = snapshot();
    value.summary.exceptionCounts = { threat: 2, threatGap: 20, noncompliant: 3, unencrypted: 4, stale: 5, update: 6, ownership: 7 };
    const result = activeInsights(value);
    expect(result.filter(item => item.key !== 'autopilot').map(item => item.title)).toEqual([
      'Reported threats', 'Non-compliant devices', 'Unencrypted devices', 'Devices needing updates',
      'Inactive devices', 'Unknown threat status', 'Devices without a matching user'
    ]);
    expect(result.find(item => item.key === 'threatGap')).toMatchObject({
      title: 'Unknown threat status', metric: { numerator: 20, denominator: 100, percentage: 20 },
      implication: 'Threat status is unknown or reporting is deactivated. This does not mean a threat was detected or that protection is working.'
    });
    expect(result.find(item => item.key === 'unencrypted')?.implication).toContain('Unknown encryption states are not included');
  });
});