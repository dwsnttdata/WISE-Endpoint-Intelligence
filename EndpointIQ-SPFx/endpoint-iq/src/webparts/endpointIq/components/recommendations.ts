import { display, JsonRecord, lower, SelectionKey } from './model';

export interface PolicyFinding { policy: string; setting: string; status: string; action: string; }
function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) as JsonRecord[] : [];
}
function needsAttention(value: unknown): boolean {
  return ['noncompliant', 'error', 'conflict'].indexOf(lower(value)) >= 0;
}
export function policyFindings(record: JsonRecord): PolicyFinding[] {
  const detail = record.complianceDetail as JsonRecord | undefined;
  return records(detail?.policyStates).reduce<PolicyFinding[]>((result, policy) => {
    const settings = records(policy.settingStates).filter(setting => needsAttention(setting.state));
    const findings = settings.length ? settings : needsAttention(policy.state) ? [undefined] : [];
    findings.forEach(setting => {
      const state = lower(setting ? setting.state : policy.state);
      const name = display(policy.displayName || policy.id);
      const settingName = setting ? display(setting.settingName || setting.setting) : 'Setting details not available';
      const status = state === 'error' ? 'Could not check policy' : state === 'conflict' ? 'Conflicting policy settings' : 'Not compliant';
      const action = state === 'error' ? `Check the reported error for ${name} in Intune, sync the device and check again.` : state === 'conflict' ? `Review overlapping settings for ${name} in Intune and agree the correct value before changing policy.` : `Open ${name} in Intune${setting ? ', review ' + settingName : ' and open its setting results'}, correct the reported failure, sync and check compliance again.`;
      const error = setting?.errorDescription || setting?.errorCode;
      result.push({ policy: name, setting: settingName, status: status + (error ? ': ' + display(error) : ''), action });
    });
    return result;
  }, []);
}
export function policySummary(record: JsonRecord): string {
  const findings = policyFindings(record);
  return findings.length ? Array.from(new Set(findings.map(item => item.policy + ': ' + item.status))).join('; ') : 'Policy failure details not available';
}
export function recommendedAction(record: JsonRecord, issue: SelectionKey): string {
  switch (issue) {
    case 'noncompliant': {
      const findings = policyFindings(record);
      return findings.length ? Array.from(new Set(findings.map(item => item.action))).join(' ') : 'Open the device in Intune > Device compliance, check the failing policy and setting, correct the reported issue, then sync and check again.';
    }
    case 'unencrypted': {
      if (record.isEncrypted !== false) return 'Encryption status is not confirmed as off. Check a fresh device report before changing encryption.';
      const detail = record.encryptionDetail as JsonRecord | undefined;
      const readiness = lower(detail?.encryptionReadinessState || detail?.encryptionReadiness);
      const assignment = lower(detail?.encryptionPolicySettingState);
      const platform = lower(record.operatingSystem);
      const cloud = lower(record.model).indexOf('cloud pc') >= 0;
      if (cloud) return 'Confirm Windows 365 encryption and protection requirements with the Cloud PC owner. An Intune encryption flag alone does not establish whether service-managed storage is unprotected.';
      const preparation = readiness === 'notready' || readiness === 'not ready' ? 'Intune reports this device is not ready for encryption. Check its readiness details and resolve prerequisites first. ' : '';
      const policy = assignment === 'notassigned' ? 'The encryption report shows no assigned policy; verify assignments and exclusions. ' : '';
      const technology = platform === 'windows' ? 'BitLocker' : platform === 'macos' || platform === 'mac os x' ? 'FileVault' : 'platform-supported encryption';
      return preparation + policy + `Verify ${technology} requirements and recovery-key backup, apply the approved encryption policy to eligible devices, then confirm encryption after sync. Never clear the TPM or decrypt a drive as a first step.`;
    }
    case 'criticalStorage': case 'lowStorage': return 'Check current free space. Use approved cleanup, protect user files and confirm backups. Prioritize devices below 2% free, then expand storage if cleanup is insufficient; check free space again.';
    case 'stale': return 'Contact the user, confirm the device is still in use, check power and connectivity, then sync. Recover active devices; remove retired devices only with approval and data-protection checks.';
    case 'threat': return 'Review the latest Defender or security-partner alert with the security team. Confirm scope and current status before approved containment; verify the threat is resolved afterward.';
    case 'threatGap': return 'Check whether threat reporting is supported for this platform. Verify the security connector, device onboarding and last report; sync and confirm a known status. Unknown or turned-off reporting is not a detected threat.';
    case 'update': return updateAction((record.updateDetail as JsonRecord | undefined) || record);
    case 'ownership': return 'Confirm whether this is a shared or userless device. For a personally assigned device, correct the primary user in Intune and verify the next report.';
    case 'certificate': return 'Check certificate expiry and the last check-in. Use the approved certificate renewal or re-enrollment process, then confirm management connectivity.';
    case 'management': return lower(record.managementState) === 'wipefailed' ? 'Escalate the failed wipe to the security team. Confirm connectivity and approval before retrying; do not assume device data was removed.' : 'Check the pending removal or wipe result and device connectivity. Retry only with approval, then verify completion.';
    case 'jailbroken': return 'Ask the security team to verify the jailbreak or root report and applicable access policy. Recover or replace the device through an approved process and verify its status.';
    case 'autopilot': return 'Confirm the planned deployment date. For devices due for deployment, check the assigned Autopilot profile, group membership and enrollment errors. Keep spare stock separate from failed enrollments.';
  }
}
export function updateAction(detail: JsonRecord): string {
  const state = lower(detail.patchStatus);
  if (state === 'not applicable') return 'Use the update process for this device platform; the Windows monthly update comparison does not apply.';
  if (state === 'unsupported os') return 'Verify Windows edition, support dates and any Extended Security Updates entitlement. Plan an approved upgrade to a supported version, test applications and confirm the new version.';
  if (state === 'non-compliant') return 'Verify current build and support eligibility, deploy the approved monthly update' + (detail.requiredKB ? ' (' + display(detail.requiredKB) + ')' : '') + ', arrange any required restart and confirm the installed build after sync.';
  if (state === 'compliant') return 'The device meets the collected monthly update baseline. Continue scheduled updates and verify the next report.';
  return 'Confirm Windows version, edition and update channel in Intune. Check the applicable monthly baseline before deciding whether an update is needed; an unknown build does not prove installation failure.';
}