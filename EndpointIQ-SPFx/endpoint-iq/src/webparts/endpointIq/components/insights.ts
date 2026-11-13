import { DeviceRecord, display, formatNumber, formatPercent, getIssuePercentage, healthMetric, IssueKey, issues, lower, normalizeDate, Percentage, SelectionKey, Snapshot } from './model';
import { recommendedAction } from './recommendations';

export const actionTitles: Record<SelectionKey, string> = {
  threat: 'Investigate', jailbroken: 'Validate', certificate: 'Renew', criticalStorage: 'Recover',
  noncompliant: 'Remediate', unencrypted: 'Encrypt', update: 'Service', lowStorage: 'Optimize',
  stale: 'Reconnect', management: 'Resolve', threatGap: 'Restore', ownership: 'Reconcile',
  autopilot: 'Prepare'
};

export const reviewLevels = ['Urgent investigation', 'Priority review', 'Remediation review', 'Reconciliation'] as const;

export function validAiIntelligence(snapshot: Snapshot): Snapshot['aiIntelligence'] | undefined {
  const validText = (value: unknown, maximum: number): value is string => typeof value === 'string' && value.trim().length > 0 && value.length <= maximum;
  const value = snapshot.aiIntelligence;
  if (!value || value.status !== 'available' || !value.executive_summary ||
      !['introduction', 'findings', 'bottomLine'].every(key => validText(value.executive_summary[key as keyof typeof value.executive_summary], 700)) ||
      !Array.isArray(value.key_insights) || value.key_insights.length > 13 ||
      !Array.isArray(value.recommendations) || value.recommendations.length > 13) return undefined;
  const validPriority = (priority: string): boolean => ['critical', 'high', 'medium', 'low'].indexOf(priority) >= 0;
  const keys = Object.keys(issues) as SelectionKey[];
  if (value.key_insights.some(item => !item || keys.indexOf(item.key) < 0 || !validText(item.title, 120) || !validText(item.observation, 500) || !validText(item.implication, 500) || !validPriority(item.priority)) ||
      value.recommendations.some(item => !item || keys.indexOf(item.key) < 0 || !validText(item.title, 120) || !validText(item.action, 500) || !validText(item.owner, 350) || !validPriority(item.priority))) return undefined;
  const expected = keys.filter(key => getIssuePercentage(snapshot, key).numerator > 0);
    if (expected.some(key => !value.key_insights.some(item => item.key === key) || !value.recommendations.some(item => item.key === key)) ||
      value.key_insights.some(item => expected.indexOf(item.key) < 0) || value.recommendations.some(item => expected.indexOf(item.key) < 0)) return undefined;
  return value;
}

export function aiRecommendation(snapshot: Snapshot, key: SelectionKey): NonNullable<Snapshot['aiIntelligence']>['recommendations'][number] | undefined {
  return validAiIntelligence(snapshot)?.recommendations.find(item => item.key === key);
}

export function executiveSummary(snapshot: Snapshot): { introduction: string; findings: string; bottomLine: string } {
  const rate = (key: 'compliance' | 'encryption' | 'threat'): string => {
    const metric = healthMetric(snapshot, key);
    return metric && metric.denominator > 0 ? `${formatPercent(metric.percentage)} (${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)})` : 'not available';
  };
  const count = (key: SelectionKey): string => {
    const metric = getIssuePercentage(snapshot, key);
    return `${formatNumber(metric.numerator)} (${formatPercent(metric.percentage)})`;
  };
  const insights = activeInsights(snapshot);
  const focus = insights.filter(item => item.key !== 'autopilot' && item.key !== 'criticalStorage').sort((left, right) => right.metric.numerator - left.metric.numerator).slice(0, 3);
  const urgent = riskRegister(snapshot).filter(entry => entry.priority === 'Urgent investigation').length;
  const patch = healthMetric(snapshot, 'patch');
  const patchText = patch && patch.denominator > 0 ? `Monthly Windows updates meet the required level on ${formatPercent(patch.percentage)} of ${formatNumber(patch.denominator)} supported Windows devices that could be checked.` : 'Monthly Windows update results are not available because the required update information is missing.';
  return {
    introduction: `This report covers ${formatNumber(snapshot.summary.totalDevices)} devices. Reported compliance is ${rate('compliance')}, encryption is ${rate('encryption')}, and devices reporting a secured threat status account for ${rate('threat')}. Each percentage covers a different group; these are not an overall health score. Compliance was checked on ${formatNumber(snapshot.summary.evaluatedDevices)} devices (${formatPercent(snapshot.summary.complianceCoverage.percentage)} of all devices). ${patchText}`,
    findings: `${count('ownership')} devices have no matching user; shared devices may not need one. ${count('criticalStorage')} devices have less than 2% free storage, out of ${formatNumber(snapshot.summary.metrics.storageMeasured || 0)} devices with valid storage measurements. ${count('update')} devices need Windows update or support review; check their Windows edition and support coverage before deciding on an upgrade. ${count('threatGap')} devices have unknown threat status, including unknown or deactivated reporting; this does not mean a threat was detected. Separately, ${formatNumber(snapshot.summary.exceptionCounts.threat || 0)} devices report low, medium or high threat severity. The report also shows ${formatNumber(snapshot.summary.exceptionCounts.unencrypted || 0)} known unencrypted devices, ${formatNumber(snapshot.summary.exceptionCounts.stale || 0)} devices marked inactive by Intune and ${formatNumber(snapshot.summary.exceptionCounts.jailbroken || 0)} devices reporting jailbreak or root access.`,
    bottomLine: `${focus.length ? 'The largest groups needing attention are ' + focus.map(item => item.title.toLowerCase() + ' (' + formatNumber(item.metric.numerator) + ')').join(', ') + '. Assign owners and check the latest device details before taking action.' : 'No issues are reported. Check that the data is complete before deciding no action is needed.'} ${urgent ? formatNumber(urgent) + ' devices need urgent investigation; review these first.' : 'No devices meet the urgent investigation rules, but unreported problems may still exist.'} Check results after changes and confirm any user impact with the service desk. Groups overlap, and missing data does not mean a device is healthy.`
  };
}

export interface ActiveInsight {
  key: SelectionKey;
  title: string;
  metric: Percentage;
  observation: string;
  implication: string;
  action: string;
  owner: string;
}

const guidance: { key: SelectionKey; title: string; implication: string; action: string; owner: string }[] = [
  { key: 'threat', title: 'Reported threats', implication: 'A reported threat level needs investigation, but does not confirm a current incident.', action: 'Check the latest Defender or security partner alert, affected devices and action taken before deciding whether to isolate a device.', owner: 'Security operations' },
  { key: 'jailbroken', title: 'Devices reporting jailbreak or root access', implication: 'Check whether the device still has jailbreak or root access and which policies apply.', action: 'Review device security and any access restrictions with the security owner.', owner: 'Security operations' },
  { key: 'certificate', title: 'Expired management certificates', implication: 'An expired certificate may affect device management; check whether the device can still connect.', action: 'Check the certificate date and last check-in, then renew or enroll the device again through an approved process.', owner: 'Endpoint operations' },
  { key: 'criticalStorage', title: 'Devices with critically low storage', implication: 'Less than 2% free storage can disrupt updates or apps, but no failure has been measured here.', action: 'Check current free space and arrange approved cleanup or more storage before updating.', owner: 'Endpoint operations' },
  { key: 'noncompliant', title: 'Non-compliant devices', implication: 'These devices are reported as non-compliant. Check policy results to find the reason.', action: 'Review compliance results in Intune, fix the failing setting and check again after the device syncs.', owner: 'Compliance operations' },
  { key: 'unencrypted', title: 'Unencrypted devices', implication: 'These devices are known to be unencrypted. Unknown encryption states are not included.', action: 'Check encryption requirements, device readiness and saved recovery keys before making changes.', owner: 'Endpoint security' },
  { key: 'update', title: 'Devices needing updates', implication: 'These Windows devices are behind the required monthly update or have a confirmed unsupported Windows version. This does not mean an installation failed.', action: 'Compare installed and required monthly updates. Check Windows edition, update channel and Extended Security Updates coverage before upgrading.', owner: 'Windows servicing' },
  { key: 'lowStorage', title: 'Devices with low storage', implication: 'Less than 10% free storage includes devices below 2%; do not add these groups together.', action: 'Address devices below 2% first, then check and free space on the remaining devices below 10%.', owner: 'Endpoint operations' },
  { key: 'stale', title: 'Inactive devices', implication: 'Intune marked these devices inactive. A missing or invalid check-in date does not tell us how long they have been inactive.', action: 'Check device use and recent check-ins with the owner before recovering or retiring the device.', owner: 'Endpoint lifecycle' },
  { key: 'management', title: 'Unfinished device actions', implication: 'A pending removal or wipe is not complete. A failed wipe needs follow-up.', action: 'Check the result, device connection and business approval; escalate failed wipes and confirm whether data was removed.', owner: 'Endpoint lifecycle' },
  { key: 'threatGap', title: 'Unknown threat status', implication: 'Threat status is unknown or reporting is deactivated. This does not mean a threat was detected or that protection is working.', action: 'Check whether threat reporting is supported, connected and set up for the device, then check when it last reported.', owner: 'Security operations' },
  { key: 'ownership', title: 'Devices without a matching user', implication: 'The user is missing or could not be matched. Shared devices may not need a named user.', action: 'Confirm whether the device is shared or intentionally has no user, then correct user details where needed. Do not retire it just because a user is missing.', owner: 'Endpoint lifecycle' },
  { key: 'autopilot', title: 'Autopilot devices not enrolled', implication: 'These registrations may include stock awaiting deployment. They are counted separately from managed devices.', action: 'Check deployment plans and device assignments before investigating enrollment or removing registrations.', owner: 'Enrollment operations' }
];

export function activeInsights(snapshot: Snapshot): ActiveInsight[] {
  return guidance.map(item => {
    const metric = getIssuePercentage(snapshot, item.key);
    const ai = validAiIntelligence(snapshot)?.key_insights.find(insight => insight.key === item.key);
    const recommendation = aiRecommendation(snapshot, item.key);
    return { ...item, ...(ai ? { title: ai.title, observation: ai.observation, implication: ai.implication } : {}), ...(recommendation ? { action: recommendation.action, owner: recommendation.owner } : {}), metric,
      observation: ai?.observation || `${formatNumber(metric.numerator)} of ${formatNumber(metric.denominator)} (${formatPercent(metric.percentage)}). ${metric.denominatorLabel}.` };
  }).filter(item => item.metric.numerator > 0);
}

export function evidenceWarnings(snapshot: Snapshot): string[] {
  const warnings: string[] = [];
  const status = snapshot.coverage.collectionStatus as { status?: string } | undefined;
  if (status?.status !== 'complete') warnings.push('Data collection is ' + (status?.status || 'not provided') + '. Missing data does not mean there are no issues.');
  const metrics = snapshot.summary.metrics;
  [['storageUnknown', 'valid storage measurements'], ['encryptionUnknown', 'a known encryption status'], ['activityUnknown', 'a valid check-in date that is not in the future']].forEach(([key, label]) => {
    if (metrics[key] > 0) warnings.push(formatNumber(metrics[key]) + ' devices lack ' + label + '.');
  });
  if (!snapshot.windowsPatchSummary || !Object.keys(snapshot.windowsPatchSummary).length) warnings.push('Required monthly Windows update information is missing. Windows age alone does not tell us whether it is still supported.');
  return warnings;
}

export interface RiskEntry {
  record: DeviceRecord;
  priority: typeof reviewLevels[number];
  rationale: string;
  evidence: string[];
  action: string;
}

export function riskEntry(record: DeviceRecord, snapshot?: Snapshot): RiskEntry {
  const flags = new Set(record.issues);
  const controlKeys: IssueKey[] = ['noncompliant', 'unencrypted', 'stale', 'update'];
  const controlCount = controlKeys.filter(key => flags.has(key)).length;
  const urgent = (flags.has('threat') && lower(record.derived.threatStatus) === 'high') || flags.has('jailbroken') || (flags.has('management') && lower(record.managementState) === 'wipefailed');
  const priority = urgent ? reviewLevels[0] : flags.has('threat') || flags.has('certificate') || flags.has('criticalStorage') || controlCount >= 2 ? reviewLevels[1] : controlCount > 0 || flags.has('lowStorage') || flags.has('management') ? reviewLevels[2] : reviewLevels[3];
  const evidence: string[] = [];
  const keys = Array.from(flags).filter(key => key !== 'criticalStorage' || !flags.has('lowStorage'));
  keys.forEach(key => {
    switch (key) {
      case 'threat': case 'threatGap': evidence.push('Threat status: ' + display(record.derived.threatStatus)); break;
      case 'lowStorage': case 'criticalStorage': evidence.push('Free device storage: ' + formatPercent(record.derived.freeStoragePercent)); break;
      case 'stale': evidence.push('Marked inactive by Intune; ' + (normalizeDate(record.derived.lastSyncDate) && record.derived.daysInactive !== null ? formatNumber(record.derived.daysInactive) + ' inactive days when collected' : 'time inactive is unknown')); break;
      case 'ownership': evidence.push('Matching user: ' + display(record.derived.userStatus)); break;
      case 'management': evidence.push('Device action status: ' + display(record.managementState)); break;
      case 'update': evidence.push('Monthly update: ' + display(record.updateDetail?.patchStatus || record.derived.patchStatus) + '; Windows support: ' + display(record.updateDetail?.supportStatus)); break;
      default: if (Object.prototype.hasOwnProperty.call(issues, key)) evidence.push(issues[key as IssueKey].label);
    }
  });
  const selected = guidance.filter(item => flags.has(item.key));
  const urgentKey = flags.has('threat') && lower(record.derived.threatStatus) === 'high' ? 'threat' : flags.has('jailbroken') ? 'jailbroken' : urgent ? 'management' : undefined;
  const nextAction = selected.find(item => item.key === urgentKey) || selected[0];
  const rationale = urgent ? 'High reported threat level, jailbreak or root access, or a failed wipe.' : priority === reviewLevels[1] ? 'Reported threat, expired certificate, critically low storage or two or more compliance, encryption, activity or update issues.' : priority === reviewLevels[2] ? 'A compliance, encryption, activity, update, storage or device action issue needs review.' : 'Check reporting, user details or enrollment; no confirmed security issue.';
  const aiAction = snapshot && nextAction ? aiRecommendation(snapshot, nextAction.key)?.action : undefined;
  return { record, priority, rationale, evidence, action: aiAction || (nextAction ? recommendedAction(record, nextAction.key) : 'Review the reported device details.') };
}

export function riskRegister(snapshot: Snapshot): RiskEntry[] {
  return snapshot.devices.map(record => riskEntry(record, snapshot)).sort((left, right) => reviewLevels.indexOf(left.priority) - reviewLevels.indexOf(right.priority) || right.record.issues.length - left.record.issues.length || display(left.record.deviceName).localeCompare(display(right.record.deviceName)));
}