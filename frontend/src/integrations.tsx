import { useEffect, useState, type FormEvent } from 'react'
import { sessionApi } from './session-api'

type Channel = { id: string; name: string; kind: string; enabled: boolean }

export function Integrations() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState('slack')
  const load = async () => setChannels(await sessionApi<Channel[]>('/alert-channels'))
  useEffect(() => {
    const controller = new AbortController()
    void sessionApi<Channel[]>('/alert-channels', { signal: controller.signal }).then(setChannels).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [])
  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget; setBusy(true); setError('')
    try { await sessionApi('/alert-channels', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form))) }); form.reset(); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Integration could not be saved.') }
    finally { setBusy(false) }
  }
  return <section className="panel account-panel-body"><h2>Alert integrations</h2><p>Destinations are private and cannot be retrieved after saving. Generic webhook hosts must be approved by the operator.</p>{error && <p className="notice danger" role="alert">{error}</p>}
    {channels.map(c => <div className="team-row" key={c.id}><span>{c.name} · {c.kind}</span>{c.enabled ? <button onClick={() => { void sessionApi(`/alert-channels/${c.id}/disable`, { method: 'POST' }).then(load).catch(e => setError(String(e))) }}>Disable integration</button> : <span>Disabled</span>}</div>)}
    <form className="account-form" onSubmit={event => { void create(event) }}><label>Name<input name="name" required maxLength={120} /></label><label>Channel<select name="kind" value={kind} onChange={e => setKind(e.target.value)}><option value="slack">Slack</option><option value="generic">Generic webhook</option><option value="email">Email</option></select></label><label>{kind === 'email' ? 'Recipient email' : 'Webhook URL'}<input name="destination" type={kind === 'email' ? 'email' : 'url'} required maxLength={2048} autoComplete="off" placeholder={kind === 'email' ? 'oncall@example.com' : 'https://hooks.slack.com/services/…'} /></label><button disabled={busy}>{busy ? 'Saving…' : 'Save integration'}</button></form>
  </section>
}
