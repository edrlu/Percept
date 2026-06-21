"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

type Hemisphere = { positions: number[]; indices: number[]; curvature?: number[]; families?: number[]; weights?: number[] };
type Surface = { mesh: string; verticesPerHemisphere: number; hemispheres: { left: Hemisphere; right: Hemisphere } };

// "Hot" activation colormap stops (low → high), the Meta/fMRI heat look:
// deep red → red → orange → yellow → near-white.
const HOT_STOPS = ["#4a0c02", "#c21e03", "#ff7a0f", "#ffce2e", "#fff4d6"];

export function CorticalBrain({ familyLevels, intensity }: { familyLevels?: number[]; intensity: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "fallback">("loading");
  const [meshName, setMeshName] = useState("fsaverage");
  const targetLevels = useRef<number[]>([0, 0, 0, 0]);
  const latestIntensity = useRef(intensity);
  // Set by the WebGL mount; the reset button calls it to animate the camera and
  // orientation back to their defaults.
  const resetView = useRef<() => void>(() => {});

  useEffect(() => {
    latestIntensity.current = intensity;
    if (familyLevels && familyLevels.length >= 4) {
      targetLevels.current = familyLevels.map((v) => Math.max(0, Math.min(1, v / 100)));
    }
  }, [familyLevels, intensity]);

  useEffect(() => {
    let disposed = false;
    let animation = 0;
    let cleanup = () => {};

    async function mount() {
      const canvas = canvasRef.current;
      if (!canvas) return;
      let surface: Surface | null = null;
      while (!disposed && !surface) {
        try {
          // Bypass the HTTP cache: the surface payload (mesh resolution, family
          // masks) can change between runs, and a stale cache would drop the
          // activation regions.
          const response = await fetch("/api/surface", { cache: "no-store" });
          if (!response.ok) throw new Error("surface unavailable");
          surface = await response.json();
        } catch {
          if (disposed) return;
          // The worker can still be downloading the surface or starting after
          // the frontend. Keep the preview label visible, then recover into
          // the real WebGL surface without requiring a page refresh.
          setState("fallback");
          await new Promise<void>((resolve) => window.setTimeout(resolve, 3_000));
        }
      }
      if (disposed) return;
      if (!surface) return;
      setMeshName(surface.mesh);

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0x000000, 0);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      const scene = new THREE.Scene();
      scene.fog = new THREE.Fog(0x08080a, 3.8, 7.8);
      const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
      camera.position.set(0, 0, 4.5);
      camera.lookAt(0, 0, 0);
      const brain = new THREE.Group();
      // FreeSurfer coordinates are millimetres; normalize them to scene units.
      brain.scale.setScalar(0.0105);
      scene.add(brain);
      // Inner group carries a fixed forward tilt so the brain stands upright
      // (FreeSurfer +z is superior, which would otherwise face the camera);
      // the outer group holds the user-controlled viewing angle.
      const oriented = new THREE.Group();
      oriented.rotation.x = -1.5;
      brain.add(oriented);
      scene.add(new THREE.HemisphereLight(0xfaf2df, 0x10130d, 1.35));
      const key = new THREE.DirectionalLight(0xfff1d8, 2.1); key.position.set(-3, 4, 5); scene.add(key);
      const fill = new THREE.DirectionalLight(0xbfcad8, 0.7); fill.position.set(3, -1.5, -2); scene.add(fill);

      // Shared activation uniforms — one update per frame drives both hemispheres.
      const hotStops = HOT_STOPS.map((hex) => { const c = new THREE.Color(hex); return new THREE.Vector3(c.r, c.g, c.b); });
      const uniforms = {
        uLevels: { value: new THREE.Vector4(0, 0, 0, 0) },
        uTime: { value: 0 },
        uHot: { value: hotStops },
      };

      // Clean near-white cortex: bright gyri, soft grey sulci so the folds stay
      // readable and the coloured activation overlays pop on top.
      const gyrus = new THREE.Color("#eeece8");
      const sulcus = new THREE.Color("#aeaaa2");
      const base = new THREE.Color();

      const meshes: { mesh: THREE.Mesh }[] = [];
      const makeHemisphere = (data: Hemisphere) => {
        const geometry = new THREE.BufferGeometry();
        const positions = new Float32Array(data.positions);
        const count = positions.length / 3;
        geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
        geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(data.indices), 1));
        geometry.computeVertexNormals();

        // Resting two-tone colour is baked once into the vertex colours.
        const curvature = data.curvature ?? new Array(count).fill(0.5);
        const colorData = new Float32Array(count * 3);
        for (let i = 0; i < count; i += 1) {
          base.copy(gyrus).lerp(sulcus, curvature[i] ?? 0.5);
          colorData[i * 3] = base.r; colorData[i * 3 + 1] = base.g; colorData[i * 3 + 2] = base.b;
        }
        geometry.setAttribute("color", new THREE.BufferAttribute(colorData, 3));

        // Per-vertex cognitive family (-1 = none) selects which stimulus drives
        // this vertex; the weight is the smooth 1→0 falloff toward the edge.
        const families = data.families ?? new Array(count).fill(-1);
        const weights = data.weights ?? new Array(count).fill(0);
        geometry.setAttribute("aFamily", new THREE.BufferAttribute(Float32Array.from(families), 1));
        geometry.setAttribute("aWeight", new THREE.BufferAttribute(Float32Array.from(weights), 1));

        const material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.74, metalness: 0.0, side: THREE.DoubleSide });
        material.onBeforeCompile = (shader) => {
          shader.uniforms.uLevels = uniforms.uLevels;
          shader.uniforms.uTime = uniforms.uTime;
          shader.uniforms.uHot = uniforms.uHot;
          shader.vertexShader = shader.vertexShader
            .replace("#include <common>", "#include <common>\nattribute float aFamily;\nattribute float aWeight;\nvarying float vFamily;\nvarying float vWeight;")
            .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFamily = aFamily;\nvWeight = aWeight;");
          shader.fragmentShader = shader.fragmentShader
            .replace("#include <common>", "#include <common>\nuniform vec4 uLevels;\nuniform float uTime;\nuniform vec3 uHot[5];\nvarying float vFamily;\nvarying float vWeight;\nvec3 hotMap(float t){\n  t = clamp(t, 0.0, 1.0);\n  float s = t * 4.0;\n  if (s < 1.0) return mix(uHot[0], uHot[1], s);\n  if (s < 2.0) return mix(uHot[1], uHot[2], s - 1.0);\n  if (s < 3.0) return mix(uHot[2], uHot[3], s - 2.0);\n  return mix(uHot[3], uHot[4], s - 3.0);\n}")
            .replace(
              "#include <emissivemap_fragment>",
              `#include <emissivemap_fragment>
{
  float level = 0.0;
  if (vFamily > 2.5) level = uLevels.w;
  else if (vFamily > 1.5) level = uLevels.z;
  else if (vFamily > 0.5) level = uLevels.y;
  else if (vFamily > -0.5) level = uLevels.x;
  // Gentle live shimmer so active cortex reads as "firing", not a static decal.
  float pulse = 0.86 + 0.14 * sin(uTime * 3.0 + vFamily * 1.7);
  float a = clamp(level * pulse, 0.0, 1.0) * smoothstep(0.0, 0.85, vWeight);
  vec3 heat = hotMap(a);
  float vis = smoothstep(0.04, 0.4, a);
  diffuseColor.rgb = mix(diffuseColor.rgb, heat, vis);
  totalEmissiveRadiance += heat * smoothstep(0.35, 1.0, a) * 1.5;
}`
            );
        };
        const mesh = new THREE.Mesh(geometry, material);
        oriented.add(mesh);
        meshes.push({ mesh });
      };
      makeHemisphere(surface.hemispheres.left);
      makeHemisphere(surface.hemispheres.right);

      // Recenter the whole brain on its true centroid so it sits in the middle
      // of the frame and rotates about its own centre.
      const box = new THREE.Box3();
      meshes.forEach(({ mesh }) => { mesh.geometry.computeBoundingBox(); if (mesh.geometry.boundingBox) box.union(mesh.geometry.boundingBox); });
      const center = new THREE.Vector3();
      box.getCenter(center);
      meshes.forEach(({ mesh }) => mesh.geometry.translate(-center.x, -center.y, -center.z));

      // Open the default view posteriorly: turn the outer group halfway around.
      // (The fixed forward tilt on `oriented` keeps the brain standing upright.)
      brain.rotation.y = Math.PI;

      // Camera-distance zoom: the camera dollies along its view axis between a
      // close and far stop, eased each frame toward `targetDist`.
      const DEFAULT_DIST = 4.5;
      const MIN_DIST = 2.8;
      const MAX_DIST = 6.5;
      let targetDist = DEFAULT_DIST;
      // Default orientation + a one-shot eased "reset" tumble back to it.
      const defaultQuat = brain.quaternion.clone();
      const fromQuat = new THREE.Quaternion();
      let resetT = 1; // >= 1 means no reset animation in flight

      let dragging = false;
      let lastX = 0; let lastY = 0;
      // Screen-relative trackball. Yaw is taken about the screen's vertical axis
      // and pitch about its horizontal axis, and each delta is *pre*-multiplied
      // onto the brain's quaternion so it rotates about the world (camera) axes
      // rather than the brain's own. That is what keeps up/down consistent no
      // matter which way the brain is currently facing -- applying pitch to a
      // local axis inverted it once the brain had been turned roughly 180.
      const screenYaw = new THREE.Vector3(0, 1, 0);
      const screenPitch = new THREE.Vector3(1, 0, 0);
      const dq = new THREE.Quaternion();
      const down = (event: PointerEvent) => { resetT = 1; dragging = true; lastX = event.clientX; lastY = event.clientY; canvas.setPointerCapture(event.pointerId); };
      const move = (event: PointerEvent) => {
        if (!dragging) return;
        dq.setFromAxisAngle(screenYaw, (event.clientX - lastX) * .008);
        brain.quaternion.premultiply(dq);
        dq.setFromAxisAngle(screenPitch, (event.clientY - lastY) * .008);
        brain.quaternion.premultiply(dq);
        lastX = event.clientX; lastY = event.clientY;
      };
      const up = () => { dragging = false; };
      // Scroll / pinch-zoom: multiplicative so each notch feels even at any
      // distance. deltaY < 0 (scroll up) zooms in; preventDefault stops the
      // page from scrolling under the canvas.
      const wheel = (event: WheelEvent) => { event.preventDefault(); targetDist = THREE.MathUtils.clamp(targetDist * (1 + event.deltaY * .0012), MIN_DIST, MAX_DIST); };
      canvas.addEventListener("pointerdown", down); canvas.addEventListener("pointermove", move); canvas.addEventListener("pointerup", up); canvas.addEventListener("pointerleave", up); canvas.addEventListener("wheel", wheel, { passive: false });

      // Animate orientation + zoom back to the opening view.
      resetView.current = () => { dragging = false; fromQuat.copy(brain.quaternion); resetT = 0; targetDist = DEFAULT_DIST; };

      const resize = () => { const rect = canvas.getBoundingClientRect(); renderer.setSize(rect.width, rect.height, false); camera.aspect = rect.width / rect.height; camera.updateProjectionMatrix(); };
      const observer = new ResizeObserver(resize); observer.observe(canvas); resize();

      const clock = new THREE.Clock();
      const levels = uniforms.uLevels.value;
      const render = () => {
        if (disposed) return;
        const dt = clock.getDelta();
        uniforms.uTime.value += dt;
        // Ease the family levels toward their targets so activations flash in
        // and fade smoothly as the timeline scrubs.
        const t = targetLevels.current;
        const k = 1 - Math.pow(0.0008, dt); // frame-rate independent smoothing
        levels.x += (t[0] - levels.x) * k;
        levels.y += (t[1] - levels.y) * k;
        levels.z += (t[2] - levels.z) * k;
        levels.w += (t[3] - levels.w) * k;
        // Ease the camera dolly toward the current zoom target.
        camera.position.z += (targetDist - camera.position.z) * (1 - Math.pow(0.0025, dt));
        // Run the reset tumble if one was requested (ease-out cubic over ~0.45s).
        if (resetT < 1) {
          resetT = Math.min(1, resetT + dt / 0.45);
          brain.quaternion.slerpQuaternions(fromQuat, defaultQuat, 1 - Math.pow(1 - resetT, 3));
        }
        // Orientation otherwise persists on brain.quaternion, set by the drag handler.
        renderer.render(scene, camera);
        animation = requestAnimationFrame(render);
      };
      animation = requestAnimationFrame(render);
      setState("ready");
      cleanup = () => { observer.disconnect(); canvas.removeEventListener("pointerdown", down); canvas.removeEventListener("pointermove", move); canvas.removeEventListener("pointerup", up); canvas.removeEventListener("pointerleave", up); canvas.removeEventListener("wheel", wheel); meshes.forEach(({ mesh }) => { mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); }); renderer.dispose(); };
    }
    mount();
    return () => { disposed = true; cancelAnimationFrame(animation); cleanup(); };
  }, []);

  return <div className="cortical-wrap">
    <canvas ref={canvasRef} className="cortical-canvas" aria-label="Interactive fsaverage cortical surface; drag to rotate, scroll to zoom" />
    <div className={`surface-status ${state}`}>{state === "loading" ? "LOADING FSAVERAGE SURFACE" : state === "fallback" ? "SURFACE OFFLINE · PREVIEW MODE" : `${meshName.toUpperCase()} PIAL · WEBGL SURFACE`}</div>
    <button type="button" className="surface-reset" onClick={() => resetView.current()} aria-label="Reset view to default orientation and zoom">RESET VIEW</button>
    <div className="surface-instruction">DRAG TO ROTATE · SCROLL TO ZOOM</div>
    <div className="activity-legend">
      <div className="legend-scale"><span>Low</span><span>High</span></div>
      <div className="legend-bar" />
      <div className="legend-title">Activity</div>
    </div>
  </div>;
}
