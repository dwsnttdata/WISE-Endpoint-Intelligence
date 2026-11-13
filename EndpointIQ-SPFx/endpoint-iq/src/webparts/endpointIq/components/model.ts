export interface JsonRecord { [key: string]: unknown; }

export type HealthKey = 'compliance' | 'encryption' | 'patch' | 'threat' | 'activity' | 'management';
export interface Percentage {
  numerator: number;
  denominator: number;
  percentage: number | null;
  denominatorLabel: string;
}
export interface HealthMetric extends Percentage { key: HealthKey; label: string; }
export type IssueKey = 'noncompliant' | 'lowStorage' | 'criticalStorage' | 'unencrypted' | 'stale' | 'threat' | 'threatGap' | 'update' | 'ownership' | 'certificate' | 'management' | 'jailbroken';
export type SelectionKey = IssueKey | 'autopilot';
export type Tab = 'overview' | 'compliance' | 'storage' | 'encryption' | 'activity' | 'threats' | 'updates' | 'enrollment' | 'experience' | 'risk';
export interface DeviceRecord extends JsonRecord {
  derived: JsonRecord & {
    freeStoragePercent: number | null;
    daysInactive: number | null;
    lastSyncDate: string | null;
    staleFlag: boolean | null;
    threatStatus: string;
    patchStatus: string;
  };
  issues: string[];
  complianceDetail: JsonRecord | null;
  updateDetail: JsonRecord | null;
  usedStorageSpaceGB: number | null;
}
export interface AiIntelligence {
  status: 'available';
  executive_summary: { introduction: string; findings: string; bottomLine: string };
  key_insights: { key: SelectionKey; title: string; observation: string; implication: string; priority: 'critical' | 'high' | 'medium' | 'low' }[];
  recommendations: { key: SelectionKey; title: string; action: string; owner: string; priority: 'critical' | 'high' | 'medium' | 'low' }[];
}
export interface Snapshot {
  schemaVersion?: number;
  metadata: JsonRecord;
  summary: {
    totalDevices: number;
    health: HealthMetric[];
    exceptionCounts: Partial<Record<IssueKey, number>>;
    percentages: Partial<Record<IssueKey, Percentage>>;
    metrics: Record<string, number>;
    evaluatedDevices: number;
    exceptionDevices: number;
    complianceCoverage: Percentage;
    autopilotRegistered: number;
    autopilotEnrolled: number;
    autopilotExceptions: number;
  };
  devices: DeviceRecord[];
  autopilot: JsonRecord[];
  updateRings: JsonRecord[];
  windowsPatchSummary?: JsonRecord;
  windowsPatchDevices?: JsonRecord[];
  distributions: Record<string, { label: string; count: number }[]>;
  policySummary: JsonRecord | null;
  coverage: JsonRecord;
  aiIntelligence?: AiIntelligence;
}
export const tabs: { key: Tab; label: string; issue?: SelectionKey }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'risk', label: 'Risk Details' },
  { key: 'compliance', label: 'Compliance', issue: 'noncompliant' },
  { key: 'storage', label: 'Storage', issue: 'lowStorage' },
  { key: 'encryption', label: 'Encryption', issue: 'unencrypted' },
  { key: 'activity', label: 'Activity', issue: 'stale' },
  { key: 'threats', label: 'Threats', issue: 'threat' },
  { key: 'updates', label: 'Updates', issue: 'update' },
  { key: 'enrollment', label: 'Enrollment', issue: 'autopilot' },
  { key: 'experience', label: 'User Experience' }
];
export const issues: Record<SelectionKey, { label: string; tab: Tab; note: string; tone: string }> = {
  noncompliant: { label: 'Non-compliant devices', tab: 'compliance', note: 'Intune reports these devices do not meet compliance requirements. See policy results and next actions below.', tone: 'red' },
  lowStorage: { label: 'Low storage space', tab: 'storage', note: 'Less than 10% free space across the device. Individual drives are not identified.', tone: 'amber' },
  criticalStorage: { label: 'Critically low storage space', tab: 'storage', note: 'Less than 2% free space. These devices are also included in the low-storage count.', tone: 'red' },
  unencrypted: { label: 'Unencrypted devices', tab: 'encryption', note: 'Intune reports encryption is off. Devices with unknown encryption status are not counted here.', tone: 'red' },
  stale: { label: 'Inactive devices', tab: 'activity', note: 'Intune marked these devices inactive when this report was collected.', tone: 'amber' },
  threat: { label: 'Reported threats', tab: 'threats', note: 'A security provider reports a low, medium or high threat level. Check the latest alert before taking action.', tone: 'red' },
  threatGap: { label: 'Unknown threat status', tab: 'threats', note: 'Threat status is unknown or reporting is turned off. This does not mean a threat was detected.', tone: 'amber' },
  update: { label: 'Devices needing updates', tab: 'updates', note: 'Windows devices below the monthly update baseline or running a confirmed unsupported version. This percentage uses all devices, not just Windows devices eligible for the monthly update check.', tone: 'amber' },
  ownership: { label: 'Devices without a matching user', tab: 'activity', note: 'The user is missing or could not be matched. Shared devices may not need a named user.', tone: 'amber' },
  certificate: { label: 'Expired management certificates', tab: 'compliance', note: 'Management certificate explicitly marked expired.', tone: 'red' },
  management: { label: 'Unfinished device actions', tab: 'activity', note: 'Device removal or wipe is waiting to finish, or the wipe failed.', tone: 'red' },
  jailbroken: { label: 'Jailbreak or root access reported', tab: 'encryption', note: 'Intune reports the device operating system has been modified. Ask the security team to verify it.', tone: 'red' },
  autopilot: { label: 'Autopilot devices not enrolled', tab: 'enrollment', note: 'Autopilot reports these registrations are not enrolled. This can include stock waiting for deployment.', tone: 'amber' }
};
export const healthIssues: Record<HealthKey, IssueKey> = {
  compliance: 'noncompliant', encryption: 'unencrypted', patch: 'update', threat: 'threat', activity: 'stale', management: 'management'
};
export const overviewIssues: SelectionKey[] = ['noncompliant', 'lowStorage', 'unencrypted', 'stale', 'threat', 'update', 'ownership', 'autopilot'];
export const lower = (value: unknown): string => String(value === null || value === undefined ? '' : value).trim().toLowerCase();

export function getIssueRecords(snapshot: Snapshot, key: SelectionKey): JsonRecord[] {
  if (!Object.prototype.hasOwnProperty.call(issues, key)) return [];
  if (key === 'autopilot') return snapshot.autopilot.filter(record => lower(record.enrollmentState) !== 'enrolled');
  return snapshot.devices.filter(record => record.issues.indexOf(key) >= 0);
}
export function getIssuePercentage(snapshot: Snapshot, key: SelectionKey): Percentage {
  if (key === 'autopilot') {
    const numerator = snapshot.summary.autopilotExceptions;
    const denominator = snapshot.summary.autopilotRegistered;
    return { numerator, denominator, percentage: denominator ? Number((numerator / denominator * 100).toFixed(1)) : null, denominatorLabel: 'All Autopilot registrations' };
  }
  const supplied = snapshot.summary.percentages[key];
  if (supplied) return supplied;
  const numerator = snapshot.summary.exceptionCounts[key] || 0;
  const denominator = key === 'lowStorage' || key === 'criticalStorage' ? snapshot.summary.metrics.storageMeasured : snapshot.summary.totalDevices;
  return {
    numerator,
    denominator,
    percentage: denominator ? Number((numerator / denominator * 100).toFixed(1)) : null,
    denominatorLabel: key === 'lowStorage' || key === 'criticalStorage' ? 'Devices with valid storage measurements' : 'All source devices'
  };
}
export const formatNumber = (value: number): string => value.toLocaleString('en-US');
export function fieldLabel(key: string): string {
  const words = key.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2').replace(/([a-z0-9])([A-Z])/g, '$1 $2');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
export function statusLabel(value: unknown): string {
  const labels: Record<string, string> = { noncompliant: 'Not compliant', compliant: 'Compliant', notContacted: 'No contact reported', retirePending: 'Removal pending', wipePending: 'Wipe pending', wipeFailed: 'Wipe failed', deactivated: 'Reporting turned off', unknown: 'Unknown', notReady: 'Not ready', notAssigned: 'Not assigned', secured: 'No threat reported', low: 'Low reported threat', medium: 'Medium reported threat', high: 'High reported threat', 'Encryption false': 'Not encrypted', 'Source stale flag': 'Marked inactive by Intune' };
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(labels, value) ? labels[value] : display(value);
}
export function displayDate(value: unknown): string {
  const date = normalizeDate(value);
  return date ? new Date(date).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }) : display(value);
}
export function executionTime(snapshot: Snapshot): { timestamp: string | null; label: string } {
  const completed = normalizeDate(snapshot.metadata.executionCompletedAtUtc);
  return completed ? { timestamp: completed, label: 'Last execution (UTC)' } : {
    timestamp: normalizeDate(snapshot.metadata.collectionTimestamp) || normalizeDate(snapshot.coverage.collectionDate),
    label: 'Last collection (UTC)'
  };
}
export const formatPercent = (value: number | null): string => value === null || !isFinite(value) ? 'Not available' : value.toFixed(1) + '%';
export function normalizeDate(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const legacy = /^\/Date\((-?\d+)(?:[+-]\d{4})?\)\/$/.exec(value);
  const iso = /^(\d{4})-(\d{2})-(\d{2})(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$/.exec(value);
  if (!legacy && !iso) return null;
  if (iso) {
    const year = Number(iso[1]);
    const month = Number(iso[2]);
    const day = Number(iso[3]);
    if (year <= 1 || month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null;
  }
  const date = new Date(legacy ? Number(legacy[1]) : value);
  return isFinite(date.getTime()) && date.getUTCFullYear() > 1 ? date.toISOString() : null;
}
export function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not provided';
  if (typeof value === 'object') return JSON.stringify(value);
  return normalizeDate(value) || String(value);
}
export function searchable(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (Array.isArray(value)) return value.map(searchable).join(' ');
  if (typeof value === 'object') return Object.keys(value).map(key => key + ' ' + searchable((value as JsonRecord)[key])).join(' ');
  return String(value) + ' ' + (normalizeDate(value) || '');
}
export function csvCell(value: unknown): string {
  let text = display(value);
  let offset = 0;
  while (offset < text.length && (/\s/.test(text[offset]) || text.charCodeAt(offset) < 32)) offset++;
  if ('=+@-'.indexOf(text[offset] || ' ') >= 0 || /^[\t\r\n]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}
export function exportCsv(records: JsonRecord[]): string {
  const fields: string[] = [];
  records.forEach(record => Object.keys(record).forEach(key => { if (fields.indexOf(key) < 0) fields.push(key); }));
  return '\uFEFF' + [fields.map(csvCell).join(','), ...records.map(record => fields.map(key => csvCell(record[key])).join(','))].join('\r\n');
}
export interface TableFilters { query: string; os: string; manufacturer: string; sortKey: string; descending: boolean; page: number; pageSize: 25 | 50; }
export function resetTableFilters(): TableFilters {
  return { query: '', os: '', manufacturer: '', sortKey: '', descending: false, page: 0, pageSize: 25 };
}
export function filterIssueRecords(snapshot: Snapshot, key: SelectionKey, filters: TableFilters): JsonRecord[] {
  return getIssueRecords(snapshot, key).filter(record =>
    (!filters.query || lower(searchable(record)).indexOf(lower(filters.query)) >= 0) &&
    (!filters.os || display(record.operatingSystem) === filters.os) &&
    (!filters.manufacturer || display(record.manufacturer) === filters.manufacturer));
}
export function pageRecords(records: JsonRecord[], page: number, pageSize: 25 | 50): JsonRecord[] {
  const safeSize = pageSize === 50 ? 50 : 25;
  const safePage = Math.max(0, Math.min(Math.floor(page) || 0, Math.max(0, Math.ceil(records.length / safeSize) - 1)));
  return records.slice(safePage * safeSize, (safePage + 1) * safeSize);
}
export function healthMetric(snapshot: Snapshot, key: HealthKey): HealthMetric | undefined {
  return snapshot.summary.health.filter(metric => metric.key === key)[0];
}
export function validateSnapshot(value: Snapshot): void {
  if (!value || !value.metadata || !value.coverage || !value.summary || !Array.isArray(value.devices) || !Array.isArray(value.autopilot) ||
      !Array.isArray(value.updateRings) || !value.distributions || !Array.isArray(value.summary.health) || !value.summary.percentages || !value.summary.exceptionCounts || !value.summary.metrics || !value.summary.complianceCoverage) {
    throw new Error('The management snapshot is missing required aggregates or exception records.');
  }
  const keys: HealthKey[] = ['compliance', 'encryption', 'patch', 'threat', 'activity', 'management'];
  if (value.summary.health.length !== keys.length || keys.some(key => !healthMetric(value, key)) || value.summary.health.some(metric =>
    !isFinite(metric.numerator) || !isFinite(metric.denominator) || metric.denominator < 0 || !metric.denominatorLabel ||
    (metric.percentage !== null && (!isFinite(metric.percentage) || metric.percentage < 0 || metric.percentage > 100))) ||
    value.devices.some(device => !Array.isArray(device.issues) || !device.issues.length || !device.derived) ||
    value.autopilot.some(record => lower(record.enrollmentState) === 'enrolled')) {
    throw new Error('The snapshot does not match the aggregate and exception-only contract.');
  }
  if (value.devices.length !== value.summary.exceptionDevices || value.autopilot.length !== value.summary.autopilotExceptions ||
      (Object.keys(issues) as SelectionKey[]).some(key => key !== 'autopilot' && getIssueRecords(value, key).length !== (value.summary.exceptionCounts[key] || 0))) {
    throw new Error('Exception records do not reconcile with the supplied summary.');
  }
}