import { useState } from 'react'
import { useApp } from '../store'
import { Toggle } from '../ui'
import Customers from './Customers'
import Market from './Market'

const VIEWS = ['Sales and seasons', 'Customer profiles'] as const

export default function Data() {
  const { real, personas } = useApp()
  const [view, setView] = useState<(typeof VIEWS)[number]>('Sales and seasons')
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Data · what happened</h1>
          <p>Everything the system knows comes from here: two years of real H&amp;M receipts (store channel, {real ? `${real.overview.start} → ${real.overview.end}` : '…'}) and the customer profiles derived from those purchases. These numbers do not depend on the simulation: they are the ground the twin and the LLM reason on.</p>
        </div>
        <Toggle options={VIEWS} value={view} onChange={setView} />
      </div>
      {view === 'Sales and seasons' ? <Market r={real} embedded /> : (
        <>
          <p className="intro-note">Real customers are grouped into profiles by clustering their purchases. In the twin every visitor has one of these profiles: it decides which areas they visit, whether they use a fitting room and how long they wait in a queue.</p>
          <Customers data={personas} embedded />
        </>
      )}
    </>
  )
}
