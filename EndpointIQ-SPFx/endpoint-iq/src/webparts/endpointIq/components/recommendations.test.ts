import { describe, expect, it } from '@jest/globals';
import { issues, SelectionKey } from './model';
import { policyFindings, policySummary, recommendedAction, updateAction } from './recommendations';

describe('Device recommendations', () => {
  it('names only reported failing policies and settings and separates errors', () => {
    const record = { complianceDetail: { policyStates: [
      { displayName: 'Encryption policy', state: 'nonCompliant', settingStates: [{ settingName: 'Require encryption', state: 'nonCompliant' }] },
      { displayName: 'Healthy policy', state: 'compliant' },
      { displayName: 'Unknown policy', state: 'unknown' },
      { displayName: 'Error policy', state: 'error' },
      { displayName: 'Conflicting policy', state: 'conflict' }
    ] } };
    expect(policyFindings(record)).toHaveLength(3);
    expect(policyFindings(record)[0].setting).toBe('Require encryption');
    expect(policySummary(record)).not.toMatch(/Healthy policy|Unknown policy/);
    expect(policySummary(record)).toContain('Could not check policy');
    expect(recommendedAction(record, 'noncompliant')).toContain('Encryption policy');
  });
  it('does not invent policy causes for old or malformed snapshots', () => {
    [{}, { complianceDetail: { policyStates: 'invalid' } }, { complianceDetail: { policyStates: [null, 1, []] } }].forEach(record => {
      expect(policyFindings(record)).toEqual([]);
      expect(policySummary(record)).toBe('Policy failure details not available');
    });
  });
  it('uses platform, readiness and assignment evidence without assuming cloud storage is exposed', () => {
    expect(recommendedAction({ isEncrypted: false, operatingSystem: 'Windows', encryptionDetail: { encryptionReadinessState: 'notReady', encryptionPolicySettingState: 'notAssigned' } }, 'unencrypted')).toMatch(/not ready.*no assigned policy.*BitLocker/);
    expect(recommendedAction({ isEncrypted: false, operatingSystem: 'macOS' }, 'unencrypted')).toContain('FileVault');
    expect(recommendedAction({ isEncrypted: false, model: 'Cloud PC Enterprise' }, 'unencrypted')).toContain('does not establish');
    expect(recommendedAction({}, 'unencrypted')).toContain('not confirmed');
  });
  it('provides guidance for every issue and keeps unknown updates and threats distinct', () => {
    (Object.keys(issues) as SelectionKey[]).forEach(issue => expect(recommendedAction({}, issue).length).toBeGreaterThan(30));
    expect(updateAction({ patchStatus: 'Unknown build' })).toContain('does not prove installation failure');
    expect(updateAction({ patchStatus: 'Non-compliant', requiredKB: 'KB123' })).toContain('KB123');
    expect(recommendedAction({}, 'threatGap')).toContain('not a detected threat');
  });
});