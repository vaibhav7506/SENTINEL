/* global URL, Response, AbortController, AbortSignal */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import ts from 'typescript'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const output = new URL('../.runtime/tests/', import.meta.url)
await mkdir(output, { recursive: true })
for (const name of await readdir(new URL('../src/', import.meta.url))) {
  if (!/\.tsx?$/.test(name)) continue
  const source = await readFile(new URL(`../src/${name}`, import.meta.url), 'utf8')
  const result = ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023,
  } })
  const code = result.outputText.replace(/(from\s+['"])(\.\/[^'"]+)(['"])/g, '$1$2.mjs$3')
  await writeFile(new URL(name.replace(/\.tsx?$/, '.mjs'), output), code)
}
const { number, percent, duration, hostLink } = await import(new URL('format.mjs', output))
const { Drivers, JsonFacts, LineChart, Table } = await import(new URL('ui.mjs', output))
const { loadApi } = await import(new URL('api.mjs', output))
const { sessionApi } = await import(new URL('session-api.mjs', output))
const render = (component, props) => renderToStaticMarkup(createElement(component, props))

test('browser mutations use cookies and CSRF headers; expired sessions clear views', async () => {
  const originalFetch = globalThis.fetch
  const originalDocument = globalThis.document
  const originalWindow = globalThis.window
  let expired = false
  try {
    globalThis.document = { cookie: 'sentinel_csrf=csrf_test' }
    globalThis.window = { dispatchEvent: () => { expired = true } }
    globalThis.fetch = async (_url, options) => {
      assert.equal(options.credentials, 'same-origin')
      assert.equal(options.cache, 'no-store')
      assert.equal(options.headers.get('X-CSRF-Token'), 'csrf_test')
      assert.equal(options.headers.get('Content-Type'), 'application/json')
      return new Response(null, { status: 204 })
    }
    await sessionApi('/hosts/enrollment-tokens', { method: 'POST', body: '{}' })
    globalThis.fetch = async () => new Response('{}', { status: 401 })
    await assert.rejects(sessionApi('/hosts'))
    assert.equal(expired, true)
  } finally { globalThis.fetch = originalFetch; globalThis.document = originalDocument; globalThis.window = originalWindow }
})

test('missing measurements and lead time stay unknown while real zero is visible', () => {
  assert.equal(number(null), '—')
  assert.equal(percent(undefined), '—')
  assert.equal(duration(null), '—')
  assert.equal(percent(0), '0.0%')
  assert.equal(duration(0), '0 sec')
  assert.equal(hostLink('a/?x=<script>'), '#/hosts/a%2F%3Fx%3D%3Cscript%3E')
})
test('empty telemetry and below-cutoff explanation do not fabricate samples', () => {
  assert.match(render(LineChart, { points: [], label: 'CPU' }), /No samples in this window/)
  assert.match(render(Drivers, { drivers: [] }), /No SHAP drivers recorded/)
})
test('recorded charts retain forecast cutoff and gaps', () => {
  const html = render(LineChart, { points: [[0, .1], [60, .2], [120, .3], [900, .4]], threshold: .8, label: 'Risk' })
  assert.match(html, /cutoff/)
  assert.match(html, /4 recorded samples/)
  assert.match(html, /M[^" ]+ L[^" ]+ L[^" ]+ M/)
})
test('stored hostile text remains escaped and tables stay keyboard accessible', () => {
  const html = render(JsonFacts, { value: { text: '<script>alert(1)</script>' } })
  assert.ok(!html.includes('<script>'))
  assert.match(html, /&lt;script&gt;/)
  assert.match(render(Table, { headers: ['Host'], children: null }), /tabindex="0"/)
})
test('API errors, corrupt JSON, incomplete payloads and oversized bodies are rejected', async () => {
  const originalFetch = globalThis.fetch
  try {
    for (const response of [new Response('{}', { status: 503 }), new Response('corrupt'), new Response('{}'), new Response('x'.repeat(4 * 1024 * 1024 + 1))]) {
      globalThis.fetch = async () => response
      await assert.rejects(loadApi('/console/snapshot', new AbortController().signal))
    }
  } finally { globalThis.fetch = originalFetch }
})
test('valid host response survives transport and an aborted attempt can recover', async () => {
  const originalFetch = globalThis.fetch
  const data = { as_of: '2026-09-27T00:00:00Z', host_id: 'fixture', telemetry: { status: 'unavailable', series: [] }, predictions: [], incidents: [] }
  try {
    globalThis.fetch = async (_url, { signal }) => {
      signal.throwIfAborted()
      return Response.json(data)
    }
    await assert.rejects(loadApi('/console/hosts/fixture', AbortSignal.abort()))
    assert.deepEqual(await loadApi('/console/hosts/fixture', new AbortController().signal), data)
  } finally { globalThis.fetch = originalFetch }
})
