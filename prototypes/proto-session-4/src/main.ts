import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { createMonolith, MONO } from "./monolith";
import { createSparks } from "./particles";

const app = document.getElementById("app") as HTMLDivElement;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x000000, 1);
app.appendChild(renderer.domElement);

const scene = new THREE.Scene();

const camera = new THREE.PerspectiveCamera(
  36,
  window.innerWidth / window.innerHeight,
  0.1,
  200,
);
camera.position.set(0, 0.5, 16);
camera.lookAt(0, 0, 0);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 3;
controls.maxDistance = 80;
controls.target.set(0, 0, 0);

const monolith = createMonolith();
scene.add(monolith.group);

const sparks = createSparks(renderer, MONO.radius);
scene.add(sparks.points);

window.addEventListener("resize", () => {
  renderer.setSize(window.innerWidth, window.innerHeight);
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
});

const clock = new THREE.Clock();

function animate() {
  const dt = Math.min(clock.getDelta(), 0.05);
  const t = clock.getElapsedTime();

  monolith.update(t, camera);
  sparks.tick(dt, t);
  controls.update();

  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

animate();
