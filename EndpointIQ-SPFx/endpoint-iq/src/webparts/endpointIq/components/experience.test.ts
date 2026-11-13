import { describe, expect, it } from '@jest/globals';
import { experienceMeasurements, experienceRecommendations } from './experience';
import { DeviceRecord, Snapshot, tabs } from './model';

describe('Client user experience recommendations', () => {
  const snapshot: Snapshot = {
    metadata: {}, coverage: {}, autopilot: [], updateRings: [], distributions: {}, policySummary: null,
    devices: [{ issues: ['lowStorage', 'criticalStorage'] } as DeviceRecord, { issues: ['ownership', 'stale'] } as DeviceRecord],
    summary: { totalDevices: 100, health: [], evaluatedDevices: 50, exceptionDevices: 2, complianceCoverage: { numerator: 50, denominator: 100, percentage: 50, denominatorLabel: 'All source devices' }, exceptionCounts: { lowStorage: 1, criticalStorage: 1 }, percentages: {}, metrics: { storageMeasured: 10 }, autopilotExceptions: 3, autopilotRegistered: 5, autopilotEnrolled: 2 }
  };
  it('counts overlapping storage once and keeps registrations separate', () => {
    const plans = experienceRecommendations(snapshot);
    expect(plans[0].affectedDevices).toBe(1);
    expect(plans[0].evidence[0].denominator).toBe(10);
    const onboarding = plans.find(plan => plan.title === 'Easier setup for new users');
    expect(onboarding?.affectedDevices).toBe(0);
    expect(onboarding?.evidence.find(item => item.key === 'autopilot')).toMatchObject({ count: 3, denominator: 5 });
  });
  it('uses plain recommendations without claiming measured user problems or detected threats', () => {
    const plans = experienceRecommendations(snapshot);
    expect(plans.map(plan => plan.title)).toEqual([
      'Enough storage for daily work', 'Up-to-date Windows devices', 'Reliable access to work',
      'Devices that are easier to support', 'Easier setup for new users', 'Secure devices for daily work'
    ]);
    expect(plans[0].impact).toContain('Slowness and failed installations have not been measured here');
    expect(plans[0].steps[0]).toContain('they are already included in the group below 10%');
    expect(plans[1].impact).toContain('This does not show that updates failed or restarts disrupted work');
    expect(plans[3].success).toContain('Missing check-in dates still mean time inactive is unknown');
    expect(plans[4].impact).toContain('Autopilot registrations not reported as enrolled');
    expect(plans[4].impact).toContain('Stock waiting for use is not necessarily a failed setup');
    expect(plans[5].impact).toContain('Unknown or deactivated threat reporting does not mean a threat was detected');
    expect(plans[5].evidence.find(item => item.key === 'threatGap')?.label).toBe('Unknown threat status');
    expect(experienceMeasurements[0]).toMatchObject({ missing: 'Time to start, sign in and be ready to work' });
  });
  it('provides implementation and verification details even with zero recorded findings', () => {
    experienceRecommendations(snapshot).forEach(plan => {
      expect(plan.steps).toHaveLength(3);
      expect(plan.owner.length).toBeGreaterThan(0);
      expect(plan.success.length).toBeGreaterThan(0);
    });
    expect(experienceMeasurements).toHaveLength(5);
  });
  it('places User Experience immediately after Enrollment', () => {
    expect(tabs[tabs.findIndex(tab => tab.key === 'enrollment') + 1].key).toBe('experience');
  });
});