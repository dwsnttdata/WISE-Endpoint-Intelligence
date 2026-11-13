import * as React from 'react';
import { createTheme, DefaultButton, Icon, IconButton, MessageBar, MessageBarType, Panel, PanelType, PrimaryButton, Spinner, SpinnerSize, ThemeProvider } from '@fluentui/react';
import { initializeIcons } from '@fluentui/react/lib/Icons';
import styles from './EndpointIq.module.scss';
import { IEndpointIqProps } from './IEndpointIqProps';
import { displayDate, executionTime, fieldLabel, statusLabel } from './model';
import { Assessment, RiskDetails, UserExperience } from './Intelligence';
import { aiRecommendation, riskEntry } from './insights';
import { PolicyFinding, policyFindings, policySummary, recommendedAction, updateAction } from './recommendations';
import { DeviceRecord, display, exportCsv, filterIssueRecords, formatNumber, formatPercent, getIssuePercentage, getIssueRecords, healthIssues, healthMetric, HealthMetric, issues, JsonRecord, overviewIssues, pageRecords, resetTableFilters, SelectionKey, Snapshot, Tab, TableFilters, tabs, validateSnapshot } from './model';

initializeIcons();
const theme = createTheme({
  isInverted: true,
  palette: { themePrimary: '#38cbbb', themeDarkAlt: '#66ddcc', themeLight: '#24665f', themeLighter: '#193c3c', themeLighterAlt: '#17212b', white: '#17212b', black: '#e8eef3', neutralLighterAlt: '#0f1720', neutralLighter: '#1e2c37', neutralLight: '#30414c', neutralQuaternaryAlt: '#30414c', neutralQuaternary: '#637987', neutralTertiary: '#a6bac7', neutralSecondary: '#b8c9d3', neutralPrimary: '#e8eef3', neutralDark: '#ffffff' },
  semanticColors: { bodyBackground: '#0f1720', bodyText: '#e8eef3', inputBackground: '#17212b', inputText: '#e8eef3', inputBorder: '#637987', focusBorder: '#66ddcc' },
  defaultFontStyle: { fontFamily: 'Segoe UI, Tahoma, sans-serif' }
});

function ObjectView({ value, name }: { value: unknown; name: string }): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  if (value === null || typeof value !== 'object') return <span className="ei-value">{display(value)}</span>;
  const keys = Object.keys(value);
  return <details className="ei-object" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{name} <span className="ei-muted">({keys.length} {Array.isArray(value) ? 'items' : 'fields'})</span></summary>
    {open && <dl>{keys.map(key => <div key={key}><dt>{fieldLabel(key)}</dt><dd><ObjectView value={(value as JsonRecord)[key]} name={fieldLabel(key)} /></dd></div>)}</dl>}
  </details>;
}

function Ratio({ metric }: { metric: { numerator: number; denominator: number; denominatorLabel: string } }): React.ReactElement {
  const labels: Record<string, string> = {
    'Intune compliance cohort; excludes Configuration Manager': 'Devices evaluated by Intune; excludes Configuration Manager',
    'Intune compliance cohort': 'Devices evaluated by Intune',
    'Devices with explicit encryption state': 'Devices with known encryption status',
    'Secured or severity-reported devices; unknown/deactivated excluded': 'Devices with a reported threat result; excludes unknown or turned-off reporting',
    'Devices with an explicit source stale flag': 'Devices with a reported inactivity status',
    'Supported Windows devices with a published Microsoft monthly B baseline': 'Supported Windows devices with a published monthly security build'
  };
  const label = Object.prototype.hasOwnProperty.call(labels, metric.denominatorLabel) ? labels[metric.denominatorLabel] : metric.denominatorLabel;
  return <span className="ei-denominator" title={label}>{formatNumber(metric.numerator)} / {formatNumber(metric.denominator)}<span>{label}</span></span>;
}

function HealthBar({ metric, onSelect }: { metric: HealthMetric; onSelect: (key: SelectionKey) => void }): React.ReactElement {
  const issue = healthIssues[metric.key];
  const labels: Record<string, string> = {
    Compliance: 'Compliant devices', Encryption: 'Encrypted devices',
    'Current / recent OS classification': 'OS classified as current or recent (legacy)',
    'Windows monthly quality patch compliance': 'Windows monthly patch compliance',
    'Secured threat reports': 'No threat reported', 'Not flagged stale': 'Not marked inactive by Intune', 'Managed state': 'Devices in managed state'
  };
  const label = Object.prototype.hasOwnProperty.call(labels, metric.label) ? labels[metric.label] : metric.label;
  return <button type="button" className={'ei-health-row ' + issues[issue].tone} title={'Review exceptions: ' + issues[issue].label + '. ' + metric.denominatorLabel}
    aria-label={label + ', ' + formatPercent(metric.percentage) + '. Review exceptions: ' + issues[issue].label} onClick={() => onSelect(issue)}>
    <span className="ei-health-label">{label}</span><strong>{formatPercent(metric.percentage)}</strong>
    <span className="ei-track" aria-hidden="true"><span style={{ width: (metric.percentage === null ? 0 : Math.min(100, Math.max(0, metric.percentage))) + '%' }} /></span>
    <Ratio metric={metric} /><Icon iconName="ChevronRight" className="ei-review" />
  </button>;
}

function Overview({ snapshot, onSelect }: { snapshot: Snapshot; onSelect: (key: SelectionKey) => void }): React.ReactElement {
  const compliance = healthMetric(snapshot, 'compliance');
  const encryption = healthMetric(snapshot, 'encryption');
  const activity = healthMetric(snapshot, 'activity');
  const top = [
    { label: 'Compliance', metric: compliance, issue: 'noncompliant' as SelectionKey, tone: 'teal' },
    { label: 'Encryption', metric: encryption, issue: 'unencrypted' as SelectionKey, tone: 'blue' },
    { label: 'Not marked inactive by Intune', metric: activity, issue: 'stale' as SelectionKey, tone: 'green' },
    { label: 'Reported threats', metric: getIssuePercentage(snapshot, 'threat'), issue: 'threat' as SelectionKey, tone: 'red' }
  ];
  const priorities: SelectionKey[] = ['noncompliant', 'criticalStorage', 'threatGap'];
  return <>
    <Assessment snapshot={snapshot} onSelect={onSelect} />
    <div className="ei-kpis">{top.map(item => <button key={item.label} className={'ei-kpi ' + item.tone} type="button" title={'Review exceptions: ' + issues[item.issue].label} onClick={() => onSelect(item.issue)}>
      <span className="ei-kpi-label">{item.label}<Icon iconName="ChevronRight" /></span>
      <strong>{formatPercent(item.metric ? item.metric.percentage : null)}</strong>{item.metric && <Ratio metric={item.metric} />}
    </button>)}</div>
    <section className="ei-band ei-health"><div className="ei-section-heading"><h2>Overall endpoint health</h2><span className="ei-muted">Independent source indicators</span></div>
      <div className="ei-health-layout"><div className="ei-score"><span className="ei-eyebrow">Compliance</span><strong>{formatPercent(compliance ? compliance.percentage : null)}</strong>
        {compliance && <Ratio metric={compliance} />}<DefaultButton iconProps={{ iconName: 'Filter' }} text="Review exceptions" title="Review non-compliant devices" onClick={() => onSelect('noncompliant')} />
      </div><div className="ei-bars">{snapshot.summary.health.map(metric => <HealthBar key={metric.key} metric={metric} onSelect={onSelect} />)}</div></div>
      <p className="ei-note">These are separate measures, not a combined score. Monthly patch compliance covers eligible supported Windows devices. Older reports instead show the share of all devices with an OS classified as current or recent. Devices with unknown or turned-off threat reporting are excluded from the no-threat-reported measure.</p>
    </section>
    <section className="ei-band"><div className="ei-section-heading"><h2>Operational exceptions</h2><span className="ei-muted">Devices may appear in more than one group</span></div>
      <div className="ei-exceptions">{overviewIssues.map(key => {
        const metric = getIssuePercentage(snapshot, key);
        return <button type="button" className={'ei-exception ' + issues[key].tone} key={key} onClick={() => onSelect(key)} title={'Review exceptions: ' + issues[key].label + '. ' + issues[key].note}>
          <span className="ei-kpi-label">{issues[key].label}<Icon iconName="ChevronRight" /></span><strong>{formatPercent(metric.percentage)}</strong>
          <span className="ei-secondary-count">{formatNumber(metric.numerator)} exceptions</span><Ratio metric={metric} />
        </button>;
      })}</div>
    </section>
    <section className="ei-band"><h2>Management priorities</h2><div className="ei-priorities">{priorities.map(key => {
      const metric = getIssuePercentage(snapshot, key);
      return <div className="ei-priority" key={key}><h3>{issues[key].label}</h3><p><strong>{formatNumber(metric.numerator)}</strong> exceptions ({formatPercent(metric.percentage)})</p><Ratio metric={metric} /><p className="ei-note">{issues[key].note}</p>
        <DefaultButton iconProps={{ iconName: 'ChevronRight' }} text="Review exceptions" title={issues[key].label} onClick={() => onSelect(key)} /></div>;
    })}</div></section>
    <section className="ei-band"><h2>Compliance data sources</h2><p className="ei-note">The policy summary and device inventory report separate totals. Non-compliant devices: {display(snapshot.policySummary?.nonCompliantDeviceCount)} in the policy summary; {formatNumber(snapshot.summary.exceptionCounts.noncompliant || 0)} in the inventory. The device table uses inventory results. Policy results and recommended actions are shown in Compliance when available.</p>
      {snapshot.policySummary ? <ObjectView value={snapshot.policySummary} name="Source policy summary" /> : <p className="ei-note">Policy summary not provided.</p>}
    </section>
  </>;
}

interface Column { key: string; label: string; value: (record: JsonRecord) => unknown; }
function derived(record: JsonRecord, key: string): unknown { return (record.derived as DeviceRecord['derived'] | undefined)?.[key]; }
function evidence(record: JsonRecord, key: SelectionKey): unknown {
  switch (key) {
    case 'noncompliant': return record.complianceState;
    case 'unencrypted': return record.isEncrypted === false ? 'Encryption false' : record.isEncrypted;
    case 'stale': return derived(record, 'staleFlag') === true ? 'Source stale flag' : null;
    case 'threat': case 'threatGap': return derived(record, 'threatStatus');
    case 'update': return derived(record, 'patchStatus');
    case 'ownership': return derived(record, 'userStatus');
    case 'certificate': return record.managementCertificateExpired === true ? 'Certificate expired' : null;
    case 'management': return record.managementState;
    case 'jailbroken': return record.jailBroken;
    default: return null;
  }
}
function columnsFor(snapshot: Snapshot, key: SelectionKey): Column[] {
  if (key === 'autopilot') return [
    { key: 'serial', label: 'Serial', value: record => record.serialNumber },
    { key: 'manufacturer', label: 'Manufacturer', value: record => record.manufacturer },
    { key: 'model', label: 'Model', value: record => record.model },
    { key: 'enrollment', label: 'Enrollment state', value: record => record.enrollmentState },
    { key: 'group', label: 'Group tag', value: record => record.groupTag },
    { key: 'contact', label: 'Last contact', value: record => record.lastContacted || record.lastContactedDateTime },
    { key: 'recommendation', label: 'Recommended next action', value: record => aiRecommendation(snapshot, key)?.action || recommendedAction(record, key) }
  ];
  const columns: Column[] = [
    { key: 'device', label: 'Device', value: record => record.deviceName },
    { key: 'user', label: 'User', value: record => record.userPrincipalName || record.userDisplayName },
    { key: 'os', label: 'OS', value: record => record.operatingSystem },
    { key: 'model', label: 'Model', value: record => record.model }
  ];
  if (key === 'lowStorage' || key === 'criticalStorage') columns.push(
    { key: 'free', label: 'Free GB', value: record => record.freeStorageSpaceGB },
    { key: 'total', label: 'Total GB', value: record => record.totalStorageSpaceGB },
    { key: 'used', label: 'Used GB', value: record => record.usedStorageSpaceGB },
    { key: 'percent', label: 'Free %', value: record => derived(record, 'freeStoragePercent') }
  );
  else if (key === 'update') columns.push(
    { key: 'patchStatus', label: 'Quality patch', value: record => (record.updateDetail as JsonRecord | undefined)?.patchStatus },
    { key: 'supportStatus', label: 'OS support', value: record => (record.updateDetail as JsonRecord | undefined)?.supportStatus },
    { key: 'installedBuild', label: 'Installed build', value: record => (record.updateDetail as JsonRecord | undefined)?.installedBuild },
    { key: 'requiredBuild', label: 'Required monthly build', value: record => (record.updateDetail as JsonRecord | undefined)?.requiredBuild },
    { key: 'requiredKB', label: 'Required KB', value: record => (record.updateDetail as JsonRecord | undefined)?.requiredKB },
    { key: 'update', label: 'Feature / policy state', value: record => (record.updateDetail as JsonRecord | undefined)?.featureUpdateStatus || (record.updateDetail as JsonRecord | undefined)?.ringStatus }
  );
  else columns.push({ key: 'evidence', label: 'Evidence / status', value: record => evidence(record, key) });
  if (key === 'noncompliant') columns.push({ key: 'policy', label: 'Policy needing attention', value: policySummary });
  if (key === 'unencrypted') columns.push({ key: 'readiness', label: 'Encryption readiness', value: record => (record.encryptionDetail as JsonRecord | undefined)?.encryptionReadiness || (record.encryptionDetail as JsonRecord | undefined)?.encryptionReadinessState });
  columns.push({ key: 'recommendation', label: 'Recommended next action', value: record => aiRecommendation(snapshot, key)?.action || recommendedAction(record, key) });
  columns.push({ key: 'sync', label: 'Last check-in (UTC)', value: record => derived(record, 'lastSyncDate') },
    { key: 'compliance', label: 'Compliance', value: record => record.complianceState });
  if (key === 'stale') columns.push({ key: 'days', label: 'Inactive days at collection', value: record => derived(record, 'daysInactive') });
  if (key === 'update') columns.push({ key: 'version', label: 'OS version', value: record => record.osVersion });
  return columns;
}
function cell(value: unknown, key: string): string {
  if (typeof value === 'number') return key === 'percent' ? value.toFixed(1) + '%' : value.toLocaleString('en-US', { maximumFractionDigits: 2 });
  if (key === 'sync' || key === 'contact') return displayDate(value);
  return ['evidence', 'compliance', 'enrollment', 'patchStatus', 'supportStatus', 'update', 'readiness'].indexOf(key) >= 0 ? statusLabel(value) : display(value);
}

function RecordTable({ snapshot, issue, onDetail }: { snapshot: Snapshot; issue: SelectionKey; onDetail: (record: JsonRecord) => void }): React.ReactElement {
  const [filters, setFilters] = React.useState<TableFilters>(resetTableFilters);
  const records = getIssueRecords(snapshot, issue);
  const columns = columnsFor(snapshot, issue);
  const filtered = filterIssueRecords(snapshot, issue, filters);
  const selectedColumn = columns.filter(column => column.key === filters.sortKey)[0];
  if (selectedColumn) filtered.sort((left, right) => {
    const leftValue = selectedColumn.value(left);
    const rightValue = selectedColumn.value(right);
    const order = typeof leftValue === 'number' && typeof rightValue === 'number' ? leftValue - rightValue : display(leftValue).localeCompare(display(rightValue), 'en', { numeric: true });
    return filters.descending ? -order : order;
  });
  const pages = Math.max(1, Math.ceil(filtered.length / filters.pageSize));
  const page = Math.min(filters.page, pages - 1);
  const visible = pageRecords(filtered, page, filters.pageSize);
  const update = (change: Partial<TableFilters>): void => setFilters(current => ({ ...current, ...change, page: 0 }));
  const options = (field: string): string[] => Array.from(new Set(records.map(record => display(record[field])))).sort();
  const download = (): void => {
    const url = URL.createObjectURL(new Blob([exportCsv(filtered.map(record => ({ ...record, policyNeedingAttention: issue === 'noncompliant' ? policySummary(record) : undefined, recommendedNextAction: aiRecommendation(snapshot, issue)?.action || recommendedAction(record, issue) })))], { type: 'text/csv;charset=utf-8;' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'WISE-Endpoint-Intelligence-' + issue + '.csv';
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <section className="ei-records" aria-label={issues[issue].label}>
    <div className="ei-section-heading"><h2>{issues[issue].label}</h2><div className="ei-actions">
      <DefaultButton iconProps={{ iconName: 'ClearFilter' }} text="Reset filters" title={'Reset filters within ' + issues[issue].label} onClick={() => setFilters(resetTableFilters())} />
      <PrimaryButton iconProps={{ iconName: 'Download' }} text="Export CSV" disabled={!filtered.length} onClick={download} />
    </div></div>
    <p className="ei-note">{issues[issue].note}</p>
    {issue === 'noncompliant' && <ComplianceActions records={filtered} snapshot={snapshot} />}
    <div className="ei-filters"><label className="ei-search">Search<input type="search" value={filters.query} placeholder="Device, user, serial or evidence" onChange={event => update({ query: event.target.value })} /></label>
      {issue !== 'autopilot' && <label>OS<select value={filters.os} onChange={event => update({ os: event.target.value })}><option value="">All OS in this issue</option>{options('operatingSystem').map(value => <option key={value}>{value}</option>)}</select></label>}
      <label>Manufacturer<select value={filters.manufacturer} onChange={event => update({ manufacturer: event.target.value })}><option value="">All manufacturers in this issue</option>{options('manufacturer').map(value => <option key={value}>{value}</option>)}</select></label>
    </div>
    <p className="ei-count" role="status" aria-live="polite">{formatNumber(filtered.length)} of {formatNumber(records.length)} selected exceptions{filtered.length ? ' | ' + formatNumber(page * filters.pageSize + 1) + '-' + formatNumber(Math.min((page + 1) * filters.pageSize, filtered.length)) + ' shown' : ''}</p>
    <div className="ei-table-scroll" role="region" aria-label={issues[issue].label + ' table'} tabIndex={0}><table>
      <thead><tr>{columns.map(column => <th key={column.key} className={column.key === 'recommendation' ? 'ei-recommendation' : undefined} scope="col" aria-sort={filters.sortKey === column.key ? filters.descending ? 'descending' : 'ascending' : 'none'}>
        <button type="button" onClick={() => update({ sortKey: column.key, descending: filters.sortKey === column.key && !filters.descending })}>{column.label}<Icon iconName={filters.sortKey === column.key ? filters.descending ? 'SortDown' : 'SortUp' : 'Sort'} /></button>
      </th>)}<th scope="col"><span className="ei-details-heading">Details</span></th></tr></thead>
      <tbody>{visible.map((record, index) => <tr key={String(record.deviceId || record.id || record.serialNumber || '') + '-' + index}>
        {columns.map((column, columnIndex) => <td key={column.key} className={column.key === 'recommendation' ? 'ei-recommendation' : undefined}>{columnIndex === 0 ? <button type="button" className="ei-link" onClick={() => onDetail(record)}>{cell(column.value(record), column.key)}</button> : cell(column.value(record), column.key)}</td>)}
        <td><IconButton iconProps={{ iconName: 'Info' }} ariaLabel={'Details for ' + display(record.deviceName || record.serialNumber)} title="View source details" onClick={() => onDetail(record)} /></td>
      </tr>)}</tbody>
    </table>{!visible.length && <div className="ei-empty">No matching exceptions.</div>}</div>
    <div className="ei-pagination"><label>Rows<select value={filters.pageSize} onChange={event => update({ pageSize: Number(event.target.value) === 50 ? 50 : 25 })}><option value={25}>25</option><option value={50}>50</option></select></label><span>Page {page + 1} of {formatNumber(pages)}</span>
      <IconButton iconProps={{ iconName: 'ChevronLeft' }} ariaLabel="Previous page" title="Previous page" disabled={page === 0} onClick={() => setFilters(current => ({ ...current, page: page - 1 }))} />
      <IconButton iconProps={{ iconName: 'ChevronRight' }} ariaLabel="Next page" title="Next page" disabled={page + 1 >= pages} onClick={() => setFilters(current => ({ ...current, page: page + 1 }))} />
    </div>
  </section>;
}

function WindowsPatchTable({ rows, snapshot }: { rows: JsonRecord[]; snapshot: Snapshot }): React.ReactElement {
  const columns: Column[] = [
    { key: 'deviceName', label: 'Device', value: row => row.deviceName },
    { key: 'osVersion', label: 'Installed OS build', value: row => row.osVersion },
    { key: 'patchStatus', label: 'Monthly patch', value: row => row.patchStatus },
    { key: 'requiredBuild', label: 'Required monthly build', value: row => row.requiredBuild },
    { key: 'requiredKB', label: 'Required KB', value: row => row.requiredKB },
    { key: 'supportStatus', label: 'OS support', value: row => row.supportStatus },
    { key: 'recommendation', label: 'Recommended next action', value: row => aiRecommendation(snapshot, 'update')?.action || updateAction(row) }
  ];
  return <section className="ei-records" aria-label="All Windows patch levels">
    <div className="ei-section-heading"><h2>Windows build comparison</h2><span className="ei-muted">All Windows devices, not only exceptions</span></div>
    <div className="ei-table-scroll" role="region" aria-label="All Windows patch levels table" tabIndex={0}><table>
      <thead><tr>{columns.map(column => <th key={column.key} className={column.key === 'recommendation' ? 'ei-recommendation' : undefined} scope="col"><span className="ei-details-heading">{column.label}</span></th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={String(row.deviceId || index)}>{columns.map(column => <td key={column.key} className={column.key === 'recommendation' ? 'ei-recommendation' : undefined}>{cell(column.value(row), column.key)}</td>)}</tr>)}</tbody>
    </table>{!rows.length && <div className="ei-empty">No Windows devices in this collection.</div>}</div>
  </section>;
}

function ComplianceActions({ records: devices, snapshot }: { records: JsonRecord[]; snapshot: Snapshot }): React.ReactElement {
  const rows = devices.reduce<(PolicyFinding & { device: string })[]>((result, record) => result.concat(policyFindings(record).map(finding => ({ ...finding, device: display(record.deviceName), action: aiRecommendation(snapshot, 'noncompliant')?.action || finding.action }))), []);
  return <details open className="ei-object"><summary>Policy results and recommended actions ({rows.length})</summary>
    <div className="ei-table-scroll" role="region" aria-label="Compliance policy recommendations" tabIndex={0}><table>
      <thead><tr>{['Device', 'Policy', 'Setting', 'Reported result', 'Recommended next action'].map(label => <th key={label} className={label === 'Recommended next action' ? 'ei-recommendation' : undefined} scope="col">{label}</th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}><td>{row.device}</td><td>{row.policy}</td><td>{row.setting}</td><td>{row.status}</td><td className="ei-recommendation">{row.action}</td></tr>)}</tbody>
    </table>{!rows.length && <p className="ei-note">Policy failure details not available in this report. Check the device compliance results in Intune.</p>}</div>
  </details>;
}

export default function EndpointIq({ loadSnapshot, colorScheme = 'teal' }: IEndpointIqProps): React.ReactElement {
  const [snapshot, setSnapshot] = React.useState<Snapshot>();
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState('');
  const [attempt, setAttempt] = React.useState(0);
  const [tab, setTab] = React.useState<Tab>('overview');
  const [issue, setIssue] = React.useState<SelectionKey>('noncompliant');
  const [detail, setDetail] = React.useState<JsonRecord>();
  React.useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setDetail(undefined);
    Promise.resolve().then(loadSnapshot).then(value => {
      validateSnapshot(value);
      if (active) { setSnapshot(value); setLoading(false); }
    }).catch((reason: unknown) => {
      if (active) { setError(reason instanceof Error ? reason.message : 'Snapshot could not be loaded.'); setLoading(false); }
    });
    return () => { active = false; };
  }, [loadSnapshot, attempt]);
  const select = (key: SelectionKey): void => { setIssue(key); setTab(issues[key].tab); setDetail(undefined); };
  const navigate = (next: Tab): void => {
    setTab(next); setDetail(undefined);
    const target = tabs.filter(item => item.key === next)[0];
    if (target.issue) setIssue(target.issue);
  };
  const execution = snapshot ? executionTime(snapshot) : { timestamp: null, label: 'Last execution (UTC)' };
  const date = execution.timestamp;
  const month = date ? new Date(date).toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : 'Collection date not provided';
  const alternatives = (Object.keys(issues) as SelectionKey[]).filter(key => issues[key].tab === tab);
  const selectedMetric = snapshot ? getIssuePercentage(snapshot, issue) : undefined;
  const activeTheme = colorScheme === 'blue' ? createTheme({
    ...theme,
    palette: { ...theme.palette, themePrimary: '#69b5ff', themeDarkAlt: '#91caff', themeLight: '#294d78', themeLighter: '#193451' },
    semanticColors: { ...theme.semanticColors, focusBorder: '#91caff' }
  }) : theme;
  const dashboardClass = styles.dashboard + (colorScheme === 'blue' ? ' ' + styles.blueScheme : '');
  return <ThemeProvider theme={activeTheme} className={dashboardClass}>
    <div className="ei-shell"><header className="ei-header"><div><div className="ei-brand"><Icon iconName="Devices3" /><span>WISE Endpoint Intelligence</span><span className="ei-eyebrow">Management overview</span></div><h1>Overall endpoint health</h1>{snapshot && <p className="ei-tenant">Tenant ID: {display(snapshot.metadata.tenantId || snapshot.metadata.tenant)}</p>}</div>
      <div className="ei-date"><strong>{month}</strong><span>{execution.label}</span>{date && <time dateTime={date}>{new Date(date).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'UTC' })} UTC</time>}</div>
    </header>
    {loading ? <div className="ei-state"><Spinner size={SpinnerSize.large} label="Loading management snapshot" /></div> : error ? <div className="ei-state"><MessageBar messageBarType={MessageBarType.error}>{error}</MessageBar><DefaultButton text="Retry" iconProps={{ iconName: 'Refresh' }} onClick={() => setAttempt(current => current + 1)} /></div> : snapshot && <>
      <div className="ei-coverage"><span>Coverage: {formatNumber(snapshot.summary.totalDevices)} source devices; compliance evaluated {formatNumber(snapshot.summary.evaluatedDevices)} ({formatPercent(snapshot.summary.complianceCoverage.percentage)}).</span><span>Data reflects the most recent successful collection, not continuous monitoring.</span></div>
      <nav className="ei-nav" aria-label="Endpoint management"><div role="tablist" aria-label="Operational views">{tabs.map((item, index) => <button key={item.key} type="button" id={'ei-tab-' + item.key} role="tab" aria-selected={tab === item.key} aria-controls="ei-view" tabIndex={tab === item.key ? 0 : -1} className={tab === item.key ? 'active' : ''} onClick={() => navigate(item.key)} onKeyDown={event => {
        let next = index;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else return;
        event.preventDefault(); navigate(tabs[next].key);
        const target = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next];
        target?.focus();
      }}>{item.label}</button>)}</div></nav>
      <main id="ei-view" role="tabpanel" aria-labelledby={'ei-tab-' + tab} tabIndex={0} className="ei-main">
        {tab === 'overview' ? <Overview snapshot={snapshot} onSelect={select} /> : tab === 'risk' ? <RiskDetails snapshot={snapshot} onDetail={setDetail} /> : tab === 'experience' ? <UserExperience snapshot={snapshot} onSelect={select} /> : <>
          <div className="ei-issue-heading"><div><p className="ei-eyebrow">{tabs.filter(item => item.key === tab)[0].label}</p><h2>{issues[issue].label}</h2></div>{selectedMetric && <div className="ei-issue-ratio"><strong>{formatPercent(selectedMetric.percentage)}</strong><Ratio metric={selectedMetric} /></div>}</div>
          {alternatives.length > 1 && <div className="ei-segments" role="group" aria-label="Issue group">{alternatives.map(key => <DefaultButton key={key} checked={issue === key} text={issues[key].label} title={issues[key].note} onClick={() => select(key)} />)}</div>}
          {tab === 'enrollment' && <p className="ei-note">Autopilot: {formatNumber(snapshot.summary.autopilotRegistered)} registered; {formatNumber(snapshot.summary.autopilotEnrolled)} enrolled (aggregate only); {formatNumber(snapshot.summary.autopilotExceptions)} registrations not reported as enrolled. These can include stock waiting for deployment.</p>}
          {tab === 'compliance' && <p className="ei-note">Policy summary: {display(snapshot.policySummary?.nonCompliantDeviceCount)} non-compliant; device inventory: {formatNumber(snapshot.summary.exceptionCounts.noncompliant || 0)}. Reported policy failures are listed below when available. Policy errors do not prove a setting failed.</p>}
          {tab === 'updates' && <>
            {snapshot.windowsPatchSummary ? <>
              <p className="ei-note">Monthly patch compliance: {formatNumber(Number(snapshot.windowsPatchSummary.compliant) || 0)} / {formatNumber(Number(snapshot.windowsPatchSummary.evaluated) || 0)} supported Windows devices ({formatPercent(snapshot.summary.health.find(metric => metric.key === 'patch')?.percentage ?? null)}). Installed builds are compared with the Microsoft monthly security build for the same Windows version.</p>
              <p className="ei-note">Not included in that check: {formatNumber(Number(snapshot.windowsPatchSummary.unsupported) || 0)} confirmed unsupported OS, {formatNumber(Number(snapshot.windowsPatchSummary.supportUnknown) || 0)} needing edition-specific support checks, {formatNumber(Number(snapshot.windowsPatchSummary.unknown) || 0)} unknown builds, and {formatNumber(Number(snapshot.windowsPatchSummary.supportStatusUnavailable) || 0)} unknown support statuses.</p>
            </> : <p className="ei-note">Monthly patch compliance is not provided in this report. The legacy OS classification is not a monthly patch check.</p>}
            <p className="ei-note">Devices needing updates: {formatNumber(selectedMetric?.numerator || 0)} / {formatNumber(selectedMetric?.denominator || 0)} all source devices ({formatPercent(selectedMetric?.percentage ?? null)}). This uses all devices, not the monthly patch compliance group. See the issue definition below.</p>
            <p className="ei-note">Per-device installation results are not available. OS versions and policy states alone do not prove an installation failed.</p>
            <ObjectView value={snapshot.windowsPatchSummary} name="Windows monthly patch summary" />
            <ObjectView value={snapshot.updateRings} name="Windows Update ring policies" />
            <ObjectView value={(snapshot as unknown as JsonRecord).featureUpdateProfiles} name="Feature update profiles" />
            <ObjectView value={(snapshot as unknown as JsonRecord).qualityUpdateProfiles} name="Quality update profiles" />
            <ObjectView value={(snapshot as unknown as JsonRecord).featureUpdateAssignments} name="Feature update profile assignments" />
            <ObjectView value={(snapshot as unknown as JsonRecord).qualityUpdateAssignments} name="Quality update profile assignments" />
          </>}
          {tab === 'updates' && snapshot.windowsPatchDevices && <WindowsPatchTable rows={snapshot.windowsPatchDevices} snapshot={snapshot} />}
          <RecordTable key={issue} snapshot={snapshot} issue={issue} onDetail={setDetail} />
        </>}
      </main>
      <footer className="ei-footer"><span>Source: {display(snapshot.metadata.tenant || snapshot.metadata.tenantName || snapshot.metadata.tenantId)} | Latest collected report</span><details><summary>Data sources and scope</summary><ObjectView value={snapshot.coverage} name="Coverage and definitions" /></details></footer>
    </>}
    </div>
    <Panel isOpen={!!detail} onDismiss={() => setDetail(undefined)} type={PanelType.large} headerText={detail ? display(detail.deviceName || detail.serialNumber || 'Exception details') : 'Exception details'} closeButtonAriaLabel="Close details" isLightDismiss theme={activeTheme} className={dashboardClass}>
      {detail && snapshot && <div className="ei-detail"><p className="ei-note">Collected source record. {tab === 'risk' ? 'Local review priority is derived from recorded exception evidence, not an authoritative security risk score.' : issues[issue].note}</p>
        {tab === 'risk' && <><h3>{riskEntry(detail as DeviceRecord, snapshot).priority}</h3><p>{riskEntry(detail as DeviceRecord, snapshot).rationale}</p><ul>{riskEntry(detail as DeviceRecord, snapshot).evidence.map(reason => <li key={reason}>{reason}</li>)}</ul><h3>Recommended next action</h3><p>{riskEntry(detail as DeviceRecord, snapshot).action}</p></>}
        {tab !== 'risk' && <><h3>Recommended next action</h3><p>{aiRecommendation(snapshot, issue)?.action || recommendedAction(detail, issue)}</p></>}
        {issue !== 'autopilot' && <><h3>Policy results</h3><ComplianceActions records={[detail]} snapshot={snapshot} /><ObjectView value={detail.complianceDetail} name="Compliance detail" /><ObjectView value={detail.encryptionDetail} name="Encryption detail" /><ObjectView value={detail.updateDetail} name="Update detail" /></>}
        <ObjectView value={detail} name="All source and derived fields" />
      </div>}
    </Panel>
  </ThemeProvider>;
}
