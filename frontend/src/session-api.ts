export async function sessionApi<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET') {
    const value = document.cookie.split('; ').find(c => /^(?:__Host-)?sentinel_csrf=/.test(c))?.split('=')[1]
    if (value) headers.set('X-CSRF-Token', decodeURIComponent(value))
  }
  const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin', cache: 'no-store' })
  if (response.status === 401) {
    if (!path.startsWith('/auth/')) window.dispatchEvent(new Event('sentinel-session-expired'))
    throw new Error(path === '/auth/login' ? 'Email or password is incorrect.' : 'Sign in to continue.')
  }
  if (!response.ok) {
    let detail = `Request failed (HTTP ${response.status}).`
    try { const body: unknown = await response.json(); if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') detail = body.detail } catch { /* Keep the safe status message. */ }
    throw new Error(detail)
  }
  return response.status === 204 ? undefined as T : await response.json() as T
}
