import { useCallback, useEffect, useState } from 'react'

export async function loadApi(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(`/api${path}`, { signal })
  if (response.status === 401) {
    window.dispatchEvent(new Event('sentinel-session-expired'))
    throw new Error('Your session ended. Sign in to continue.')
  }
  if (!response.ok) throw new Error(response.status === 404 ? 'This record was not found.' : `The API could not load this view (HTTP ${response.status}).`)
  const reader = response.body?.getReader()
  if (!reader) throw new Error('The API returned an empty response.')
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > 4 * 1024 * 1024) throw new Error('The API response exceeded the view limit.')
      chunks.push(value)
    }
  } finally { await reader.cancel() }
  const bytes = new Uint8Array(size)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  let data: unknown
  try { data = JSON.parse(new TextDecoder().decode(bytes)) }
  catch { throw new Error('The API returned invalid JSON.') }
  if (!data || typeof data !== 'object' || !('as_of' in data) || typeof data.as_of !== 'string' || !Number.isFinite(Date.parse(data.as_of))) throw new Error('The API returned an invalid view.')
  if (path === '/console/snapshot') {
    for (const key of ['hosts', 'predictions', 'incidents', 'alerts', 'proposals', 'models', 'evaluations', 'experiments', 'failure_events']) {
      if (!(key in data) || !Array.isArray(data[key as keyof typeof data])) throw new Error('The API returned an incomplete snapshot.')
    }
    for (const key of ['totals', 'overview', 'governance']) {
      if (!(key in data) || !data[key as keyof typeof data] || typeof data[key as keyof typeof data] !== 'object') throw new Error('The API returned an incomplete snapshot.')
    }
  } else if (!('host_id' in data) || !('telemetry' in data) || !('predictions' in data) || !Array.isArray(data.predictions) || !('incidents' in data) || !Array.isArray(data.incidents)) throw new Error('The API returned an invalid host view.')
  return data
}

export function useApi<T>(path: string) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true })
  const [revision, setRevision] = useState(0)
  const retry = useCallback(() => setRevision(n => n + 1), [])
  useEffect(() => {
    let controller: AbortController | null = null
    let active = true
    let fetching = false
    const load = async () => {
      if (fetching) return
      fetching = true
      controller = new AbortController()
      const attempt = controller
      const timeout = window.setTimeout(() => attempt.abort(), 10000)
      try {
        const data = await loadApi(path, attempt.signal) as T
        if (active) setState({ data, error: null, loading: false })
      } catch (error) {
        if (active) setState(previous => ({ ...previous, error: attempt.signal.aborted ? 'The request timed out. Retrying automatically.' : error instanceof Error ? error.message : 'The API is unavailable.', loading: false }))
      } finally {
        window.clearTimeout(timeout)
        fetching = false
      }
    }
    void load()
    const timer = window.setInterval(() => { if (!document.hidden) void load() }, 30000)
    return () => { active = false; controller?.abort(); window.clearInterval(timer) }
  }, [path, revision])
  return { ...state, retry }
}
