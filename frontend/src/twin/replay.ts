// Replay tracks grouped per visit, fetched one hour at a time (a busy day is ~800k samples).
import type { Replay } from '../api'

export const CHUNK_S = 3600
const OVERLAP_S = 8   // each chunk also holds a few seconds of its neighbours, so interpolation never breaks at the hour
const GAP_S = 6       // samples further apart than this mean the person was not tracked (fitting room)

export type Series = { t: number[]; x: number[]; z: number[]; tr: number[] }
export type Chunk = Map<number, Series>
export type At = { x: number; z: number; track: number; heading: number }

export const chunkRange = (h: number) => [h * CHUNK_S - OVERLAP_S, (h + 1) * CHUNK_S + OVERLAP_S] as const

export function buildChunk(rows: Replay['tracks']): Chunk {
  const m: Chunk = new Map()
  for (const [t, track, vid, x, z] of rows) {
    let s = m.get(vid)
    if (!s) { s = { t: [], x: [], z: [], tr: [] }; m.set(vid, s) }
    s.t.push(t); s.x.push(x); s.z.push(z); s.tr.push(track)
  }
  for (const s of m.values()) {
    // the backend returns rows in time order per day, but keep this robust
    if (s.t.some((v, i) => i && v < s.t[i - 1])) {
      const ix = s.t.map((_, i) => i).sort((a, b) => s.t[a] - s.t[b])
      s.t = ix.map(i => s.t[i]); s.x = ix.map(i => s.x[i]); s.z = ix.map(i => s.z[i]); s.tr = ix.map(i => s.tr[i])
    }
  }
  return m
}

/** Last sample index with t <= T, or -1. */
export function idxAt(s: Series, T: number) {
  let lo = 0, hi = s.t.length - 1, r = -1
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (s.t[mid] <= T) { r = mid; lo = mid + 1 } else hi = mid - 1 }
  return r
}

/** Interpolated position at T, or null when the person is not tracked at that instant. */
export function poseAt(s: Series, T: number): At | null {
  const i = idxAt(s, T)
  if (i < 0) return null
  const n = s.t.length
  if (i === n - 1) {
    if (T - s.t[i] > 2) return null
    const j = Math.max(0, i - 1)
    return { x: s.x[i], z: s.z[i], track: s.tr[i], heading: Math.atan2(s.x[i] - s.x[j], s.z[i] - s.z[j]) }
  }
  const dt = s.t[i + 1] - s.t[i]
  if (dt > GAP_S) return null
  const f = (T - s.t[i]) / dt, dx = s.x[i + 1] - s.x[i], dz = s.z[i + 1] - s.z[i]
  return { x: s.x[i] + dx * f, z: s.z[i] + dz * f, track: s.tr[i], heading: dx || dz ? Math.atan2(dx, dz) : 0 }
}

export const clock = (s: number) => {
  const t = Math.max(0, Math.floor(s))
  return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t / 60) % 60).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`
}
