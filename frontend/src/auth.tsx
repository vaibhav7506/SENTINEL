import { useState, type FormEvent } from 'react'
import { sessionApi } from './session-api'

export type Profile = { id: string; email: string; role: 'OWNER' | 'ADMIN' | 'MEMBER'; account: { id: string; name: string } }

export function AuthForm({ onAuthenticated, error: initialError }: { onAuthenticated: (profile: Profile) => void; error: string }) {
  const [signup, setSignup] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    const payload = { email: String(form.get('email')), password: String(form.get('password')), ...(signup ? { account_name: String(form.get('account')) } : {}) }
    try { onAuthenticated(await sessionApi<Profile>(signup ? '/auth/register' : '/auth/login', { method: 'POST', body: JSON.stringify(payload) })) }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to connect.') }
    finally { setBusy(false) }
  }
  return <main className="auth-screen"><section className="auth-card">
    <a className="auth-brand" href="#/login"><span className="brand-mark">S</span>SENTINEL<span>+</span></a>
    <span className="eyebrow">OBSERVE · PREDICT · REVIEW</span>
    <h1>{signup ? 'Create your workspace' : 'Welcome back'}</h1>
    <p>{signup ? 'Start an account for your team and enroll your first host.' : 'Sign in to see your hosts, forecasts, and incidents.'}</p>
    {(error || initialError) && <p className="notice danger" role="alert">{error || initialError}</p>}
    <form onSubmit={event => { void submit(event) }} className="account-form">
      {signup && <label>Workspace name<input name="account" required maxLength={160} autoComplete="organization" placeholder="Your team" /></label>}
      <label>Email<input name="email" type="email" required maxLength={320} autoComplete="username" placeholder="you@example.com" /></label>
      <label>Password<input name="password" type="password" required minLength={12} maxLength={128} autoComplete={signup ? 'new-password' : 'current-password'} /></label>
      <small>Use at least 12 characters.</small>
      <button className="primary-action" disabled={busy}>{busy ? 'Connecting…' : signup ? 'Create workspace' : 'Sign in'}</button>
    </form>
    <button className="auth-switch" onClick={() => { setSignup(v => !v); setError('') }} disabled={busy}>{signup ? 'Already have an account? Sign in' : 'New to Sentinel? Create a workspace'}</button>
    <div className="auth-foot">Your workspace. Your machines. Human review at every step.</div>
  </section></main>
}
