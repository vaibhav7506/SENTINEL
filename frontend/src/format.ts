export const percent = (n: number | null | undefined) => n == null ? '—' : `${(n * 100).toFixed(1)}%`
export const number = (n: number | null | undefined, digits = 3) => n == null ? '—' : n.toFixed(digits)
export const timestamp = (value: string | null | undefined) => value ? new Date(value).toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : 'Not recorded'
export const duration = (n: number | null | undefined) => n == null ? '—' : n >= 60 ? `${(n / 60).toFixed(1)} min` : `${n.toFixed(0)} sec`
export const human = (s: string) => s.replaceAll('_', ' ')
export const hostLink = (id: string) => `#/hosts/${encodeURIComponent(id)}`
