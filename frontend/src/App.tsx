import { useEffect, useState } from 'react'
import { App as Console } from './Console'
import { AuthForm, type Profile } from './auth'
import { sessionApi } from './session-api'

export function App() {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    void sessionApi<Profile>('/auth/me', { signal: controller.signal }).then(setProfile)
      .catch((e: unknown) => { if (!controller.signal.aborted && e instanceof Error && !e.message.includes('Sign in')) setError(e.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    const expired = () => { setProfile(null); setError('Your session ended. Sign in to continue.') }
    window.addEventListener('sentinel-session-expired', expired)
    const updated = () => { void sessionApi<Profile>('/auth/me', { signal: controller.signal }).then(setProfile).catch(() => undefined) }
    window.addEventListener('sentinel-account-updated', updated)
    return () => { controller.abort(); window.removeEventListener('sentinel-session-expired', expired); window.removeEventListener('sentinel-account-updated', updated) }
  }, [])
  if (loading) return <main className="auth-screen"><p role="status">Connecting to your account…</p></main>
  if (!profile) return <AuthForm error={error} onAuthenticated={p => { setProfile(p); setError(''); window.location.hash = '/overview' }} />
  const logout = async () => {
    try { await sessionApi('/auth/logout', { method: 'POST' }); setProfile(null); setError('') }
    catch (e) { setError(e instanceof Error ? e.message : 'Sign out failed.') }
  }
  return <Console key={`${profile.account.id}:${profile.id}`} profile={profile} onLogout={() => { void logout() }} accountError={error} />
}
