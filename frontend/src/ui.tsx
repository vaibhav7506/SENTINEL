import { Component, type ReactNode } from 'react'
import type { Driver } from './types'
import { number } from './format'

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone}`}><span className="dot" />{children}</span> }
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty"><span aria-hidden="true">◎</span><h3>{title}</h3><p>{children}</p></div> }
export function Panel({ title, kicker, action, children, className = '' }: { title: string; kicker?: string; action?: ReactNode; children: ReactNode; className?: string }) { return <section className={`panel ${className}`}><div className="panel-heading"><div>{kicker && <span className="eyebrow">{kicker}</span>}<h2>{title}</h2></div>{action}</div>{children}</section> }
export function Stat({ label, value, note, tone = '' }: { label: string; value: ReactNode; note?: string; tone?: string }) { return <div className={`stat ${tone}`}><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</div> }
export function Table({ headers, children }: { headers: string[]; children: ReactNode }) { return <div className="table-scroll" tabIndex={0} role="region" aria-label={headers.join(', ')}><table><thead><tr>{headers.map(h => <th key={h} scope="col">{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div> }
export function JsonFacts({ value }: { value: unknown }) { return <pre className="json-facts">{JSON.stringify(value, null, 2)}</pre> }
export function Drivers({ drivers }: { drivers: Driver[] | undefined }) { return drivers?.length ? <div className="drivers">{drivers.map(d => <div key={d.feature}><code>{d.feature}</code><span className={d.contribution > 0 ? 'text-danger' : 'text-good'}>{d.contribution > 0 ? '+' : ''}{number(d.contribution, 5)}</span><small>Input {number(d.value)}</small></div>)}<p className="caption">SHAP associations in probability units. Correlated inputs can share attribution; these are not causal diagnoses.</p></div> : <Empty title="No SHAP drivers recorded">SHAP is computed when the frozen model threshold is crossed. Below-threshold scores have no explanation.</Empty> }
export function LineChart({ points, threshold, label, unit = '' }: { points: [number, number][]; threshold?: number; label: string; unit?: string }) {
  if (!points.length) return <Empty title="No samples in this window">Charts appear when recorded data is available.</Empty>
  const values = points.map(p => p[1])
  const lower = threshold != null ? 0 : Math.min(0, ...values)
  const upper = threshold != null ? 1 : Math.max(0, ...values, lower + 0.01) * 1.08
  const first = Math.min(...points.map(p => p[0])), last = Math.max(...points.map(p => p[0]))
  const x = (t: number) => 48 + (t - first) / Math.max(last - first, 1) * 630
  const y = (v: number) => 148 - (v - lower) / (upper - lower) * 118
  const intervals = points.slice(1).map((p, i) => p[0] - points[i][0]).filter(t => t > 0).sort((a, b) => a - b)
  const gap = (intervals[Math.floor(intervals.length / 2)] ?? 60) * 2.5
  const path = points.map(([t, v], i) => `${i && t - points[i - 1][0] <= gap ? 'L' : 'M'}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  return <div className="chart"><svg viewBox="0 0 720 180" role="img" aria-label={`${label}. ${points.length} recorded samples; latest ${number(values.at(-1))}${unit}.`}><title>{label}</title>{[lower, (upper + lower) / 2, upper].map(v => <g key={v}><line x1="48" x2="678" y1={y(v)} y2={y(v)} className="grid-line" /><text x="3" y={y(v) + 4}>{number(v, upper > 10 ? 0 : 2)}{unit}</text></g>)}{threshold != null && <><line x1="48" x2="678" y1={y(threshold)} y2={y(threshold)} className="threshold-line" /><text x="680" y={y(threshold) + 4}>cutoff</text></>}<path d={path} className="chart-path" />{points.length === 1 && <circle cx={x(points[0][0])} cy={y(points[0][1])} r="4" fill="currentColor" />}<text x="48" y="172">{new Date(first * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</text><text x="678" y="172" textAnchor="end">{new Date(last * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</text></svg></div>
}
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <main className="fatal" role="alert"><h1>The console could not render this view.</h1><p>Your backend records are still stored. Reload to recover.</p><button onClick={() => window.location.reload()}>Reload console</button></main> : this.props.children }
}
