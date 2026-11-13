import * as React from 'react';
import { DefaultButton, IconButton } from '@fluentui/react';
import { activeInsights, aiRecommendation, executiveSummary, reviewLevels, riskRegister, validAiIntelligence } from './insights';
import { experienceMeasurements, experienceRecommendations } from './experience';
import { display, displayDate, exportCsv, formatNumber, JsonRecord, lower, pageRecords, SelectionKey, Snapshot, statusLabel } from './model';

function downloadRows(records: JsonRecord[]): void {
  const url = URL.createObjectURL(new Blob([exportCsv(records)], { type: 'text/csv;charset=utf-8;' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = 'WISE-risk-register.csv';
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Assessment({ snapshot, onSelect }: { snapshot: Snapshot; onSelect: (key: SelectionKey) => void }): React.ReactElement {
  const insights = activeInsights(snapshot);
  const ai = validAiIntelligence(snapshot);
  const summary = ai ? ai.executive_summary : executiveSummary(snapshot);
  return <>
    <section className="ei-band ei-assessment"><div className="ei-section-heading"><h2>Executive summary</h2><span className="ei-muted">{ai ? 'Mistral Medium 3.5 · Aggregate analysis' : 'Deterministic analysis'} · When this report was collected</span></div>
      <p>{summary.introduction}</p>
      <p>{summary.findings}</p>
      <p><strong>Bottom line: </strong>{summary.bottomLine}</p>
    </section>
    <section className="ei-band"><div className="ei-insight-grid"><div><h2>Active insights</h2>{insights.map(item => {
      const aiItem = ai?.key_insights.find(insight => insight.key === item.key);
      return <article className="ei-insight" key={item.key}><h3>{aiItem?.title || item.title}{aiItem && <span className="ei-muted"> {aiItem.priority} priority</span>}</h3><p>{aiItem?.observation || item.observation}</p><p className="ei-note">{aiItem?.implication || item.implication}</p></article>;
    })}</div>
      <div><h2>Recommended actions</h2>{insights.map(item => { const recommendation = aiRecommendation(snapshot, item.key); return <article className="ei-insight ei-action" key={item.key}><h3>{recommendation?.title || item.title}{recommendation && <span className="ei-muted"> {recommendation.priority} priority</span>}</h3><p>{recommendation?.action || item.action} <span className="ei-muted">Suggested owner: {recommendation?.owner || item.owner}; {formatNumber(item.metric.numerator)} affected {item.key === 'autopilot' ? 'registrations' : 'devices'}.</span></p><DefaultButton text="Review details" iconProps={{ iconName: 'ChevronRight' }} onClick={() => onSelect(item.key)} /></article>; })}</div></div>
      {!insights.length && <p className="ei-note">No issues are reported. Check that the data is complete before deciding all devices are healthy.</p>}
    </section>
  </>;
}

export function RiskDetails({ snapshot, onDetail }: { snapshot: Snapshot; onDetail: (record: JsonRecord) => void }): React.ReactElement {
  const [query, setQuery] = React.useState('');
  const [priority, setPriority] = React.useState('');
  const [page, setPage] = React.useState(0);
  const [pageSize, setPageSize] = React.useState<25 | 50>(25);
  const ai = validAiIntelligence(snapshot);
  const register = riskRegister(snapshot);
  const filtered = register.filter(entry => (!priority || priority === entry.priority) && lower([entry.record.deviceName, entry.record.userPrincipalName, entry.record.deviceId, ...entry.evidence, entry.action].join(' ')).indexOf(lower(query)) >= 0);
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  const exportRows = filtered.map(entry => ({ device: entry.record.deviceName, deviceId: entry.record.deviceId, user: entry.record.userPrincipalName, priority: entry.priority, rationale: entry.rationale, evidence: entry.evidence.join('; '), nextAction: entry.action, lastSyncUtc: entry.record.derived.lastSyncDate }));
  const visible = pageRecords(exportRows, currentPage, pageSize);
  return <>
    <section className="ei-band"><h2>Risk overview</h2><div className="ei-risk-counts">{reviewLevels.map(level => <div key={level}><span>{level}</span><strong>{formatNumber(register.filter(entry => entry.priority === level).length)}</strong></div>)}</div>
      <p className="ei-note">{ai?.executive_summary.findings || 'Risk categories are derived from Python-reported evidence, not Microsoft risk scores. Missing data does not mean low risk.'}</p>
    </section>
    <section className="ei-band"><h2>Reported risk details</h2><div className="ei-insight-grid"><div><h3>Threat status across all devices</h3><table><thead><tr><th><span className="ei-details-heading">Reported status</span></th><th><span className="ei-details-heading">Devices</span></th></tr></thead><tbody>{(snapshot.distributions.threat || []).map(row => <tr key={row.label}><td>{statusLabel(row.label)}</td><td>{formatNumber(row.count)}</td></tr>)}</tbody></table>{!snapshot.distributions.threat?.length && <p className="ei-note">Threat status breakdown not provided.</p>}</div>
      <div><h3>Unfinished device actions</h3><table><thead><tr><th><span className="ei-details-heading">Reported status</span></th><th><span className="ei-details-heading">Devices</span></th></tr></thead><tbody>{['retirePending', 'wipePending', 'wipeFailed'].map(state => <tr key={state}><td>{statusLabel(state)}</td><td>{formatNumber(snapshot.devices.filter(record => record.managementState === state).length)}</td></tr>)}</tbody></table><p className="ei-note">Only pending removals, pending wipes and failed wipes are shown here. Unknown or turned-off threat reporting does not mean a threat was detected.</p></div></div>
    </section>
    <section className="ei-records"><div className="ei-section-heading"><h2>Risk register</h2><div className="ei-actions"><DefaultButton text="Reset filters" iconProps={{ iconName: 'ClearFilter' }} onClick={() => { setQuery(''); setPriority(''); setPage(0); }} /><DefaultButton text="Export CSV" iconProps={{ iconName: 'Download' }} disabled={!filtered.length} onClick={() => downloadRows(exportRows)} /></div></div>
      <div className="ei-filters"><label className="ei-search">Search risk details<input type="search" value={query} placeholder="Device, user, ID or reason" onChange={event => { setQuery(event.target.value); setPage(0); }} /></label><label>Review category<select value={priority} onChange={event => { setPriority(event.target.value); setPage(0); }}><option value="">All categories</option>{reviewLevels.map(level => <option key={level}>{level}</option>)}</select></label></div>
      <p className="ei-count" role="status">{formatNumber(filtered.length)} of {formatNumber(register.length)} devices needing review</p>
      <div className="ei-table-scroll" role="region" aria-label="Risk register table" tabIndex={0}><table><thead><tr>{['Device', 'User', 'Review priority', 'Reasons / evidence', 'Recommended next action', 'Last sync (UTC)'].map(label => <th key={label} className={label === 'Recommended next action' ? 'ei-recommendation' : undefined} scope="col"><span className="ei-details-heading">{label}</span></th>)}</tr></thead><tbody>{visible.map((row, index) => <tr key={String(row.deviceId || index)}><td><button type="button" className="ei-link" onClick={() => onDetail(filtered[currentPage * pageSize + index].record)}>{display(row.device)}</button></td><td>{display(row.user)}</td><td><strong title={display(row.rationale)}>{display(row.priority)}</strong><p className="ei-note">{display(row.rationale)}</p></td><td>{display(row.evidence)}</td><td className="ei-recommendation">{display(row.nextAction)}</td><td>{displayDate(row.lastSyncUtc)}</td></tr>)}</tbody></table>{!visible.length && <p className="ei-empty">No matching exception devices.</p>}</div>
      <div className="ei-pagination"><label>Rows<select value={pageSize} onChange={event => { setPageSize(Number(event.target.value) === 50 ? 50 : 25); setPage(0); }}><option value={25}>25</option><option value={50}>50</option></select></label><span>Page {currentPage + 1} of {formatNumber(pages)}</span><IconButton iconProps={{ iconName: 'ChevronLeft' }} title="Previous page" ariaLabel="Previous page" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)} /><IconButton iconProps={{ iconName: 'ChevronRight' }} title="Next page" ariaLabel="Next page" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)} /></div>
    </section>
  </>;
}

export function UserExperience({ snapshot, onSelect }: { snapshot: Snapshot; onSelect: (key: SelectionKey) => void }): React.ReactElement {
  const recommendations = experienceRecommendations(snapshot);
  return <>
    <section className="ei-band ei-assessment"><h2>User experience recommendations</h2><p>Suggested improvements based on the device data in this report. Startup speed, app reliability, network speed and user satisfaction have not been measured.</p><p className="ei-note">Each device is counted once per recommendation, but may appear in several groups. Autopilot registrations are counted separately. These are proposed actions, not measured improvements.</p>
    </section>
    {recommendations.map(plan => { const actions = plan.keys.map(key => aiRecommendation(snapshot, key)).filter((item): item is NonNullable<typeof item> => !!item); return <section className="ei-band" key={plan.title}><div className="ei-section-heading"><h2>{plan.title}</h2><span className="ei-muted">{formatNumber(plan.affectedDevices)} affected managed devices</span></div><p>{actions.length ? actions.map(item => item.action).join(' ') : plan.impact}</p>
      <div className="ei-insight-grid"><div><h3>What the report shows</h3><dl className="ei-experience-evidence">{plan.evidence.map(item => <div key={item.key}><dt>{item.label}</dt><dd>{formatNumber(item.count)} / {formatNumber(item.denominator)}<span className="ei-note"> {item.denominatorLabel}</span><IconButton iconProps={{ iconName: 'ChevronRight' }} title={'Review ' + item.label.toLowerCase()} ariaLabel={'Review ' + item.label.toLowerCase()} onClick={() => onSelect(item.key)} /></dd></div>)}</dl><p className="ei-note">No reported issues does not mean all data is complete or users have no problems.</p><h3>Suggested owner</h3><p>{actions.length ? Array.from(new Set(actions.map(item => item.owner))).join('; ') : plan.owner}</p></div>
      <div><h3>Recommended steps</h3><ol>{actions.length ? actions.map(item => <li key={item.key}>{item.action} <span className="ei-muted">Owner: {item.owner}.</span></li>) : plan.steps.map(step => <li key={step}>{step}</li>)}</ol><h3>How to check results</h3><p>{plan.success}</p></div></div>
    </section>; })}
    <section className="ei-band"><h2>What still needs to be measured</h2><div className="ei-table-scroll" role="region" aria-label="What still needs to be measured" tabIndex={0}><table><thead><tr>{['Area', 'Not measured in this report', 'Suggested checks'].map(label => <th scope="col" key={label}><span className="ei-details-heading">{label}</span></th>)}</tr></thead><tbody>{experienceMeasurements.map(item => <tr key={item.domain}><td>{item.domain}</td><td>{item.missing}</td><td>{item.recommendation}</td></tr>)}</tbody></table></div></section>
    <section className="ei-band"><h2>Putting the plan into practice</h2><p><strong>Check:</strong> confirm the data is complete, how devices are used and any user problems with the service desk. Agree owners and identify critical business groups.</p><p><strong>Test:</strong> investigate security concerns first, then test approved fixes with a small, representative group. Explain planned downtime, data protection and recovery options before wider rollout.</p><p><strong>Confirm:</strong> check new device data and ask users whether the problem is resolved. Record remaining issues and any changes that need to be reversed. A newer report alone does not show improvement.</p><p><strong>Review:</strong> check daily reports with the responsible teams. Regularly review repeat support requests, user experience measures, unresolved risks and spending needs. Collect extra data only with agreed scope, privacy checks, licensing and permissions.</p></section>
  </>;
}