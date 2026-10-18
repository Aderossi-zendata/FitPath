import type { Persona } from '../api'
import { Card, Empty, eur, int, pct } from '../ui'

const ZONE_LABEL: Record<string, string> = { new_in: 'New in', ladies: 'Ladies', divided: 'Divided', men: 'Men', kids: 'Kids', denim: 'Denim', basics: 'Basics', accessories: 'Accessories' }
const ZONE_ORDER = ['ladies', 'divided', 'new_in', 'basics', 'accessories', 'denim', 'men', 'kids']
// same colours as the departments in the 3D twin
const ZONE_HEX: Record<string, string> = { new_in: '#f2b84b', ladies: '#e58fa8', divided: '#b592e8', men: '#7fb2e6', kids: '#7fd1a6', denim: '#4a6fa5', basics: '#c9d1d8', accessories: '#b98a52' }

export default function Customers({ data, embedded }: { data: { personas: Persona[]; customers_clustered: number; k: number } | null; embedded?: boolean }) {
  if (!data) return <Empty>Loading profiles…</Empty>
  const ps = [...data.personas].sort((a, b) => b.share_store_visits - a.share_store_visits)
  return (
    <>
      {!embedded && (
        <div className="page-head">
          <div>
            <h1>Customer profiles</h1>
            <p>{data.k} profiles derived by clustering {int(data.customers_clustered)} H&amp;M customers with at least 2 purchases, using frequency, receipt value, lines bought, new-in items, discounts, channel and age. Each profile drives the behaviour of the simulated visitors.</p>
          </div>
        </div>
      )}
      <div className="grid g3">
        {ps.map(p => {
          const d = p.behaviour.derived, a = p.behaviour.assumed
          return (
            <section className="card" key={p.id}>
              <div className="card-head">
                <h3>{p.name}</h3>
                <span className="badge real">Real data</span>
              </div>
              <p className="sub" style={{ color: 'var(--soft)' }}>{p.description}</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div className="kpi"><span>Store visits</span><b>{pct(p.share_store_visits)}</b><small>{pct(p.share_customers)} of customers</small></div>
                <div className="kpi"><span>Average receipt</span><b>{eur(Number(d.avg_ticket_eur))}</b><small>{String(d.items_per_basket)} items</small></div>
              </div>
              <div>
                <div className="sub" style={{ marginBottom: 6 }}>Favourite departments</div>
                <div style={{ display: 'flex', height: 12, borderRadius: 4, overflow: 'hidden', gap: 2 }} role="img" aria-label="Preferences by department">
                  {ZONE_ORDER.map(z => <span key={z} title={`${ZONE_LABEL[z]} ${pct(d.zone_weights[z])}`} style={{ width: `${(d.zone_weights[z] ?? 0) * 100}%`, background: ZONE_HEX[z] }} />)}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 10px', marginTop: 6, fontSize: 11.5, color: 'var(--soft)' }}>
                  {ZONE_ORDER.map(z => <span key={z}><i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: ZONE_HEX[z], marginRight: 4 }} />{ZONE_LABEL[z]} {pct(d.zone_weights[z])}</span>)}
                </div>
              </div>
              <table className="t">
                <tbody>
                  <tr><td>Buys on discount</td><td className="r num">{pct(Number(d.discount_sensitivity))}</td></tr>
                  <tr><td>Buys new in</td><td className="r num">{pct(Number(d.novelty_attraction))}</td></tr>
                  <tr><td>Items to try on</td><td className="r num">{pct(Number(d.tryon_share))}</td></tr>
                  <tr><td>Uses a fitting room <small style={{ color: 'var(--muted)' }}>(assumption)</small></td><td className="r num">{pct(a.fitting_prob)}</td></tr>
                  <tr><td>Queue patience <small style={{ color: 'var(--muted)' }}>(assumption)</small></td><td className="r num">{Math.round(a.queue_patience_s / 60)} min</td></tr>
                </tbody>
              </table>
            </section>
          )
        })}
      </div>
      <Card title="How to read the profiles" sub="What comes from the data and what is an assumption">
        <p className="sub" style={{ color: 'var(--soft)' }}>Favourite departments, receipt value, discounts, new-in items and items to try on are computed from each profile's real purchases. Fitting-room use, queue patience and walking speed are model assumptions, scaled by age and appetite for new-in items: they should be calibrated with store data (e.g. people counters, fitting-room times).</p>
      </Card>
    </>
  )
}
