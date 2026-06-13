// 3D intersection maneuver viewer for the map modal.
// Consumes the /api/corner contract and renders the reconstructed corner, the
// vehicle's swept path + conflict zones, an animated truck, driver instructions,
// and the nearest Cyvl 360° panorama as an immersive backdrop.
//
// Exposed as window.openCorner3D(corner) / window.closeCorner3D() so the classic
// app.js script can drive it. Local meter frame: +x east, +y north, z up.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const COLOR = { road: 0x2b2f36, lane: 0xffd23f, curb: 0xc8ccd2, swept: 0x3b82f6, conflict: 0xef4444, truck: 0xf04e23 };
const Z = { road: 0.0, lane: 0.05, swept: 0.12, conflict: 0.2 };

let renderer, scene, camera, controls, truckGroup, content, animId;
let POSES = [], STEPS = [], clock = 0, lastTs = 0, playing = true;

const $ = id => document.getElementById(id);

function shape(THREE_, pts) {
  const s = new THREE.Shape();
  s.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) s.lineTo(pts[i][0], pts[i][1]);
  s.closePath();
  return s;
}
function fill(pts, color, z, opacity) {
  const m = new THREE.Mesh(
    new THREE.ShapeGeometry(shape(THREE, pts)),
    new THREE.MeshBasicMaterial({ color, transparent: opacity < 1, opacity, side: THREE.DoubleSide, depthWrite: opacity >= 1 })
  );
  m.position.z = z;
  return m;
}
function polyline(pts, color, z) {
  const g = new THREE.BufferGeometry().setFromPoints(pts.map(([x, y]) => new THREE.Vector3(x, y, z)));
  return new THREE.Line(g, new THREE.LineBasicMaterial({ color }));
}
function curbWall(line, color, h = 0.35, t = 0.5) {
  const out = [];
  const mat = new THREE.MeshLambertMaterial({ color });
  for (let i = 0; i < line.length - 1; i++) {
    const [x1, y1] = line[i], [x2, y2] = line[i + 1];
    const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
    if (len < 0.4) continue;
    const box = new THREE.Mesh(new THREE.BoxGeometry(len, t, h), mat);
    box.position.set((x1 + x2) / 2, (y1 + y2) / 2, h / 2);
    box.rotation.z = Math.atan2(dy, dx);
    out.push(box);
  }
  return out;
}

function bounds(corner) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  (corner.road_polygons || []).flat().forEach(([x, y]) => {
    minx = Math.min(minx, x); miny = Math.min(miny, y);
    maxx = Math.max(maxx, x); maxy = Math.max(maxy, y);
  });
  if (!isFinite(minx)) { minx = miny = -30; maxx = maxy = 30; }
  return { cx: (minx + maxx) / 2, cy: (miny + maxy) / 2, size: Math.max(maxx - minx, maxy - miny, 20) };
}

function addPanorama(corner) {
  const pano = corner.panorama;
  if (!pano || !pano.image_url) return;
  const loader = new THREE.TextureLoader();
  loader.setCrossOrigin('anonymous');
  loader.load(pano.image_url, tex => {
    // Native three.js y-up equirectangular sphere viewed from the inside.
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(300, 60, 40),
      new THREE.MeshBasicMaterial({ map: tex, side: THREE.BackSide })
    );
    sphere.position.y = 6;                                  // raise eye-height a touch
    sphere.rotation.y = -(pano.bearing || 0) * Math.PI / 180;  // yaw to align photo north (approx)
    scene.add(sphere);                                      // added to scene (y-up), NOT content
    scene.background = null;
  }, undefined, () => console.warn('panorama texture blocked — keeping plain backdrop'));
}

function buildScene(corner) {
  // Build everything in the data's local z-up frame inside `content`, then tip
  // `content` into three's native y-up world so the panorama sphere is upright.
  content = new THREE.Group();

  (corner.road_polygons || []).forEach(p => content.add(fill(p, COLOR.road, Z.road, 0.96)));
  (corner.curb_lines || []).forEach(l => curbWall(l, COLOR.curb).forEach(m => content.add(m)));
  (corner.lane_edges || []).forEach(l => content.add(polyline(l, COLOR.lane, Z.lane)));
  if (corner.swept_path?.length) content.add(fill(corner.swept_path, COLOR.swept, Z.swept, 0.4));
  (corner.conflict_zones || []).forEach(z => content.add(fill(z, COLOR.conflict, Z.conflict, 0.65)));

  // truck
  const d = corner.vehicle_dims_m || { length: 12, width: 2.5, height: 3.5 };
  truckGroup = new THREE.Group();
  const geo = new THREE.BoxGeometry(d.length, d.width, d.height);
  const body = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ color: COLOR.truck, transparent: true, opacity: 0.9 }));
  body.position.z = d.height / 2;
  truckGroup.add(body);
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: 0x111111 }));
  edges.position.z = d.height / 2;
  truckGroup.add(edges);
  content.add(truckGroup);

  content.rotation.x = -Math.PI / 2;     // local +z (up) -> world +y (up)
  scene.add(content);

  addPanorama(corner);
}

function poseAt(t) {                       // t in [0,1] over the pose list
  const n = POSES.length;
  const f = Math.min(t, 1) * (n - 1);
  const i = Math.floor(f), j = Math.min(i + 1, n - 1), k = f - i;
  const a = POSES[i], b = POSES[j];
  return {
    x: a.x + (b.x - a.x) * k,
    y: a.y + (b.y - a.y) * k,
    h: a.heading_deg + (b.heading_deg - a.heading_deg) * k,
  };
}
function stepFor(t) {
  let s = STEPS[0];
  for (const st of STEPS) if (st.at <= t + 1e-6) s = st;
  return s;
}

const LOOP_MS = 7000, HOLD_MS = 1200;

function animate() {
  animId = requestAnimationFrame(animate);
  const now = performance.now();
  if (playing) clock += now - lastTs;
  lastTs = now;
  const cycle = LOOP_MS + HOLD_MS;
  const m = clock % cycle;
  const t = Math.min(m / LOOP_MS, 1);
  const p = poseAt(t);
  if (truckGroup) { truckGroup.position.set(p.x, p.y, 0); truckGroup.rotation.z = p.h * Math.PI / 180; }
  const st = stepFor(t);
  if (st) { $('c3d-step').textContent = st.instruction; }
  controls.update();
  renderer.render(scene, camera);
}

window.openCorner3D = function (corner) {
  $('corner-modal').classList.remove('hidden');
  const host = $('corner-canvas');
  host.innerHTML = '';

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0e0f13);
  scene.add(new THREE.AmbientLight(0xffffff, 0.85));
  const sun = new THREE.DirectionalLight(0xffffff, 0.8); sun.position.set(30, -20, 60); scene.add(sun);

  const w = host.clientWidth, h = host.clientHeight;
  camera = new THREE.PerspectiveCamera(50, w / h, 0.5, 5000);   // default up = +Y

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(w, h);
  host.appendChild(renderer.domElement);

  buildScene(corner);

  // World is y-up; local (x,y) ground maps to world (x, 0, -y).
  const b = bounds(corner);
  camera.position.set(b.cx, b.size * 1.25, -b.cy + b.size * 1.25);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(b.cx, 0, -b.cy);
  controls.update();

  // header / panel
  $('c3d-title').textContent = corner.intersection_id?.slice(0, 10) || 'Corner';
  $('c3d-sub').textContent = `${corner.vehicle} · ${corner.panorama ? `360° photo ${corner.panorama.dist_m}m away` : 'no photo'}`;
  const v = $('c3d-verdict'); v.textContent = corner.verdict; v.className = `verdict ${corner.verdict}`;

  POSES = corner.poses || [];
  STEPS = corner.steps || [];
  clock = 0; lastTs = performance.now(); playing = true;
  $('c3d-play').textContent = '⏸ Pause';
  cancelAnimationFrame(animId);
  animate();
};

window.closeCorner3D = function () {
  cancelAnimationFrame(animId);
  $('corner-modal').classList.add('hidden');
  if (renderer) { renderer.dispose(); renderer.domElement?.remove(); }
};

window.toggleCorner3D = function () {
  playing = !playing;
  $('c3d-play').textContent = playing ? '⏸ Pause' : '▶ Play';
  lastTs = performance.now();
};

window.addEventListener('resize', () => {
  if (!renderer || $('corner-modal').classList.contains('hidden')) return;
  const host = $('corner-canvas');
  camera.aspect = host.clientWidth / host.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(host.clientWidth, host.clientHeight);
});
