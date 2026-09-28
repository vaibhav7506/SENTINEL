import { useEffect, useRef, useState } from 'react'
import { useApi } from './api'
import { Pages } from './pages'
import { Badge, ErrorBoundary } from './ui'
import { timestamp } from './format'
import type { Snapshot } from './types'
import type { Profile } from './auth'
import { AccountHosts, TeamAccount } from './account-pages'
import { AuditLog } from './audit'
import { Integrations } from './integrations'

const navigation = [
  ['overview', 'Overview', '◈'], ['hosts', 'Hosts', '▤'], ['predictions', 'Predictions', '⌁'],
  ['incidents', 'Incidents', '◉'], ['experiments', 'Experiments', '⊞'],
  ['models', 'Models', '◇'], ['evaluation', 'Evaluation', '▥'],
] as const
const getRoute = () => window.location.hash.slice(2) || 'overview'

type ConsoleProps = { profile: Profile; onLogout: () => void; accountError: string }
export function App(props: ConsoleProps) { return <ErrorBoundary><Console {...props} /></ErrorBoundary> }
function Console({ profile, onLogout, accountError }: ConsoleProps) {
  const [route, setRoute] = useState(getRoute)
  const [menu, setMenu] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)
  const { data, error, loading, retry } = useApi<Snapshot>('/console/snapshot')
  useEffect(() => {
    const navigate = () => { setRoute(getRoute()); setMenu(false); document.getElementById('main-content')?.focus(); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', navigate)
    return () => window.removeEventListener('hashchange', navigate)
  }, [])
  useEffect(() => {
    if (!menu) return
    document.querySelector<HTMLAnchorElement>('#mobile-navigation a')?.focus()
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setMenu(false); menuButton.current?.focus() } }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [menu])
  const page = route.split('/')[0]
  const title = page === 'team' ? 'Team & account' : page === 'alerts' ? 'Alert settings' : page === 'audit' ? 'Audit log' : navigation.find(n => n[0] === page)?.[1] ?? 'View not found'
  const visibleNavigation = navigation.filter(([id]) => id !== 'experiments' || profile.role !== 'MEMBER')
  useEffect(() => { document.title = `Sentinel / ${route.startsWith('hosts/') ? 'Host detail' : title}` }, [route, title])
  const fresh = data?.hosts.some(h => h.prediction_fresh)
  return <div className="console-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>Skip to content</a>
    <aside id="mobile-navigation" className={`sidebar ${menu ? 'open' : ''}`}>
      <a href="#/overview" className="brand"><span className="brand-mark">S</span>SENTINEL<span className="brand-plus">+</span></a>
      <button className="nav-close" onClick={() => { setMenu(false); menuButton.current?.focus() }}>Close navigation ×</button>
      <div className="workspace-label"><span className="dot" /><span>{profile.account.name}<small>{profile.role}</small></span></div>
      <nav aria-label="Main navigation">{visibleNavigation.map(([id, label, icon]) => <a key={id} href={`#/${id}`} className={page === id ? 'active' : ''} aria-current={page === id ? 'page' : undefined}><span aria-hidden="true">{icon}</span>{label}{id === 'incidents' && !!data?.overview.active_incidents && <em>{data.overview.active_incidents}</em>}</a>)}{profile.role === 'OWNER' && <a href="#/team" className={page === 'team' ? 'active' : ''}><span aria-hidden="true">♧</span>Team & account</a>}</nav>
      {profile.role === 'OWNER' && <nav aria-label="Account security"><a href="#/alerts">Alert settings</a><a href="#/audit">Audit log</a></nav>}
      <div className="account-context"><span>{profile.email}</span><button onClick={onLogout}>Sign out</button></div>
      <div className="sidebar-foot"><span className="eyebrow">OBSERVE · PREDICT · REVIEW</span><p>Human approval governs every remediation.</p><div className="local-note">ACCOUNT WORKSPACE / PHASE 3</div></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="breadcrumbs"><button ref={menuButton} className="menu-toggle" aria-label={menu ? 'Close navigation' : 'Open navigation'} aria-expanded={menu} aria-controls="mobile-navigation" onClick={() => setMenu(v => !v)}>☰</button><span>Console</span><span className="crumb-slash">/</span><strong>{route.startsWith('hosts/') ? 'Host detail' : title}</strong></div><div className="topbar-status"><Badge tone={error ? 'danger' : fresh ? 'good' : 'warning'}>{error ? 'API disconnected' : fresh ? 'Inference current' : loading ? 'Connecting' : 'Inference stale'}</Badge><button className="refresh-button" onClick={retry} aria-label="Refresh console data">↻</button></div></header>
      <main id="main-content" tabIndex={-1}>
        {accountError && <p className="notice danger" role="alert">{accountError}</p>}
        {error && <div className="notice danger" role="alert"><strong>Data connection interrupted.</strong> {error} {data && 'Showing the last successful snapshot.'}<button onClick={retry}>Retry</button></div>}
        {page === 'alerts' ? (profile.role === 'OWNER' ? <Integrations /> : <p>Owner access required.</p>) : page === 'audit' ? (profile.role === 'OWNER' ? <AuditLog /> : <p>Owner access required.</p>) : page === 'team' ? <TeamAccount profile={profile} /> : route === 'hosts' ? <AccountHosts profile={profile} /> : loading ? <div className="loading" role="status"><span className="loading-line" /><span className="eyebrow">CONNECTING TO SENTINEL</span><h1>Loading your workspace</h1><p>Reading persisted observations, forecasts, and evaluation runs.</p></div> : data ? <Pages key={route} route={route} data={data} /> : <div className="empty"><h1>Workspace unavailable</h1><p>Start the Sentinel API and retry the connection.</p><button onClick={retry}>Reconnect</button></div>}
        <footer><span>SENTINEL CONSOLE <span className="footer-dot">·</span> Real records, local environment</span><span>Snapshot {data ? timestamp(data.as_of) : 'unavailable'} · refreshes every 30s</span></footer>
      </main>
    </div>
  </div>
}
