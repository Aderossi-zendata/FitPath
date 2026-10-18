// Thin client for the FitPath FastAPI backend (proxied at /api in dev)

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export type Status = {
  data: Record<string, boolean>
  llm: { provider: string; model: string; fallbacks?: string[]; configured: boolean }
  date_range: [string, string] | null
}
export type SimRequest = {
  start: string; days: number; store_size: SizeKey
  tills_open: number; seed: number; label?: string
}
export type SizeKey = 'low' | 'medium' | 'high'
export type Size = { label: string; area: string; visitors: [number, number]; conversion: [number, number] }
export type VolumeDay = { date: string; index: number; visitors: [number, number]; buyers: [number, number] }
export type Job = { id: string; state: 'running' | 'done' | 'error'; done_days: number; days: number; error?: string }
export type RunInfo = { run_id: string; start: string; days: number; label: string | null; layout: string; totals: Totals }
export type Totals = {
  visits: number; receipts: number; conversion: number; revenue: number; intent_eur: number; lost_eur: number
  avg_ticket: number; avg_minutes: number; browsers: number; browser_conversion: number
}
export type Summary = {
  run_id: string
  config: { start: string; days: number; store_size?: SizeKey; layout: StoreLayout }
  store_size?: Size & { key: SizeKey }
  day_plan?: { date: string; index: number; visitors: number; intent_conversion: number; buyers: number }[]
  totals: Totals
  daily: { date: string; visits: number; receipts: number; revenue: number; lost_fit: number; lost_till: number; minutes: number; conversion: number; volume_index: number }[]
  zones: { zone: string; name: string; stops: number; dwell_s: number; reach: number; conversion: number; revenue: number; picked: number; bought: number }[]
  personas: { persona: number; name: string; visits: number; receipts: number; revenue: number; minutes: number; fitting: number; lost: number; conversion: number; avg_ticket: number }[]
  fitting: { users: number; abandon_share: number; avg_wait_s: number; p90_wait_s: number; conv_short_wait: number; conv_long_wait: number | null; lost_eur: number; long_wait_threshold_s: number; cabins_open: number }
  tills: { avg_wait_s: number; p90_wait_s: number; abandons: number; lost_eur: number; tills_open: number }
  matching: { receipts: number; correct_share: number; avg_purity: number; id_switches: number; camera_coverage: number; tracks: number }
  hourly: { daytype: string; slot: number; time: string; in_store: number; fit_queue: number; till_queue: number; cabins_busy: number }[]
  top_articles: { article_id: number; name: string; ptype: string; zone: string; units: number; revenue: number }[]
  impulse_by_zone: { zone: string; units: number; revenue: number }[]
  note: string
}
export type Lift = { season: string; cat: string; units: number; share_season: number; share_all: number; lift: number }
export type Pair = { a: string | number; b: string | number; baskets: number; support: number; conf_a_to_b: number; conf_b_to_a: number; lift: number }
export type Real = {
  overview: { start: string; end: string; units: number; customers: number; articles: number; revenue_eur: number; baskets: number; avg_basket_units: number; avg_ticket_eur: number }
  weekly: { week: string; revenue_eur: number; units: number; baskets: number }[]
  monthly_by_group: { month: string; igroup: string; revenue_eur: number; units: number }[]
  season_lift: Record<string, { up: Lift[]; down: Lift[] }>
  weekday: { days: { dow: number; units: number; revenue_eur: number; baskets: number }[]; weekend_lift: { garment: string; lift: number; weekend_share: number }[]; weekday_lift: { garment: string; lift: number; weekend_share: number }[] }
  month_day: { dom: number; avg_rev: number; index: number }[]
  markdowns: { week: string; avg_discount: number; share_discounted: number }[]
  top_by_season: Record<string, { product_code: number; name: string; ptype: string; igroup: string; units: number; revenue_eur: number }[]>
  trends: { weeks: number; rising: { ptype: string; units_now: number; units_year_ago: number; share_change: number }[]; falling: { ptype: string; units_now: number; units_year_ago: number; share_change: number }[] }
  basket_types: { top_lift: Pair[]; most_common: Pair[] }
  basket_articles: { top_lift: Pair[]; most_common: Pair[]; names: Record<string, string> }
  price_note: string
}
export type Persona = {
  id: number; name: string; description: string; customers: number; share_customers: number; share_store_visits: number
  profile: Record<string, number>; club_active_share: number
  behaviour: { derived: Record<string, unknown> & { zone_weights: Record<string, number> }; assumed: Record<string, number> }
}
export type Advice = {
  headline: string; generated_by: 'llm' | 'rules'; model?: string; note?: string
  recommendations: { title: string; why: string; action: string; impact: string; source: 'real' | 'simulated'; priority: 'high' | 'medium' | 'low'; area: string }[]
}

export type Fixture = { t: 'rack' | 'table' | 'wall'; x: number; z: number; w?: number; d?: number }
export type StoreRect = { x0: number; z0: number; w: number; d: number; door: [number, number] }
export type Block = { x0: number; x1: number; z0: number; z1: number; name: string; cls: string }
export type StoreLayout = {
  name: string; fixtures: Record<string, Fixture[]>; cabins: number[]; cabins_open: number
  tills: number[]; tills_open: number; mannequins: [number, number][]
  store: StoreRect; area_m2: number; blocks: Record<string, Block>; assign: Record<string, string>
  fitting: { door_z: number; in_z: number; x0: number; x1: number; queue: { x: number; z: number; dx: number } }
  till_z: number; pay_z: number; entrance: { x: number; z_in: number; z_out: number; exit_x: number }
  decompression?: { x0: number; x1: number; z0: number; z1: number }; columns?: [number, number][]
  decor?: { t: string; x: number; z: number; w: number; d: number; label?: string }[]; zone_names?: Record<string, string>
}
export type LayoutResponse = {
  layout: StoreLayout
  store?: StoreRect
  cameras: { x: number; z: number; r: number; name: string }[]
  space_fit?: Record<string, number>
}
export type BehaviorParam = { key: string; name: string; what: string; range: [number, number] | Record<string, [number, number]>; unit: string; sources: string[] }
export type BehaviorInfo = { params: BehaviorParam[]; sources: Record<string, { label: string; url: string }>; not_modelled: { what: string; url: string | null }[]; note: string }
export type ReplayVisit = {
  visit_id: number; persona: number; kind: string; enter_t: number; exit_t: number
  receipt_no: number | null; bought_eur: number; used_fitting: boolean; outcome: string
}
/** tracks rows are [t, track, visit_id, x, z] */
export type Replay = { day: string; tracks: [number, number, number, number, number][]; visits: ReplayVisit[] }


export type Period = { key: string; label: string; start: string; days: number; mode: 'future' | 'history'; cutoff: string | null }
export type Strategy = {
  name: string
  extra_till?: { from: number; to: number; days: string }
  fitting_attendant?: { from: number; to: number; days: string }
  layout?: Record<string, string | string[]>
  discount?: { zone: string; pct: number; days: string }[]
  cross_merch?: { from: string; to: string }[]
  multibuy?: { zone: string; type: string; days: string }
  clearance?: { pct: number; days: string }
  bundle?: { a: string; b: string; pct: number }[]
  feature?: { garment: string; days: string }
  rationale?: string; source?: 'real' | 'simulated'; expected?: string; levers?: string[]
}
export type Proposal = { generated_by: 'llm' | 'rules'; model?: string; note?: string; strategies: Strategy[]; period: Period }
export type Delta = { base: number; delta: number; ci90: [number, number] | null; delta_pct: number | null }
export type Experiment = {
  id: string; label: string; strategy: Strategy; levers: string[]; verdict: 'worth it' | 'not worth it' | 'uncertain'
  settings: { start: string; days: number; size: string; mode: string; cutoff: string | null; reps: number }
  summary: Record<'revenue' | 'margin' | 'profit' | 'receipts' | 'visits' | 'conversion' | 'lost_eur' | 'lost_fit' | 'lost_till' | 'till_wait_s' | 'fit_wait_s' | 'fit_abandon', Delta>
  day_index: number[]; seconds: number; created: number
  assumptions: Record<string, number>
  explain?: Explain
}
export type ExperimentRow = { id: string; label: string; levers: string[]; verdict: Experiment['verdict']; settings: Experiment['settings']; created: number; profit: Delta }
export type Explain = { explanation: string; next: Strategy | null; generated_by: string; model?: string }
export type Backtest = { cutoff: string; days: number; wape_daily: number; wape_weekly: number; naive_last_year_wape_daily: number; trend: number; series: { date: string; pred: number; actual: number }[]; note: string }
export type SearchResult = {
  id: string; seconds: number
  settings: { start: string; days: number; size: string; mode: string; n_candidates: number; n_explore: number; explore_reps: number; verify_reps: number }
  constraints: { max_staff_hours_per_day: number; max_discounts: number }
  explored: { strategy: Strategy; label: string; profit: number; from_llm: boolean }[]
  ranked: { strategy: Strategy; label: string; pred_profit: number; pred_std: number }[]
  verified: { experiment_id: string; label: string; strategy: Strategy; summary: Experiment['summary']; verdict: Experiment['verdict'] }[]
  ranker: { type: string; loo_corr: number | null; trained_on: number }
}
export type AnyJob = { id: string; state: 'running' | 'done' | 'error'; phase?: string; done: number; total: number; result_id?: string; error?: string }

export type Finding = { key: string; title: string; loss_eur: number | null; area: string; text: string; focus: { day: string; t: number; value: number; label: string } | null; focus_text?: string | null; zone?: string }
export type Diagnosis = { run_id: string; totals: Totals; lost: { fitting: number; tills: number; other: number }; findings: Finding[]; days: string[] }
export type PlanItem = { experiment_id: string; label: string; levers: string[]; verdict: Experiment['verdict']; settings: { start: string; days: number; size: string; mode: string }; profit: Delta; revenue: Delta; conversion: Delta; note: string | null; added: number }

export type ToolPart = {
  type: 'tool'; call_id: string; name: string; title: string; args: Record<string, unknown>
  status: 'pending_approval' | 'running' | 'done' | 'error' | 'rejected'
  progress?: { done: number; total: number; phase: string }; result?: unknown; summary?: string; error?: string
  ui?: { show_run?: string; label?: string; experiment_id?: string; preview?: { layout: StoreLayout; strategy: Strategy; levers: string[] } }
  started?: number; ended?: number
}
export type MsgPart = { type: 'text' | 'error'; text: string } | ToolPart
export type AgentMessage = { id: string; role: 'user'; text: string; time: number } | { id: string; role: 'assistant'; parts: MsgPart[]; model: string | null; started: number; ended?: number }
export type AgentSession = { id: string; title: string; created: number; updated: number; busy: boolean; messages: AgentMessage[] }
export type AgentSessionMeta = { id: string; title: string; updated: number; busy: boolean; messages: number }
export type Suggestion = { id: string; title: string; why: string; source: 'real' | 'simulated' | 'user'; strategy: Strategy; levers: string[]; session_id: string; created: number; experiment_id: string | null; verdict: Experiment['verdict'] | null; profit?: number }
export type StoreNote = { id: string; text: string; effect: Record<string, number>; created: number }

export const api = {
  status: () => req<Status>('/status'),
  sizes: () => req<{ sizes: Record<SizeKey, Size>; sources: { label: string; url: string }[] }>('/sizes'),
  volume: (start: string, days: number, size: SizeKey) => req<VolumeDay[]>(`/volume?start=${start}&days=${days}&size=${size}`),
  real: () => req<Real>('/real?scope=store'),
  personas: () => req<{ personas: Persona[]; customers_clustered: number; k: number }>('/personas'),
  runs: () => req<RunInfo[]>('/sim/runs'),
  run: (id: string) => req<Summary>(`/sim/runs/${id}`),
  startSim: (body: SimRequest) => req<Job>('/sim', { method: 'POST', body: JSON.stringify(body) }),
  job: (id: string) => req<Job>(`/sim/jobs/${id}`),
  advice: (run_id: string | null) => req<Advice>('/advice', { method: 'POST', body: JSON.stringify({ run_id }) }),
  layout: () => req<LayoutResponse>('/layout'),
  behavior: () => req<BehaviorInfo>('/behavior'),
  replay: (run_id: string, day: string, start: number, end: number, every = 2) =>
    req<Replay>(`/sim/runs/${run_id}/replay?day=${day}&start=${start}&end=${end}&every=${every}`),
  cachedAdvice: (run_id: string) => req<Advice>(`/advice/${run_id}`),
  periods: () => req<{ periods: Period[] }>('/periods'),
  backtest: () => req<Backtest>('/forecast/backtest'),
  propose: (period: string, run_id: string | null) => req<Proposal>('/strategies/propose', { method: 'POST', body: JSON.stringify({ period, run_id }) }),
  lastProposal: () => req<Proposal>('/strategies/last'),
  startExperiment: (body: { strategy: Strategy; period: string; size: string; reps: number; label?: string }) => req<AnyJob>('/experiments', { method: 'POST', body: JSON.stringify(body) }),
  experiments: () => req<ExperimentRow[]>('/experiments'),
  experiment: (id: string) => req<Experiment>(`/experiments/${id}`),
  explain: (id: string) => req<Explain>(`/experiments/${id}/explain`, { method: 'POST' }),
  startSearch: (body: { period: string; size: string; include_llm: boolean; n_explore: number }) => req<AnyJob>('/search', { method: 'POST', body: JSON.stringify(body) }),
  searches: () => req<{ id: string; created: number; seconds: number; settings: SearchResult['settings'] }[]>('/searches'),
  search: (id: string) => req<SearchResult>(`/searches/${id}`),
  anyJob: (id: string) => req<AnyJob>(`/sim/jobs/${id}`),
  agentSessions: () => req<AgentSessionMeta[]>('/agent/sessions'),
  agentNew: () => req<AgentSession>('/agent/sessions', { method: 'POST' }),
  agentSession: (id: string) => req<AgentSession>(`/agent/sessions/${id}`),
  agentDelete: (id: string) => req<{ ok: boolean }>(`/agent/sessions/${id}`, { method: 'DELETE' }),
  agentSend: (id: string, text: string) => req<AgentSession>(`/agent/sessions/${id}/messages`, { method: 'POST', body: JSON.stringify({ text }) }),
  agentApprove: (id: string, call_id: string, approve: boolean) => req<{ ok: boolean }>(`/agent/sessions/${id}/approve`, { method: 'POST', body: JSON.stringify({ call_id, approve }) }),
  agentStop: (id: string) => req<{ ok: boolean }>(`/agent/sessions/${id}/stop`, { method: 'POST' }),
  agentInfo: () => req<{ models: string[]; tools: { name: string; title: string }[] }>('/agent/info'),
  suggestions: () => req<Suggestion[]>('/agent/suggestions'),
  removeSuggestion: (id: string) => req<Suggestion[]>(`/agent/suggestions/${id}`, { method: 'DELETE' }),
  notes: () => req<{ notes: StoreNote[]; constraints: { cabins_open: number; tills_open: number } }>('/agent/notes'),
  removeNote: (id: string) => req<{ notes: StoreNote[]; constraints: { cabins_open: number; tills_open: number } }>(`/agent/notes/${id}`, { method: 'DELETE' }),
  preview: (strategy: Strategy) => req<{ layout: StoreLayout; strategy: Strategy; levers: string[] }>('/strategy/preview', { method: 'POST', body: JSON.stringify({ strategy }) }),
  diagnosis: (run_id: string) => req<Diagnosis>(`/sim/runs/${run_id}/diagnosis`),
  plan: () => req<PlanItem[]>('/plan'),
  addPlan: (experiment_id: string, note?: string) => req<PlanItem[]>('/plan', { method: 'POST', body: JSON.stringify({ experiment_id, note }) }),
  removePlan: (experiment_id: string) => req<PlanItem[]>(`/plan/${experiment_id}`, { method: 'DELETE' }),
}
