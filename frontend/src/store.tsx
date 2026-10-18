// App-wide state: data loaded once, the current store analysis, strategy settings and every background job.
// Jobs are tracked here (not in pages), so switching tab or reloading never loses a running or finished job.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type AnyJob, type Diagnosis, type ExperimentRow, type Persona, type PlanItem, type Real, type RunInfo, type SizeKey, type Status, type Summary } from './api'

type Persisted = {
  runId: string | null
  jobs: Record<string, string>          // tag -> job id (tags: "analysis", "exp:<key>", "search")
  period: string; size: SizeKey; reps: number
}
const KEY = 'fitpath-state-v1'
const SIZES: readonly SizeKey[] = ['low', 'medium', 'high']
// older versions stored Italian period keys: map them to the current ones
const OLD_PERIODS: Record<string, string> = { 'weekend-futuro': 'next-weekend', 'settimana-futura': 'next-week' }
const load = (): Persisted => {
  const base: Persisted = { runId: null, jobs: {}, period: 'next-week', size: 'medium', reps: 4 }
  try {
    const p: Persisted = { ...base, ...JSON.parse(localStorage.getItem(KEY) || '{}') }
    // older versions stored other size names: anything that is not low/medium/high becomes 'medium'
    if (!SIZES.includes(p.size)) p.size = 'medium'
    p.period = OLD_PERIODS[p.period] ?? p.period
    return p
  } catch { return base }
}

export const strategyKey = (s: object) => JSON.stringify(s, Object.keys(s).sort())

type Ctx = {
  status: Status | null; real: Real | null; personas: { personas: Persona[]; customers_clustered: number; k: number } | null
  runs: RunInfo[]; runId: string | null; summary: Summary | null; diagnosis: Diagnosis | null
  setRunId: (id: string | null) => void
  period: string; size: SizeKey; reps: number
  setPeriod: (p: string) => void; setSize: (s: SizeKey) => void; setReps: (n: number) => void
  jobs: Record<string, AnyJob>
  startJob: (tag: string, job: AnyJob) => void
  experiments: ExperimentRow[]; reloadExperiments: () => void
  searchId: string | null
  plan: PlanItem[]; setPlan: (p: PlanItem[]) => void
  error: string | null
}
const AppCtx = createContext<Ctx | null>(null)
export const useApp = () => { const c = useContext(AppCtx); if (!c) throw new Error('no store'); return c }

export function AppProvider({ children }: { children: ReactNode }) {
  const [p, setP] = useState<Persisted>(load)
  const [status, setStatus] = useState<Status | null>(null)
  const [real, setReal] = useState<Real | null>(null)
  const [personas, setPersonas] = useState<Ctx['personas']>(null)
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null)
  const [jobs, setJobs] = useState<Record<string, AnyJob>>({})
  const [experiments, setExperiments] = useState<ExperimentRow[]>([])
  const [searchId, setSearchId] = useState<string | null>(null)
  const [plan, setPlan] = useState<PlanItem[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { try { localStorage.setItem(KEY, JSON.stringify(p)) } catch { /* private mode */ } }, [p])
  const patch = useCallback((x: Partial<Persisted>) => setP(o => ({ ...o, ...x })), [])

  const reloadExperiments = useCallback(() => { api.experiments().then(setExperiments).catch(() => {}) }, [])
  const reloadRuns = useCallback(() => api.runs().then(r => { setRuns(r); return r }), [])

  useEffect(() => {
    api.status().then(setStatus).catch(e => setError(`Backend unreachable: ${e.message}`))
    api.real().then(setReal).catch(() => {})
    api.personas().then(setPersonas).catch(() => {})
    api.plan().then(setPlan).catch(() => {})
    api.searches().then(s => setSearchId(s[0]?.id ?? null)).catch(() => {})
    reloadExperiments()
    reloadRuns().then(r => { if (!p.runId && r[0]) patch({ runId: r[0].run_id }) }).catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setSummary(null); setDiagnosis(null)
    if (!p.runId) return
    api.run(p.runId).then(setSummary).catch(() => patch({ runId: null }))
    api.diagnosis(p.runId).then(setDiagnosis).catch(() => {})
  }, [p.runId, patch])

  // poll every tracked job; react to completion here so it works whatever page is open
  const pRef = useRef(p); pRef.current = p
  useEffect(() => {
    const tick = async () => {
      const entries = Object.entries(pRef.current.jobs)
      for (const [tag, id] of entries) {
        const j = await api.anyJob(id).catch(() => null)
        if (!j) { patch({ jobs: Object.fromEntries(Object.entries(pRef.current.jobs).filter(([t]) => t !== tag)) }); continue }
        setJobs(o => ({ ...o, [tag]: j }))
        if (j.state === 'running') continue
        patch({ jobs: Object.fromEntries(Object.entries(pRef.current.jobs).filter(([t]) => t !== tag)) })
        if (j.state === 'done') {
          if (tag === 'analysis') { await reloadRuns(); patch({ runId: j.id }) }
          if (tag.startsWith('exp:') || tag === 'search') reloadExperiments()
          if (tag === 'search' && j.result_id) setSearchId(j.result_id)
        }
      }
    }
    const h = window.setInterval(tick, 1200)
    return () => window.clearInterval(h)
  }, [patch, reloadExperiments, reloadRuns])

  const startJob = useCallback((tag: string, job: AnyJob) => {
    setJobs(o => ({ ...o, [tag]: job }))
    patch({ jobs: { ...pRef.current.jobs, [tag]: job.id } })
  }, [patch])

  const value = useMemo<Ctx>(() => ({
    status, real, personas, runs, runId: p.runId, summary, diagnosis, setRunId: id => patch({ runId: id }),
    period: p.period, size: p.size, reps: p.reps,
    setPeriod: v => patch({ period: v }), setSize: v => patch({ size: v }), setReps: v => patch({ reps: v }),
    jobs, startJob, experiments, reloadExperiments, searchId, plan, setPlan, error,
  }), [status, real, personas, runs, p, summary, diagnosis, jobs, startJob, experiments, reloadExperiments, searchId, plan, error, patch])

  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>
}
