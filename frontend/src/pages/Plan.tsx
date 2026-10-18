import { api, type Strategy } from '../api'
import { DeltaCell, JobProgress, VERDICT } from '../components'
import { strategyKey, useApp } from '../store'
import { Card, Empty, eur } from '../ui'

const pt = (n: number) => `${(n * 100).toFixed(1)} pt`

export default function Plan() {
  const app = useApp()
  const items = app.plan
  const sum = items.reduce((s, i) => s + i.profit.delta, 0)
  const comboJob = app.jobs['exp:combo']

  async function testCombination() {
    // merge the levers of every planned strategy into one and test it as a whole
    const parts = await Promise.all(items.map(i => api.experiment(i.experiment_id)))
    const combo: Strategy = { name: 'Full plan' }
    for (const e of parts) {
      const s = e.strategy
      if (s.extra_till) combo.extra_till = s.extra_till
      if (s.fitting_attendant) combo.fitting_attendant = s.fitting_attendant
      if (s.layout) combo.layout = { ...(combo.layout ?? {}), ...s.layout }
      if (s.discount) combo.discount = [...(combo.discount ?? []), ...s.discount].slice(0, 2)
      if (s.cross_merch) combo.cross_merch = [...(combo.cross_merch ?? []), ...s.cross_merch].slice(0, 2)
    }
    const st = parts[0].settings
    const period = { '2020-09-25': 'next-weekend', '2020-09-28': 'next-week', '2020-11-27': 'bf-2020', '2019-11-29': 'bf-2019' }[st.start] ?? app.period
    app.startJob('exp:combo', await api.startExperiment({ strategy: combo, period, size: st.size, reps: app.reps, label: `Full plan (${items.length} strategies)` }))
    void strategyKey
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plan · what we decided</h1>
          <p>The strategies picked in the comparison, with the effect estimated by the twin. Each strategy was tested on its own: before adopting them together, test the combination, because effects do not always add up.</p>
        </div>
      </div>
      {items.length === 0 ? <Empty>The plan is empty: in Strategies, use “Add to plan” on the strategies that are worth it.</Empty> : (
        <>
          <Card title={`${items.length} ${items.length === 1 ? 'strategy' : 'strategies'} in the plan`} sub={`Indicative total net profit: ${eur(sum)} over the tested periods`}>
            <div className="plan">
              {items.map(i => (
                <article className="rec" key={i.experiment_id}>
                  <div className="meta"><span className={`verdict ${VERDICT[i.verdict].cls}`}>{VERDICT[i.verdict].label}</span><span className="prio">{i.settings.start} · {i.settings.days} {i.settings.days === 1 ? 'day' : 'days'} · {i.settings.size} traffic</span></div>
                  <h3>{i.label}</h3>
                  <ul className="levers">{i.levers.map(l => <li key={l}>{l}</li>)}</ul>
                  <div className="plan-kpis">
                    <div><span>Net profit</span><DeltaCell d={i.profit} fmt={n => eur(n)} better="up" /></div>
                    <div><span>Revenue</span><DeltaCell d={i.revenue} fmt={n => eur(n)} better="up" /></div>
                    <div><span>Conversion</span><DeltaCell d={i.conversion} fmt={pt} better="up" /></div>
                  </div>
                  <button className="btn ghost" onClick={async () => app.setPlan(await api.removePlan(i.experiment_id))}>Remove from plan</button>
                </article>
              ))}
            </div>
          </Card>
          {items.length > 1 && (
            <Card title="Test the whole plan together" sub="Combines the levers of every strategy into one scenario and compares it with the current store.">
              <button className="btn" onClick={testCombination} disabled={comboJob?.state === 'running'}>{comboJob?.state === 'running' ? 'Simulating…' : 'Test the combination'}</button>
              <JobProgress job={comboJob} />
              {comboJob?.state === 'done' && <p className="note">Done: the result is in the comparison in Strategies.</p>}
            </Card>
          )}
        </>
      )}
    </>
  )
}
