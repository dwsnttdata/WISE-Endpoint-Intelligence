import { getIssuePercentage, issues, SelectionKey, Snapshot } from './model';

export interface ExperienceRecommendation {
  title: string;
  keys: SelectionKey[];
  impact: string;
  owner: string;
  steps: string[];
  success: string;
  evidence: { key: SelectionKey; label: string; count: number; denominator: number; denominatorLabel: string }[];
  affectedDevices: number;
}

const plans = [
  { title: 'Enough storage for daily work', keys: ['criticalStorage', 'lowStorage'], impact: 'Low free storage can affect downloads, updates and daily work. Slowness and failed installations have not been measured here.', owner: 'Endpoint operations and service desk', steps: ['Check free space with the user. Start with devices below 2%; they are already included in the group below 10%.', 'Use approved cleanup and file-retention policies. Protect user files and confirm backups before deleting data or adding storage.', 'Test cleanup with a small group, schedule it outside important work and provide a service desk contact.'], success: 'New device data shows enough free storage, and users confirm they can work normally. Track repeat storage-related support requests separately.' },
  { title: 'Up-to-date Windows devices', keys: ['update'], impact: 'These devices need a monthly update or Windows support review. This does not show that updates failed or restarts disrupted work.', owner: 'Windows update team and app owners', steps: ['Compare installed and required monthly updates. Check Windows edition, update channel and Extended Security Updates coverage where needed.', 'Check that important apps work and test updates with an approved small group before wider rollout. Review installation results in Intune.', 'Agree update times, restart notices and recovery plans with business owners. Check results after the next device sync.'], success: 'Supported devices that can be checked meet the required monthly update level. Devices with unsupported Windows versions have an agreed plan. Test users confirm apps work and report any restart disruption.' },
  { title: 'Reliable access to work', keys: ['noncompliant', 'certificate'], impact: 'Non-compliance and expired management certificates may affect access or device management. Blocked sign-ins have not been confirmed here.', owner: 'Compliance team and sign-in support', steps: ['Check the failing policy setting, certificate date and last check-in. Compare reported access problems with approved sign-in records.', 'Explain the fix to affected users and provide help with recovery. Keep compliance and access protections in place.', 'Renew the certificate or enroll the device again only through an approved process. After sync, check policy results and confirm users can access their work.'], success: 'New device data shows the compliance or certificate issue is resolved, and users confirm access. Check sign-in records before attributing support requests to a policy.' },
  { title: 'Devices that are easier to support', keys: ['stale', 'management', 'ownership'], impact: 'Inactive devices, unfinished actions and missing user details make it harder to know who to contact and what support is needed.', owner: 'Device management team and service desk', steps: ['Check device purpose, main contact and last check-in. Confirm whether shared devices, kiosks, devices without users or stored devices are intentional.', 'Check the device connection and management service with the owner. Review pending or failed actions before retrying them.', 'Use approved support to recover devices in use. Remove or wipe devices only with business approval, data-protection checks and confirmation that the action finished.'], success: 'Devices in use check in within the agreed policy period. Devices intentionally without users are documented, and action results are confirmed. Missing check-in dates still mean time inactive is unknown.' },
  { title: 'Easier setup for new users', keys: ['autopilot'], impact: 'Check Autopilot registrations not reported as enrolled against deployment plans. Stock waiting for use is not necessarily a failed setup.', owner: 'Device setup team and new-user support', steps: ['Check deployment dates, intended users and Autopilot assignments.', 'Test the approved setup with the expected apps and policies. Give new users a named support contact and clear setup steps.', 'Check actual enrollment errors before making changes. Review spare stock separately and remove registrations only through an approved process.'], success: 'Devices planned for use are enrolled, and users can complete first-day tasks. Setup time and related support requests need separate measurement.' },
  { title: 'Secure devices for daily work', keys: ['unencrypted', 'threat', 'threatGap', 'jailbroken'], impact: 'Check encryption, jailbreak or root reports and threat status with the security team. Unknown or deactivated threat reporting does not mean a threat was detected or that users experienced problems.', owner: 'Endpoint security and security operations', steps: ['Investigate reported threats and jailbreak or root access with the security owner before routine improvements.', 'Check device support, encryption readiness, saved recovery keys and threat-reporting setup. Separate unknown reporting from confirmed protection problems.', 'Test approved fixes with a small group. Explain prompts or restarts and provide recovery support while keeping security protections in place.'], success: 'New device data confirms the relevant protection or reporting status, and security owners check the results. Ask affected users about disruption and track repeat problems through service desk records.' }
];

export function experienceRecommendations(snapshot: Snapshot): ExperienceRecommendation[] {
  return plans.map(plan => {
    const keys = plan.keys as SelectionKey[];
    return { ...plan, keys, evidence: keys.map(key => {
      const metric = getIssuePercentage(snapshot, key);
      return { key, label: issues[key].label, count: metric.numerator, denominator: metric.denominator, denominatorLabel: metric.denominatorLabel };
    }), affectedDevices: snapshot.devices.filter(record => keys.some(key => record.issues.indexOf(key) >= 0)).length };
  });
}

export const experienceMeasurements = [
  { domain: 'Startup and sign-in speed', missing: 'Time to start, sign in and be ready to work', recommendation: 'Check Endpoint analytics availability and licensing. Measure current times, then compare test users with the wider device group.' },
  { domain: 'App reliability', missing: 'Crashes, freezes, time to open apps and installation results', recommendation: 'Compare approved app measurements with device details and support records before recommending replacement.' },
  { domain: 'Network and remote work', missing: 'Connection delays, lost data, Wi-Fi and VPN quality, and remote-session speed', recommendation: 'Use approved connection checks for reported problems. Look for patterns by working hours and location.' },
  { domain: 'Device performance', missing: 'Processor and memory use, storage response time and battery wear', recommendation: 'Measure resource use before recommending more memory, more storage or replacement devices.' },
  { domain: 'User feedback and support', missing: 'User feedback, support requests, time to resolve problems and lost working time', recommendation: 'Agree how to collect feedback and support measures, protect privacy and review results regularly.' }
];