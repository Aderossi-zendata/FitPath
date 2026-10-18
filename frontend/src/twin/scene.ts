// Three.js store twin: fixtures, fitting rooms, tills and ceiling cameras built from /api/layout,
// people placed by the replay, and a ceiling-camera picture-in-picture rendered with a scissor pass.
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { Block, Fixture, LayoutResponse, StoreLayout } from '../api'

export type StrategyLike = {
  feature?: { garment: string }; clearance?: { pct: number }; multibuy?: { zone: string; type: string }
  discount?: { zone: string; pct: number }[]; cross_merch?: { from: string; to: string }[]; bundle?: { a: string; b: string }[]
  extra_till?: { from: number; to: number }; fitting_attendant?: { from: number; to: number }
}
const GARMENT_LABEL: Record<string, string> = { Knitwear: 'Knitwear', Outdoor: 'Jackets and coats', 'Trousers Denim': 'Jeans', Trousers: 'Trousers', 'Dresses Ladies': 'Dresses', Blouses: 'Blouses', Shirts: 'Shirts', Skirts: 'Skirts', 'Jersey Basic': 'Basic jersey', 'Jersey Fancy': 'Fancy jersey', 'Socks and Tights': 'Socks and tights', 'Under-, Nightwear': 'Underwear', Swimwear: 'Swimwear', Shorts: 'Shorts', Shoes: 'Shoes', Accessories: 'Accessories', Dressed: 'Smart wear' }
export type Cam = { id: string; name: string; x: number; z: number; r: number; kind: 'grid' | 'fit' | 'door' }
export type Pose = { vid: number; track: number; x: number; z: number; heading: number; persona: number }
export type PipState = { cam: Cam; follow: { x: number; z: number; label: string; gap: boolean } | null }

const ZONES: Record<string, { name: string; color: number }> = {
  new_in: { name: 'New in', color: 0xf2b84b }, ladies: { name: 'Ladies', color: 0xe58fa8 }, divided: { name: 'Divided', color: 0xb592e8 },
  men: { name: 'Men', color: 0x7fb2e6 }, kids: { name: 'Kids', color: 0x7fd1a6 }, denim: { name: 'Denim', color: 0x4a6fa5 },
  basics: { name: 'Basics', color: 0xc9d1d8 }, accessories: { name: 'Accessories', color: 0xb98a52 },
}
// same order as HEX in ui.tsx, so a persona keeps its colour across pages
export const PERSONA_HEX = [0x3987e5, 0xd95926, 0x199e70, 0xc98500, 0xd55181, 0x008300, 0x9085e9]
const STORE_DEFAULT = { x0: -25, z0: -20, w: 50, d: 40, door: [-4, 4] as [number, number] }
const ease = (k: number) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2)
type G = { x: number; y: number; z: number; ry: number; c: number; h: number }

export function camsFrom(res: LayoutResponse): Cam[] {
  return res.cameras.map((c, i) => ({
    id: 'CAM ' + String(i + 1).padStart(2, '0'), name: c.name, x: c.x, z: c.z, r: c.r,
    kind: /fitting/i.test(c.name) ? 'fit' : /^door/i.test(c.name) ? 'door' : 'grid',
  }))
}

/** Camera that sees (x, z); keeps the current one while still inside its footprint (hysteresis). */
export function coverCam(cams: Cam[], x: number, z: number, cur: Cam | null): Cam | null {
  if (cur && Math.hypot(x - cur.x, z - cur.z) < cur.r * 0.95) return cur
  let best: Cam | null = null, bd = 1e9
  for (const c of cams) {
    const d = Math.hypot(x - c.x, z - c.z)
    if (d < c.r * 0.95 && d < bd) { bd = d; best = c }
  }
  return best
}

const std = (c: number, o: THREE.MeshStandardMaterialParameters = {}) =>
  new THREE.MeshStandardMaterial({ color: c, roughness: 0.85, metalness: 0.05, ...o })
const css = (c: number) => '#' + c.toString(16).padStart(6, '0')

function disposeTree(o: THREE.Object3D) {
  o.traverse(x => {
    const m = x as THREE.Mesh
    m.geometry?.dispose()
    const mat = m.material as THREE.Material | THREE.Material[] | undefined
    if (Array.isArray(mat)) mat.forEach(q => q.dispose()); else mat?.dispose()
  })
}

type Person = { g: THREE.Group; label: HTMLDivElement; persona: number; seen: number }
type Label = { el: HTMLDivElement; pos: THREE.Vector3 }

export class StoreTwin {
  renderer: THREE.WebGLRenderer
  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(42, 1, 0.1, 400)
  controls: OrbitControls
  pipCam = new THREE.PerspectiveCamera(88, 1.5, 0.1, 30)
  cams: Cam[]
  onPick: (vid: number | null) => void = () => {}
  onFrame: (dt: number) => void = () => {}
  pip: PipState | null = null
  following: number | null = null

  private stage: HTMLElement
  private labelsEl: HTMLElement
  private pipEl: HTMLElement
  private pctx: CanvasRenderingContext2D
  private pipCanvas: HTMLCanvasElement
  private fog = new THREE.Fog(0x12181f, 90, 180)
  private people = new Map<number, Person>()
  private peopleGroup = new THREE.Group()
  private camGroup = new THREE.Group()
  private overlay: THREE.Object3D[] = []
  private staticLabels: Label[] = []
  private camLabels: (Label & { cam: Cam })[] = []
  private activeCone: THREE.Mesh
  private ring: THREE.Mesh
  private trail: THREE.Line
  private cabinLamps: THREE.MeshBasicMaterial[] = []
  private cabinsOpen: number
  private mannequins: [number, number][]
  private bodyGeo = new THREE.CylinderGeometry(0.17, 0.21, 0.8, 12)
  private headGeo = new THREE.SphereGeometry(0.15, 14, 10)
  private headMat = std(0xe0c2a2)
  private bodyMats = PERSONA_HEX.map(c => std(c))
  private raf = 0
  private last = performance.now()
  private ro: ResizeObserver
  private fly: { p0: THREE.Vector3; p1: THREE.Vector3; t0: THREE.Vector3; t1: THREE.Vector3; s: number } | null = null
  private down: { x: number; y: number } | null = null
  private ray = new THREE.Raycaster()
  private disposed = false
  // one group per department: a strategy that moves departments rebuilds them in their new block
  private layout: StoreLayout
  private depts = new Map<string, THREE.Group>()
  private zoneLabels = new Map<string, Label>()
  private anims: { s: number; ms: number; fn: (e: number) => void; done?: () => void }[] = []
  private badges: Label[] = []
  private ghosts: THREE.Object3D[] = []
  private seed = 7

  constructor(els: { stage: HTMLElement; canvas: HTMLCanvasElement; labels: HTMLElement; pip: HTMLElement; pipCanvas: HTMLCanvasElement },
    res: LayoutResponse, layout: StoreLayout) {
    this.stage = els.stage; this.labelsEl = els.labels; this.pipEl = els.pip; this.pipCanvas = els.pipCanvas
    this.pctx = els.pipCanvas.getContext('2d')!
    this.cams = camsFrom(res)
    this.cabinsOpen = layout.cabins_open
    this.layout = layout
    this.mannequins = layout.mannequins
    const r = this.renderer = new THREE.WebGLRenderer({ canvas: els.canvas, antialias: true })
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap
    this.scene.background = new THREE.Color(0x12181f)
    this.scene.fog = this.fog
    this.controls = new OrbitControls(this.camera, els.canvas)
    Object.assign(this.controls, { enableDamping: true, maxPolarAngle: Math.PI * 0.47, minDistance: 4, maxDistance: 130 })
    this.pipCam.up.set(0, 0, -1)
    this.scene.add(this.peopleGroup, this.camGroup)
    this.camGroup.visible = false

    this.buildShell(layout.store ?? res.store ?? STORE_DEFAULT)
    this.buildFixtures(layout)
    this.buildFitting(layout)
    this.buildTills(layout)
    this.buildCameras()

    this.activeCone = new THREE.Mesh(new THREE.ConeGeometry(1, 3.4, 40, 1, true),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.14, side: THREE.DoubleSide, depthWrite: false }))
    this.activeCone.visible = false
    this.ring = new THREE.Mesh(new THREE.RingGeometry(0.34, 0.46, 40), new THREE.MeshBasicMaterial({ color: 0xe58fa8, side: THREE.DoubleSide }))
    this.ring.rotation.x = -Math.PI / 2; this.ring.visible = false
    const tg = new THREE.BufferGeometry(); tg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3 * 4000), 3)); tg.setDrawRange(0, 0)
    this.trail = new THREE.Line(tg, new THREE.LineBasicMaterial({ color: 0xe58fa8, transparent: true, opacity: 0.7 }))
    this.trail.visible = false
    this.scene.add(this.activeCone, this.ring, this.trail)
    this.overlay.push(this.activeCone, this.ring, this.trail, this.camGroup)

    this.view('top', false)
    els.canvas.addEventListener('pointerdown', this.pdown)
    els.canvas.addEventListener('pointerup', this.pup)
    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(this.stage)
    this.resize()
    this.raf = requestAnimationFrame(this.loop)
  }

  // ---------- static scene ----------
  private box(w: number, h: number, d: number, c: number, x: number, y: number, z: number, o: THREE.MeshStandardMaterialParameters = {}, parent: THREE.Object3D = this.scene) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), std(c, o)); m.position.set(x, y, z)
    m.castShadow = m.receiveShadow = true; parent.add(m); return m
  }

  private label(html: string, pos: THREE.Vector3, cls: string): Label {
    const el = document.createElement('div'); el.className = 'lbl ' + cls; el.innerHTML = html
    this.labelsEl.appendChild(el); const l = { el, pos }; this.staticLabels.push(l); return l
  }

  private dashed(pts: THREE.Vector3[], color: number, opacity = 0.7) {
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineDashedMaterial({ color, dashSize: 0.5, gapSize: 0.35, transparent: true, opacity }))
    l.computeLineDistances(); this.scene.add(l); return l
  }

  private rect(x0: number, z0: number, x1: number, z1: number, color: number, y = 0.03) {
    return this.dashed([new THREE.Vector3(x0, y, z0), new THREE.Vector3(x1, y, z0), new THREE.Vector3(x1, y, z1), new THREE.Vector3(x0, y, z1), new THREE.Vector3(x0, y, z0)], color)
  }

  private rnd() { return (this.seed = (this.seed * 16807) % 2147483647) / 2147483647 }

  private shade(hex: number) {
    const tmp = new THREE.Color().setHex(hex), hsl = { h: 0, s: 0, l: 0 }; tmp.getHSL(hsl)
    return new THREE.Color().setHSL(hsl.h + (this.rnd() - 0.5) * 0.08, Math.min(1, hsl.s * (0.6 + this.rnd() * 0.5)), Math.min(0.85, hsl.l * (0.7 + this.rnd() * 0.5)))
  }

  private buildShell(st: { x0: number; z0: number; w: number; d: number; door: [number, number] }) {
    const s = this.scene, cx = st.x0 + st.w / 2, cz = st.z0 + st.d / 2, x1 = st.x0 + st.w, z1 = st.z0 + st.d, L = this.layout
    s.add(new THREE.HemisphereLight(0xe8eef4, 0x1c2229, 2.2))
    const sun = new THREE.DirectionalLight(0xffffff, 2.2)
    sun.position.set(cx - 18, 40, cz + 22); sun.target.position.set(cx, 0, cz); sun.castShadow = true; sun.shadow.mapSize.set(4096, 4096)
    Object.assign(sun.shadow.camera, { left: -st.w / 2 - 4, right: st.w / 2 + 4, top: st.d / 2 + 6, bottom: -st.d / 2 - 6, near: 1, far: 120 }); sun.shadow.bias = -0.0006
    s.add(sun, sun.target)
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(st.w + 2, st.d + 2), std(0x1e252c)); floor.rotation.x = -Math.PI / 2; floor.position.set(cx, 0, cz); floor.receiveShadow = true
    const wood = new THREE.Mesh(new THREE.PlaneGeometry(st.w, st.d), std(0x2a2622, { roughness: 0.9 })); wood.rotation.x = -Math.PI / 2; wood.position.set(cx, 0.002, cz); wood.receiveShadow = true
    s.add(floor, wood)
    const W = 0x323b44, [d0, d1] = st.door
    this.box(st.w + 0.4, 1.2, 0.3, W, cx, 0.6, st.z0 - 0.15)
    this.box(0.3, 1.2, st.d + 0.3, W, st.x0 - 0.15, 0.6, cz); this.box(0.3, 1.2, st.d + 0.3, W, x1 + 0.15, 0.6, cz)
    // shop windows on both sides of the door
    for (const [a, b] of [[st.x0, d0], [d1, x1]]) {
      this.box(b - a, 0.3, 0.3, W, (a + b) / 2, 0.15, z1 + 0.15)
      const g = new THREE.Mesh(new THREE.PlaneGeometry(b - a, 2.6), new THREE.MeshStandardMaterial({ color: 0x9fc3dc, transparent: true, opacity: 0.14, metalness: 0.2, roughness: 0.05, side: THREE.DoubleSide, depthWrite: false }))
      g.position.set((a + b) / 2, 1.6, z1 + 0.15); s.add(g)
    }
    const door = new THREE.Mesh(new THREE.PlaneGeometry(d1 - d0, 1.4), new THREE.MeshBasicMaterial({ color: 0xe58fa8, transparent: true, opacity: 0.2 }))
    door.rotation.x = -Math.PI / 2; door.position.set((d0 + d1) / 2, 0.01, z1 - 0.4); s.add(door)
    this.label('Entrance', new THREE.Vector3((d0 + d1) / 2, 0.6, z1 + 1.2), 'zone ghost')
    // decompression zone: left empty on purpose, shoppers adjust to the store before looking at products
    const dz = L.decompression
    if (dz) {
      this.overlay.push(this.rect(dz.x0, dz.z0, dz.x1, z1 - 0.5, 0x5b6670))
      this.label('Decompression zone', new THREE.Vector3((dz.x0 + dz.x1) / 2, 0.3, dz.z0 + 1.2), 'zone ghost')
    }
    for (const [x, z] of L.columns ?? []) this.box(0.6, 3.6, 0.6, 0xc8cfd5, x, 1.8, z)
    for (const d of L.decor ?? []) if (d.t === 'podium') this.box(d.w, 0.35, d.d, 0xe6e1d8, d.x, 0.175, d.z)
  }

  /** Floor tint, fixtures and products of one department, in world coordinates. */
  private buildDept(k: string, list: Fixture[], block?: Block) {
    const g = new THREE.Group(), color = ZONES[k]?.color ?? 0x8c99a4, hang: G[] = [], stack: G[] = []
    if (block) {
      const tint = new THREE.Mesh(new THREE.PlaneGeometry(block.x1 - block.x0, block.z1 - block.z0),
        new THREE.MeshStandardMaterial({ color, transparent: true, opacity: 0.12, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2 }))
      tint.rotation.x = -Math.PI / 2; tint.position.set((block.x0 + block.x1) / 2, 0.006, (block.z0 + block.z1) / 2); tint.receiveShadow = true
      g.add(tint)
    }
    for (const f of list) {
      if (f.t === 'rack') {
        const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 1.5), std(0x8a949c, { metalness: 0.6, roughness: 0.4 })); pole.position.set(f.x, 0.75, f.z)
        const base = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.05, 24), std(0x6b747c)); base.position.set(f.x, 0.03, f.z)
        const ring = new THREE.Mesh(new THREE.TorusGeometry(0.75, 0.025, 8, 40), std(0xa8b1b8, { metalness: 0.7, roughness: 0.3 })); ring.rotation.x = Math.PI / 2; ring.position.set(f.x, 1.45, f.z)
        g.add(pole, base, ring)
        for (let a = 0; a < Math.PI * 2; a += Math.PI / 11) hang.push({ x: f.x + Math.cos(a) * 0.75, y: 1.05, z: f.z + Math.sin(a) * 0.75, ry: -a, c: color, h: 1 })
      } else if (f.t === 'table') {
        const w = f.w ?? 2, d = f.d ?? 1.2
        this.box(w, 0.8, d, 0x5a4a3c, f.x, 0.4, f.z, {}, g)
        for (let i = 0; i < Math.floor(w / 0.45); i++) for (let j = 0; j < Math.floor(d / 0.5); j++) {
          const h = 0.12 + this.rnd() * 0.18
          stack.push({ x: f.x - w / 2 + 0.25 + i * 0.45, y: 0.8 + h / 2, z: f.z - d / 2 + 0.27 + j * 0.5, ry: 0, c: color, h })
        }
      } else {
        // wall shelves (thin, one side) and gondolas (thicker, both sides, lower so the floor stays readable)
        const w = f.w ?? 0.5, d = f.d ?? 4, along = d > w, len = along ? d : w, thick = along ? w : d, double = thick > 0.7
        const H = double ? 1.5 : 2.2, levels = double ? 2 : 3
        this.box(w, H, d, 0x3a434c, f.x, H / 2, f.z, {}, g)
        const sides = double ? [-1, 1] : [along ? (f.x < 0 ? 1 : -1) : (f.z < 0 ? 1 : -1)]
        const n = Math.floor(len / 0.42)
        for (const sd of sides) for (let i = 0; i < n; i++) for (let lv = 0; lv < levels; lv++) {
          const off = -len / 2 + 0.25 + i * 0.42, out = sd * (thick / 2 + 0.07)
          stack.push({ x: along ? f.x + out : f.x + off, y: 0.45 + lv * 0.62, z: along ? f.z + off : f.z + out, ry: along ? Math.PI / 2 : 0, c: color, h: 0.3 })
        }
      }
    }
    const mk = (geo: THREE.BufferGeometry, items: G[], scaleY: boolean) => {
      if (!items.length) return
      const m = new THREE.InstancedMesh(geo, std(0xffffff, { roughness: 0.9 }), items.length), o = new THREE.Object3D()
      items.forEach((q, i) => {
        o.position.set(q.x, q.y, q.z); o.rotation.set(0, q.ry, 0); o.scale.set(1, scaleY ? q.h : 1, 1); o.updateMatrix()
        m.setMatrixAt(i, o.matrix); m.setColorAt(i, this.shade(q.c))
      })
      m.castShadow = true; g.add(m)
    }
    mk(new THREE.BoxGeometry(0.05, 0.75, 0.42), hang, false)
    mk(new THREE.BoxGeometry(0.38, 1, 0.32), stack, true)
    return g
  }

  private blockCentre(id: string | undefined, y = 2.4) {
    const b = id ? this.layout.blocks?.[id] : undefined
    return b ? new THREE.Vector3((b.x0 + b.x1) / 2, y, (b.z0 + b.z1) / 2) : new THREE.Vector3(0, y, 0)
  }

  private buildFixtures(layout: StoreLayout) {
    for (const [k, list] of Object.entries(layout.fixtures)) {
      const block = layout.blocks?.[layout.assign?.[k]]
      const g = this.buildDept(k, list, block); this.scene.add(g); this.depts.set(k, g)
      const pos = block ? this.blockCentre(layout.assign[k]) : new THREE.Vector3(list[0]?.x ?? 0, 2.4, list[0]?.z ?? 0)
      const color = ZONES[k]?.color ?? 0x8c99a4
      this.zoneLabels.set(k, this.label(`<i style="background:${css(color)}"></i>${ZONES[k]?.name ?? k}`, pos, 'zone'))
    }
    // mannequins in the windows and on the podium (the detector ignores them)
    const podium = (layout.decor ?? []).filter(d => d.t === 'podium')
    for (const [x, z] of layout.mannequins) {
      const g = new THREE.Group(), mat = std(0xe9e4dc)
      const b = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.2, 0.85, 12), mat); b.position.y = 0.62
      const h = new THREE.Mesh(new THREE.SphereGeometry(0.14, 14, 10), mat); h.position.y = 1.2
      const st = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.25), std(0x777777)); st.position.y = 0.12
      const up = podium.some(d => Math.abs(x - d.x) <= d.w / 2 && Math.abs(z - d.z) <= d.d / 2) ? 0.35 : 0
      g.add(b, h, st); g.position.set(x, up, z); g.traverse(o => { o.castShadow = true }); this.scene.add(g)
    }
  }

  private buildFitting(layout: StoreLayout) {
    const f = layout.fitting, xs = layout.cabins, z0 = (layout.store ?? STORE_DEFAULT).z0, depth = f.door_z - z0, cz = (z0 + f.door_z) / 2, half = 0.8
    xs.forEach((x, i) => {
      const open = i < layout.cabins_open
      this.box(0.08, 2.1, depth, 0x48525c, x - half, 1.05, cz)
      const cur = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 1.9), std(open ? 0x7a3d50 : 0x3a3f45, { side: THREE.DoubleSide })); cur.position.set(x, 1.05, f.door_z); this.scene.add(cur)
      const lamp = new THREE.MeshBasicMaterial({ color: open ? 0x62d29c : 0x3a4650 }); this.cabinLamps.push(lamp)
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 10), lamp); s.position.set(x, 2.35, f.door_z + 0.05); this.scene.add(s)
    })
    const x0 = Math.min(...xs) - half, x1 = Math.max(...xs) + half
    this.box(0.08, 2.1, depth, 0x48525c, x1, 1.05, cz)
    const w = x1 - x0 + 0.2, cx = (x0 + x1) / 2
    const blind = new THREE.Mesh(new THREE.PlaneGeometry(w, depth), new THREE.MeshBasicMaterial({ color: 0xff6b5e, transparent: true, opacity: 0.1, depthWrite: false }))
    blind.rotation.x = -Math.PI / 2; blind.position.set(cx, 2.2, cz)
    const edge = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w, 2.2, depth)), new THREE.LineBasicMaterial({ color: 0xff6b5e, transparent: true, opacity: 0.55 }))
    edge.position.set(cx, 1.1, cz)
    this.scene.add(blind, edge); this.overlay.push(blind, edge)
    this.label(`Fitting rooms (${xs.length}) · not filmed`, new THREE.Vector3(cx, 2.7, cz), 'zone blind')
  }

  private buildTills(layout: StoreLayout) {
    const xs = layout.tills, z = layout.till_z, x0 = Math.min(...xs) - 1.3, x1 = Math.max(...xs) + 1.3, cx = (x0 + x1) / 2
    this.box(x1 - x0, 1, 0.8, 0x3f4a54, cx, 0.5, z); this.box(x1 - x0, 0.05, 0.85, 0x8d6e5a, cx, 1.02, z)
    this.box(x1 - x0, 2, 0.4, 0x2f373f, cx, 1, z + 1.6)   // back shelf with bags
    xs.forEach((x, i) => {
      const open = i < layout.tills_open
      this.box(0.45, 0.32, 0.05, open ? 0x62d29c : 0x3a4650, x, 1.25, z - 0.25, { emissive: open ? 0x62d29c : 0x000000, emissiveIntensity: 0.5 })
    })
    this.label(`<i style="background:#62d29c"></i>Tills (${layout.tills_open}/${xs.length} open)`, new THREE.Vector3(cx, 1.9, z), 'zone')
  }

  private buildCameras() {
    for (const c of this.cams) {
      const dome = new THREE.Mesh(new THREE.SphereGeometry(0.18, 14, 10, 0, Math.PI * 2, 0, Math.PI / 2), std(0xdfe7ee)); dome.rotation.x = Math.PI; dome.position.set(c.x, 3.4, c.z)
      const col = c.kind === 'door' ? 0x62d29c : c.kind === 'fit' ? 0xf2b84b : 0xe58fa8
      const cone = new THREE.Mesh(new THREE.ConeGeometry(c.r, 3.4, 32, 1, true),
        new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: c.kind !== 'grid' ? 0.13 : 0.05, side: THREE.DoubleSide, depthWrite: false }))
      cone.position.set(c.x, 1.7, c.z)
      const foot = new THREE.Mesh(new THREE.RingGeometry(c.r - 0.04, c.r, 48), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false }))
      foot.rotation.x = -Math.PI / 2; foot.position.set(c.x, 0.02, c.z)
      this.camGroup.add(dome, cone, foot)
      const el = document.createElement('div'); el.className = 'lbl camtag'; el.textContent = c.id.replace('CAM ', 'C'); el.title = c.name
      this.labelsEl.appendChild(el); this.camLabels.push({ el, pos: new THREE.Vector3(c.x, 3.6, c.z), cam: c })
    }
  }

  // ---------- dynamic ----------
  setCamsVisible(on: boolean) { this.camGroup.visible = on }

  /** Place everyone visible at this instant; figures are pooled by visit id. */
  setPeople(poses: Pose[], stamp: number) {
    for (const p of poses) {
      let P = this.people.get(p.vid)
      if (!P) {
        const g = new THREE.Group()
        const body = new THREE.Mesh(this.bodyGeo, this.bodyMats[p.persona % this.bodyMats.length]); body.position.y = 0.4
        const head = new THREE.Mesh(this.headGeo, this.headMat); head.position.y = 0.98
        for (const m of [body, head]) { m.castShadow = true; m.userData.vid = p.vid }
        g.add(body, head); this.peopleGroup.add(g)
        const label = document.createElement('div'); label.className = 'lbl tid'; this.labelsEl.appendChild(label)
        P = { g, label, persona: p.persona, seen: stamp }; this.people.set(p.vid, P)
      }
      P.seen = stamp
      P.g.position.set(p.x, 0, p.z); P.g.rotation.y = p.heading
      const txt = '#' + p.track
      if (P.label.textContent !== txt) P.label.textContent = txt
      P.label.classList.toggle('on', p.vid === this.following)
    }
    for (const [vid, P] of this.people) {
      if (P.seen === stamp) continue
      this.peopleGroup.remove(P.g); P.label.remove(); this.people.delete(vid)
    }
  }

  setCabinsBusy(n: number) {
    this.cabinLamps.forEach((m, i) => { if (i < this.cabinsOpen) m.color.setHex(i < n ? 0xff6b5e : 0x62d29c) })
  }

  /** Followed person: ring at the feet, trail so far, active camera cone. */
  setFollow(vid: number | null, at: { x: number; z: number } | null, trail: [number, number][], cam: Cam | null) {
    this.following = vid
    this.ring.visible = !!at
    if (at) {
      this.ring.position.set(at.x, 0.03, at.z)
      // when the user has zoomed in, drift the orbit centre towards the person without fighting zoom and rotation;
      // in the overview the store stays still and the ring and trail show where the person is
      const t = this.controls.target, dx = (at.x - t.x) * 0.06, dz = (at.z - t.z) * 0.06
      const st = this.layout.store ?? STORE_DEFAULT, zoomed = this.camera.position.distanceTo(t) < Math.max(st.w, st.d) * 0.8
      if (!this.fly && zoomed) { t.x += dx; t.z += dz; this.camera.position.x += dx; this.camera.position.z += dz }
    }
    const arr = this.trail.geometry.attributes.position.array as Float32Array, n = Math.min(trail.length, 4000)
    for (let k = 0; k < n; k++) { arr[k * 3] = trail[k][0]; arr[k * 3 + 1] = 0.04; arr[k * 3 + 2] = trail[k][1] }
    this.trail.geometry.setDrawRange(0, n); this.trail.geometry.attributes.position.needsUpdate = true
    this.trail.visible = vid != null && n > 1
    this.activeCone.visible = !!cam && vid != null
    if (cam) { this.activeCone.scale.set(cam.r, 1, cam.r); this.activeCone.position.set(cam.x, 1.7, cam.z) }
    this.camLabels.forEach(l => l.el.classList.toggle('on', vid != null && l.cam === cam))
  }

  view(kind: 'top' | 'wide', animate = true) {
    const st = this.layout.store ?? STORE_DEFAULT, s = Math.max(st.w, st.d * 1.3) / 31, cx = st.x0 + st.w / 2, cz = st.z0 + st.d / 2
    const p1 = kind === 'top' ? new THREE.Vector3(cx, 30 * s, cz + 13 * s) : new THREE.Vector3(cx - 6 * s, 17 * s, cz + 24 * s), t1 = new THREE.Vector3(cx, 0, cz + 0.5)
    if (!animate) { this.camera.position.copy(p1); this.controls.target.copy(t1); this.controls.update(); return }
    this.fly = { p0: this.camera.position.clone(), p1, t0: this.controls.target.clone(), t1, s: performance.now() }
  }

  private pdown = (e: PointerEvent) => { this.down = { x: e.clientX, y: e.clientY } }
  private pup = (e: PointerEvent) => {
    if (!this.down || Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 5) return
    const r = this.renderer.domElement.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top
    this.ray.setFromCamera(new THREE.Vector2((mx / r.width) * 2 - 1, -(my / r.height) * 2 + 1), this.camera)
    const hit = this.ray.intersectObjects(this.peopleGroup.children, true)[0]
    if (hit) { this.onPick(hit.object.userData.vid as number); return }
    // figures are small from above: fall back to the nearest one on screen
    const v = new THREE.Vector3(); let best: number | null = null, bd = 22
    for (const [vid, P] of this.people) {
      v.set(P.g.position.x, 0.6, P.g.position.z).project(this.camera)
      const d = Math.hypot((v.x * 0.5 + 0.5) * r.width - mx, (-v.y * 0.5 + 0.5) * r.height - my)
      if (d < bd) { bd = d; best = vid }
    }
    this.onPick(best)
  }

  private resize() {
    const w = this.stage.clientWidth, h = this.stage.clientHeight
    if (!w || !h) return
    this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix()
    const pw = Math.round(Math.min(w < 700 ? 190 : w < 1400 ? 320 : 560, w * 0.42)), ph = Math.round(pw / 1.5)
    this.pipEl.style.width = pw + 'px'; this.pipEl.style.height = ph + 'px'
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.pipCanvas.width = pw * dpr; this.pipCanvas.height = ph * dpr; this.pctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    this.pipCam.aspect = pw / ph
  }

  private loop = (now: number) => {
    if (this.disposed) return
    const dt = Math.min(0.1, (now - this.last) / 1000); this.last = now
    this.onFrame(dt)
    this.anims = this.anims.filter(a => {
      if (now < a.s) return true
      const k = Math.min(1, (now - a.s) / a.ms)
      a.fn(ease(k))
      if (k >= 1) a.done?.()
      return k < 1
    })
    if (this.fly) {
      const k = Math.min(1, (now - this.fly.s) / 900), e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2
      this.camera.position.lerpVectors(this.fly.p0, this.fly.p1, e); this.controls.target.lerpVectors(this.fly.t0, this.fly.t1, e)
      if (k >= 1) this.fly = null
    }
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    this.renderPip()
    this.placeLabels()
    this.raf = requestAnimationFrame(this.loop)
  }

  /** Move departments to the blocks of `layout`: each moved one sinks in its old block, a dashed arc shows
   *  where it goes, and it is rebuilt (fixtures for the new block's shape) rising in the new one. */
  animateLayout(layout: StoreLayout, ms = 2400) {
    this.ghosts.forEach(g => { this.scene.remove(g); this.overlay = this.overlay.filter(o => o !== g) }); this.ghosts = []
    const cur = this.layout.assign, next = layout.assign
    if (!cur || !next || !layout.blocks) return
    const t0 = performance.now() + 350
    let k = 0
    for (const z of Object.keys(next)) {
      if (cur[z] === next[z] || !layout.fixtures[z]) continue
      const old = this.depts.get(z), from = this.blockCentre(cur[z]), to = this.blockCentre(next[z])
      const ng = this.buildDept(z, layout.fixtures[z], layout.blocks[next[z]]); ng.scale.y = 0.001; this.scene.add(ng); this.depts.set(z, ng)
      const s = t0 + k++ * 300, color = ZONES[z]?.color ?? 0x8c99a4
      if (old) this.anims.push({ s, ms: ms * 0.45, fn: e => { old.scale.y = Math.max(0.001, 1 - e) }, done: () => { this.scene.remove(old); disposeTree(old) } })
      this.anims.push({ s: s + ms * 0.45, ms: ms * 0.55, fn: e => { ng.scale.y = Math.max(0.001, e) } })
      const lab = this.zoneLabels.get(z)
      if (lab) { const p0 = lab.pos.clone(); this.anims.push({ s, ms, fn: e => { lab.pos.lerpVectors(p0, to, e) } }) }
      const b = this.layout.blocks[cur[z]]
      const ghost = this.rect(b.x0, b.z0, b.x1, b.z1, 0x8c99a4)
      const mid = from.clone().lerp(to, 0.5); mid.y = 6 + from.distanceTo(to) * 0.12
      const arc = this.dashed(new THREE.QuadraticBezierCurve3(new THREE.Vector3(from.x, 0.3, from.z), mid, new THREE.Vector3(to.x, 0.3, to.z)).getPoints(40), color, 0.9)
      this.ghosts.push(ghost, arc); this.overlay.push(ghost, arc)
    }
    this.layout = { ...layout, cabins_open: this.layout.cabins_open, tills_open: this.layout.tills_open }
  }

  /** Show the strategy's promotions as signs over the store (window display, 3-for-2, discounts, extra till...). */
  setStrategy(strategy: StrategyLike | null) {
    this.badges.forEach(b => b.el.remove()); this.badges = []
    if (!strategy) return
    const L = this.layout, st = L.store ?? STORE_DEFAULT
    const add = (html: string, pos: THREE.Vector3, cls = '') => {
      const el = document.createElement('div'); el.className = 'lbl strat-badge ' + cls; el.innerHTML = html
      this.labelsEl.appendChild(el); this.badges.push({ el, pos })
    }
    const at = (zone: string, dy = 1.1) => this.blockCentre(L.assign?.[zone], 2.4 + dy)
    const tx = L.tills.length ? (Math.min(...L.tills) + Math.max(...L.tills)) / 2 : 0
    if (strategy.feature) add(`Window display · <b>${GARMENT_LABEL[strategy.feature.garment] ?? strategy.feature.garment}</b>`, new THREE.Vector3(st.door[0] - 3, 2.8, st.z0 + st.d - 0.8), 'hot')
    if (strategy.clearance) add(`End-of-season sale <b>−${strategy.clearance.pct}%</b>`, new THREE.Vector3(st.x0 + st.w / 2, 3.6, st.z0 + st.d / 2), 'hot')
    if (strategy.multibuy) add(`<b>${strategy.multibuy.type === '3x2' ? '3-for-2' : '2nd item −50%'}</b>`, at(strategy.multibuy.zone))
    for (const d of strategy.discount ?? []) add(`<b>−${d.pct}%</b>`, at(d.zone, 1.6))
    for (const c of strategy.cross_merch ?? []) add(`+ ${ZONES[c.to]?.name ?? c.to}`, at(c.from, .6))
    for (const b of strategy.bundle ?? []) add(`Bundle <b>${b.a} + ${b.b}</b>`, new THREE.Vector3(tx, 3.2, L.till_z - 3))
    if (strategy.extra_till) add(`Extra till <b>${strategy.extra_till.from}-${strategy.extra_till.to}</b>`, new THREE.Vector3(Math.max(...L.tills), 2.6, L.till_z))
    if (strategy.fitting_attendant) add(`Fitting-room attendant <b>${strategy.fitting_attendant.from}-${strategy.fitting_attendant.to}</b>`, new THREE.Vector3(L.fitting.queue.x, 3, L.fitting.queue.z))
  }

  private pipRect() {
    const sr = this.stage.getBoundingClientRect(), pr = this.pipEl.getBoundingClientRect()
    return { x: pr.left - sr.left, y: pr.top - sr.top, w: pr.width, h: pr.height }
  }

  private placeLabels() {
    const w = this.stage.clientWidth, h = this.stage.clientHeight, v = new THREE.Vector3()
    const pip = this.pip && this.pipEl.offsetParent ? this.pipRect() : null
    const place = (el: HTMLElement, pos: THREE.Vector3, show = true) => {
      v.copy(pos).project(this.camera)
      const sx = (v.x * 0.5 + 0.5) * w, sy = (-v.y * 0.5 + 0.5) * h
      let ok = show && v.z < 1
      if (ok && pip && sx > pip.x && sx < pip.x + pip.w && sy > pip.y && sy < pip.y + pip.h) ok = false
      el.style.display = ok ? '' : 'none'
      if (ok) el.style.transform = `translate(${sx}px, ${sy}px) translate(-50%, -100%)`
    }
    for (const l of this.staticLabels) place(l.el, l.pos)
    for (const l of this.badges) place(l.el, l.pos)
    for (const l of this.camLabels) place(l.el, l.pos, this.camGroup.visible || l.el.classList.contains('on'))
    const p = new THREE.Vector3()
    for (const P of this.people.values()) { p.set(P.g.position.x, 1.35, P.g.position.z); place(P.label, p) }
  }

  private boxFor(x: number, z: number, top: number): [number, number, number, number] | null {
    const p = new THREE.Vector3(); let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9
    for (const [dx, dy, dz] of [[-.25, 0, -.25], [.25, 0, .25], [-.25, top, .25], [.25, top, -.25], [-.25, 0, .25], [.25, top, .25], [.25, 0, -.25], [-.25, top, -.25]]) {
      p.set(x + dx, dy, z + dz).project(this.pipCam); if (p.z > 1) return null
      x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y)
    }
    return [x0, y0, x1, y1]
  }

  private renderPip() {
    if (!this.pip || !this.pipEl.offsetParent) return
    const { cam, follow } = this.pip, pc = this.pipCam
    pc.position.set(cam.x, 3.4, cam.z); pc.lookAt(cam.x, 0, cam.z)
    pc.fov = 2 * Math.atan((Math.max(2.4, cam.r) * 1.05 / 3.4) / pc.aspect) * 180 / Math.PI; pc.updateProjectionMatrix()
    const { x, y, w, h } = this.pipRect(), H = this.stage.clientHeight, r = this.renderer
    if (!w || !h) return
    const vis = this.overlay.map(o => o.visible)
    this.overlay.forEach(o => { o.visible = false }); this.scene.fog = null
    r.setScissorTest(true); r.setScissor(x, H - y - h, w, h); r.setViewport(x, H - y - h, w, h)
    r.render(this.scene, pc)
    r.setScissorTest(false); r.setViewport(0, 0, this.stage.clientWidth, H)
    this.scene.fog = this.fog; this.overlay.forEach((o, i) => { o.visible = vis[i] })
    // detections as the in-store edge model would output them
    const ctx = this.pctx
    ctx.clearRect(0, 0, w, h); ctx.lineWidth = 1.5; ctx.font = '600 10px "JetBrains Mono", monospace'
    const draw = (b: [number, number, number, number] | null, col: string, text: string, dashed: boolean) => {
      if (!b) return
      const X0 = (b[0] * 0.5 + 0.5) * w, X1 = (b[2] * 0.5 + 0.5) * w, Y0 = (-b[3] * 0.5 + 0.5) * h, Y1 = (-b[1] * 0.5 + 0.5) * h
      if (X1 < 0 || Y1 < 0 || X0 > w || Y0 > h) return
      ctx.setLineDash(dashed ? [4, 3] : []); ctx.strokeStyle = col; ctx.strokeRect(X0, Y0, X1 - X0, Y1 - Y0); ctx.setLineDash([])
      const tw = ctx.measureText(text).width + 6; ctx.fillStyle = 'rgba(8,11,14,.8)'; ctx.fillRect(X0, Y0 - 12, tw, 12)
      ctx.fillStyle = col; ctx.fillText(text, X0 + 3, Y0 - 3)
    }
    for (const [mx, mz] of this.mannequins) draw(this.boxFor(mx, mz, 1.35), '#8c99a4', 'mannequin · ignored', true)
    for (const [vid, P] of this.people) {
      if (follow && vid === this.following) continue
      draw(this.boxFor(P.g.position.x, P.g.position.z, 1.15), css(PERSONA_HEX[P.persona % 7]), P.label.textContent ?? '', false)
    }
    if (follow) draw(this.boxFor(follow.x, follow.z, 1.15), follow.gap ? '#f2b84b' : '#e58fa8', follow.label, follow.gap)
  }

  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.ro.disconnect()
    this.controls.dispose()
    this.renderer.domElement.removeEventListener('pointerdown', this.pdown)
    this.renderer.domElement.removeEventListener('pointerup', this.pup)
    this.scene.traverse(o => {
      const m = o as THREE.Mesh
      m.geometry?.dispose()
      const mat = m.material as THREE.Material | THREE.Material[] | undefined
      if (Array.isArray(mat)) mat.forEach(x => x.dispose()); else mat?.dispose()
    })
    this.renderer.dispose()
    this.labelsEl.replaceChildren()
  }
}
