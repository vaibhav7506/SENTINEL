import { useEffect, useState, type FormEvent } from 'react'
import { sessionApi } from './session-api'
import { timestamp } from './format'
import type { Profile } from './auth'
import { Integrations } from './integrations'

type Host = { id: string; display_name: string; hostname: string; status: string; platform: string; agent_version: string; last_seen_at: string }
type Enrollment = { id: string; token: string; expires_at: string; install_command: string }
type Credential = { id: string; key_prefix: string; revoked_at: string | null }
type Person = { id: string; email: string; role: Profile['role'] }

export function AccountHosts({ profile }: { profile: Profile }) {
  const [hosts, setHosts] = useState<Host[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [keys, setKeys] = useState<Credential[]>([])
  const [selected, setSelected] = useState('')
  const canManage = profile.role !== 'MEMBER'
  const refresh = async () => {
    try { setHosts(await sessionApi<Host[]>('/hosts')); setError('') }
    catch (e) { setError(e instanceof Error ? e.message : 'Hosts unavailable.') }
    finally { setLoading(false) }
  }
  useEffect(() => {
    const controller = new AbortController()
    const load = () => { void sessionApi<Host[]>('/hosts', { signal: controller.signal }).then(value => { if (!controller.signal.aborted) { setHosts(value); setError(''); setLoading(false) } }).catch(e => { if (!controller.signal.aborted) { setError(String(e)); setLoading(false) } }) }
    load()
    const timer = window.setInterval(() => { if (!document.hidden) load() }, 30000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [])
  const generate = async () => {
    setBusy(true); setEnrollment(null); setCopied(false); setError('')
    try { setEnrollment(await sessionApi<Enrollment>('/hosts/enrollment-tokens', { method: 'POST', body: '{}' })) }
    catch (e) { setError(e instanceof Error ? e.message : 'Enrollment unavailable.') }
    finally { setBusy(false) }
  }
  const inspect = async (host: string) => {
    setSelected(host)
    try { setKeys(await sessionApi<Credential[]>(`/hosts/${encodeURIComponent(host)}/credentials`)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Credentials unavailable.') }
  }
  const revoke = async (key: string) => {
    try { await sessionApi(`/agent-credentials/${key}/revoke`, { method: 'POST' }); await inspect(selected) }
    catch (e) { setError(e instanceof Error ? e.message : 'Revocation failed.') }
  }
  return <>
    <div className="page-heading"><div><span className="eyebrow">YOUR WORKSPACE / MACHINES</span><h1>Hosts</h1><p>Enroll machines and keep each agent’s access independent.</p></div>{canManage && <button className="primary-action" onClick={() => { void generate() }} disabled={busy}>{busy ? 'Generating…' : '+ Add host'}</button>}</div>
    {error && <p className="notice danger" role="alert">{error}</p>}
    {enrollment && <section className="panel enrollment-card"><div className="panel-heading"><h2>Install your agent</h2><button onClick={() => { setEnrollment(null); setCopied(false) }}>Dismiss</button></div><div className="account-panel-body">
      <p className="notice">This one-time enrollment token is displayed once. It expires at {timestamp(enrollment.expires_at)}. The permanent host key is saved on the machine after enrollment.</p>
      <p>Run this command on the Linux machine you want to monitor.</p>
      <pre className="install-command"><code>{enrollment.install_command}</code></pre>
      <div className="action-row"><button onClick={() => { void navigator.clipboard.writeText(enrollment.install_command).then(() => setCopied(true)).catch(() => setError('Select the command to copy it manually.')) }}>{copied ? 'Copied' : 'Copy install command'}</button><button onClick={() => { void sessionApi(`/hosts/enrollment-tokens/${enrollment.id}/revoke`, { method: 'POST' }).then(() => setEnrollment(null)).catch(e => setError(String(e))) }}>Revoke this token</button></div>
    </div></section>}
    <section className="panel"><div className="panel-heading"><h2>Enrolled machines</h2><button onClick={() => { void refresh() }}>Refresh</button></div>
      {loading ? <p className="account-panel-body" role="status">Loading hosts…</p> : hosts.length === 0 ? <div className="empty"><h2>Your first host starts here</h2><p>{canManage ? 'Select Add host to generate a short-lived install command.' : 'Ask an owner or admin to enroll a machine.'}</p></div> : <div className="table-scroll"><table><thead><tr><th>Host</th><th>Platform</th><th>Agent</th><th>Last seen</th><th>Status</th>{canManage && <th>Access</th>}</tr></thead><tbody>{hosts.map(h => <tr key={h.id}><td><a className="text-link" href={`#/hosts/${encodeURIComponent(h.id)}`}>{h.display_name}</a><small className="host-subtitle">{h.hostname}</small></td><td>{h.platform || 'Unknown'}</td><td>{h.agent_version || 'Legacy'}</td><td>{timestamp(h.last_seen_at)}</td><td>{h.status}</td>{canManage && <td><button onClick={() => { void inspect(h.id) }}>Manage access</button></td>}</tr>)}</tbody></table></div>}
    </section>
    {selected && canManage && <section className="panel account-panel-body"><h2>Host credentials</h2><p>Existing secret keys cannot be shown. Revocation stops this credential’s telemetry access.</p>{keys.length ? keys.map(k => <div className="action-row" key={k.id}><code>{k.key_prefix}…</code>{k.revoked_at ? <span>Revoked {timestamp(k.revoked_at)}</span> : <button onClick={() => { void revoke(k.id) }}>Revoke credential</button>}</div>) : <p>No host credentials.</p>}</section>}
  </>
}

export function TeamAccount({ profile }: { profile: Profile }) {
  const [people, setPeople] = useState<Person[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const load = async () => { try { setPeople(await sessionApi<Person[]>('/team')) } catch (e) { setError(String(e)) } }
  useEffect(() => {
    if (profile.role !== 'OWNER') return
    const controller = new AbortController()
    void sessionApi<Person[]>('/team', { signal: controller.signal }).then(setPeople).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [profile.role])
  if (profile.role !== 'OWNER') return <div className="empty"><h1>Account management</h1><p>Your account owner manages the team and settings.</p></div>
  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element); setBusy(true); setError('')
    try { await sessionApi('/team', { method: 'POST', body: JSON.stringify(Object.fromEntries(form)) }); element.reset(); await load(); setNotice('Team member created. Share their initial password privately.') }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not create user.') }
    finally { setBusy(false) }
  }
  return <><div className="page-heading"><div><span className="eyebrow">WORKSPACE / ADMINISTRATION</span><h1>Team & account</h1><p>{profile.account.name} · {profile.email}</p></div></div>
    {error && <p className="notice danger" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <section className="panel account-panel-body"><h2>Team</h2>{people.map(p => <div className="team-row" key={p.id}><span>{p.email}</span><label>Role<select aria-label={`Role for ${p.email}`} value={p.role} onChange={e => { void sessionApi(`/team/${p.id}`, { method: 'PATCH', body: JSON.stringify({ role: e.target.value }) }).then(() => { window.dispatchEvent(new Event('sentinel-account-updated')); void load() }).catch(e => setError(String(e))) }}><option>OWNER</option><option>ADMIN</option><option>MEMBER</option></select></label></div>)}
      <h2>Create a team member</h2><form className="account-form" onSubmit={event => { void add(event) }}><label>Email<input type="email" name="email" required maxLength={320} autoComplete="off" /></label><label>Initial password<input type="password" name="password" required minLength={12} maxLength={128} autoComplete="new-password" /></label><label>Role<select name="role" defaultValue="MEMBER"><option>MEMBER</option><option>ADMIN</option><option>OWNER</option></select></label><button disabled={busy}>{busy ? 'Creating…' : 'Create member'}</button></form>
    </section><section className="panel account-panel-body"><h2>Workspace name</h2><form className="account-form" onSubmit={event => { event.preventDefault(); const name = new FormData(event.currentTarget).get('name'); void sessionApi('/account', { method: 'PATCH', body: JSON.stringify({ name }) }).then(() => { window.dispatchEvent(new Event('sentinel-account-updated')); setNotice('Workspace name saved.') }).catch(e => setError(String(e))) }}><label>Name<input name="name" defaultValue={profile.account.name} required maxLength={160} /></label><button>Save workspace</button></form></section>
    <Integrations />
  </>
}
