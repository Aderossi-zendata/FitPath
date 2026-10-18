import { useEffect, useState } from 'react'
import { api, type BehaviorInfo, type LayoutResponse } from '../api'
import { Card } from '../ui'

const ZONE_LABEL: Record<string, string> = { new_in: 'New in', ladies: 'Ladies', divided: 'Divided', men: 'Men', kids: 'Kids', denim: 'Denim', basics: 'Basics', accessories: 'Accessories' }
const CLS_LABEL: Record<string, string> = { sight_line: 'in sight from the entrance', window: 'window display', near_tills: 'by the tills', side_fitting: 'by the fitting rooms',
  side: 'side', center_back: 'centre, towards the back', side_back: 'side, at the back', back_wall: 'back wall' }
const num = (x: number) => x.toLocaleString('en-GB', { maximumFractionDigits: 3 })

/** The store plan and every behavioural assumption of the twin, with ranges and sources. */
export default function StoreModel() {
  const [lay, setLay] = useState<LayoutResponse | null>(null)
  const [beh, setBeh] = useState<BehaviorInfo | null>(null)
  useEffect(() => { api.layout().then(setLay).catch(() => {}); api.behavior().then(setBeh).catch(() => {}) }, [])
  const L = lay?.layout
  return (
    <>
      {L && (
        <Card title={`The store: ${L.area_m2.toLocaleString('en-GB')} m² on one floor`} sub="H&M-style floor plan: decompression zone at the entrance, departments in blocks, fitting rooms next to Ladies, tills by the exit. Space = block area relative to the area justified by the department's sales (1 = right size).">
          <table className="t">
            <thead><tr><th>Department</th><th>Block</th><th>Position</th><th className="r">Space</th></tr></thead>
            <tbody>
              {Object.entries(L.assign).map(([z, b]) => (
                <tr key={z}><td>{ZONE_LABEL[z] ?? z}</td><td>{L.blocks[b]?.name}</td><td>{CLS_LABEL[L.blocks[b]?.cls] ?? L.blocks[b]?.cls}</td>
                  <td className="r num">{lay?.space_fit?.[z]?.toFixed(2) ?? '–'}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="note">{L.cabins.length} fitting rooms · {L.tills.length} tills ({L.tills_open} open by default) · {lay?.cameras.length} ceiling cameras.</p>
        </Card>
      )}
      {beh && (
        <Card title="Model assumptions and sources" sub={beh.note}>
          <table className="t">
            <thead><tr><th>Effect</th><th>What it does in the twin</th><th>Range</th><th>Sources</th></tr></thead>
            <tbody>
              {beh.params.map(p => (
                <tr key={p.key}>
                  <td><b>{p.name}</b></td>
                  <td>{p.what}</td>
                  <td className="num">{Array.isArray(p.range) ? `${num(p.range[0])} – ${num(p.range[1])}` : Object.entries(p.range).map(([c, r]) => <div key={c}>{CLS_LABEL[c] ?? c}: {num(r[0])}–{num(r[1])}</div>)}<div className="sub">{p.unit}</div></td>
                  <td>{p.sources.map(s => <div key={s}><a href={beh.sources[s]?.url} target="_blank" rel="noreferrer">{beh.sources[s]?.label}</a></div>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="sub" style={{ marginTop: 10 }}>Not modelled</div>
          <ul>{beh.not_modelled.map(n => <li key={n.what}>{n.url ? <a href={n.url} target="_blank" rel="noreferrer">{n.what}</a> : n.what}</li>)}</ul>
        </Card>
      )}
    </>
  )
}
