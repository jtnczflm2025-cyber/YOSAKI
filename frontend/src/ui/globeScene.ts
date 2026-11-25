import * as THREE from 'three'
import ThreeGlobe from 'three-globe'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

export async function createGlobe(host: HTMLElement, signal: AbortSignal, onInteract: () => void, onFailure: () => void) {
  const response = await fetch('/assets/land-points.json', { signal })
  if (!response.ok) throw new Error('Globe map unavailable')
  const points: [number, number][] = await response.json()
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError')
  // Keep the last rendered frame visible when the on-demand loop is paused.
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, preserveDrawingBuffer: true, powerPreference: 'low-power' })
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75))
  renderer.setClearColor(0x000000, 0)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(34, 1, .1, 1800)
  camera.position.set(330 * Math.sin(110 * Math.PI / 180), 145, 330 * Math.cos(110 * Math.PI / 180))
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableZoom = false; controls.enablePan = false
  controls.enableDamping = true; controls.dampingFactor = .065
  controls.autoRotateSpeed = .33
  controls.minPolarAngle = .35; controls.maxPolarAngle = Math.PI - .35
  controls.saveState()
  controls.addEventListener('start', onInteract)
  const globe = new ThreeGlobe({ animateIn: false })
    .showAtmosphere(true).atmosphereColor('#e5edf6').atmosphereAltitude(.045)
    .showGraticules(true)
  globe.globeMaterial(new THREE.MeshPhongMaterial({ color: '#566170', shininess: 24, specular: '#202831' }))
  // All layers are static; the Vue host owns the only animation loop.
  globe.pauseAnimation()
  scene.add(globe)
  scene.add(new THREE.AmbientLight(0xcbd6e3, 2.2))
  const rim = new THREE.Mesh(new THREE.SphereGeometry(101.2, 80, 64), new THREE.ShaderMaterial({
    vertexShader: 'varying vec3 n; varying vec3 v; void main(){ vec4 p=modelViewMatrix*vec4(position,1.0); n=normalize(normalMatrix*normal); v=normalize(-p.xyz); gl_Position=projectionMatrix*p; }',
    fragmentShader: 'varying vec3 n; varying vec3 v; void main(){float a=pow(1.0-max(dot(normalize(n),normalize(v)),0.0),2.0); gl_FragColor=vec4(0.90,0.94,1.0,a*0.65);}',
    transparent: true, depthWrite: false,
  }))
  scene.add(rim)
  const light = new THREE.DirectionalLight(0xffffff, 2.5)
  light.position.set(-150, 240, 300); scene.add(light)
  const vertices: number[] = []
  for (const [lat, lng] of points) {
    const p = globe.getCoords(lat, lng, .004)
    vertices.push(p.x, p.y, p.z)
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
  const material = new THREE.PointsMaterial({ color: '#eef4fc', size: .58, sizeAttenuation: true, transparent: true, opacity: .86 })
  scene.add(new THREE.Points(geometry, material))
  // Decorative orbits do not represent traffic, users, servers, or memory locations.
  for (let i = 0; i < 3; i++) {
    const path = Array.from({ length: 181 }, (_, j) => {
      const a = j / 180 * Math.PI * 2
      return new THREE.Vector3(Math.cos(a) * (120 + i * 7), 0, Math.sin(a) * (120 + i * 7))
    })
    const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(path), new THREE.LineBasicMaterial({ color: '#f2f6fc', transparent: true, opacity: .26 }))
    ring.rotation.set(.3 + i * .6, i * .8, .2 + i * .4)
    scene.add(ring)
    for (let j = 0; j < 4; j++) {
      const dot = new THREE.Mesh(new THREE.SphereGeometry(.65, 8, 8), new THREE.MeshBasicMaterial({ color: '#f3f8ff' }))
      dot.position.copy(path[j * 41]); ring.add(dot)
    }
  }
  const lost = (event: Event) => { event.preventDefault(); onFailure() }
  renderer.domElement.addEventListener('webglcontextlost', lost)
  renderer.domElement.setAttribute('aria-hidden', 'true')
  renderer.domElement.style.touchAction = 'pan-y'
  host.appendChild(renderer.domElement)
  function resize() {
    const width = host.clientWidth, height = host.clientHeight
    if (!width || !height) return
    camera.aspect = width / height; camera.updateProjectionMatrix()
    renderer.setSize(width, height, false)
  }
  resize()
  return {
    controls,
    resize,
    render(delta: number, auto: boolean, reduced: boolean) {
      controls.autoRotate = auto; controls.enableDamping = !reduced
      controls.update(delta); renderer.render(scene, camera)
    },
    dispose() {
      controls.removeEventListener('start', onInteract); controls.dispose()
      renderer.domElement.removeEventListener('webglcontextlost', lost)
      scene.traverse(object => {
        const mesh = object as THREE.Mesh
        mesh.geometry?.dispose()
        if (Array.isArray(mesh.material)) mesh.material.forEach(item => item.dispose())
        else mesh.material?.dispose()
      })
      globe._destructor?.()
      renderer.dispose(); renderer.domElement.remove()
    },
  }
}
