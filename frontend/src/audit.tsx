import { useEffect, useState } from 'react'
import { sessionApi } from './session-api'
import { timestamp } from './format'

type AuditEvent = { id: string; action: string; resource_id: string | null; created_at: string }
export function AuditLog() {
  const [rows, setRows] = useState<AuditEvent[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController()
    void sessionApi<AuditEvent[]>('/audit-events', { signal: controller.signal }).then(setRows)
      .catch(e => { if (!controller.signal.aborted) setError(String(e)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [])
  return <section className="panel account-panel-body"><h1>Audit log</h1>
    <p>Latest 100 append-only security events for this account. Owner access only.</p>
    {error && <p role="alert" className="notice danger">{error}</p>}
    {loading ? <p role="status">Loading audit events…</p> : rows.length ?
      <div className="table-scroll"><table><thead><tr><th>Time</th><th>Action</th><th>Resource</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}><td>{timestamp(row.created_at)}</td>
          <td>{row.action}</td><td><code>{row.resource_id ?? '—'}</code></td></tr>)}</tbody></table></div>
      : !error && <p>No audit events recorded.</p>}
  </section>
}
