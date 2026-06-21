"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";

type Hemisphere = { positions: number[]; indices: number[]; curvature?: number[]; families?: number[]; weights?: number[] };
type Surface = { mesh: string; verticesPerHemisphere: number; hemispheres: { left: Hemisphere; right: Hemisphere } };

// Inferno activation colormap stops (low → high): perceptually uniform, the
// only place colour lives on the brain. near-black → violet → magenta → orange
// → amber → pale yellow. Quiet cortex stays near the anatomical grey.
const HOT_STOPS = ["#000004", "#420A68", "#932667", "#DD513A", "#FCA50A", "#FCFFA4"];
const DEFAULT_GLOW = "#9AA6F2"; // periwinkle (ATTN) — neutral-cool fallback tint

export function CorticalBrain({ familyLevels, intensity, dominantColor }: { familyLevels?: number[]; intensity: number; dominantColor?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "fallback">("loading");
  const [meshName, setMeshName] = useState("fsaverage");
  const targetLevels = useRef<number[]>([0, 0, 0, 0]);
  const latestIntensity = useRef(intensity);
  // The dominant system's hue drives the ambient back-glow + the fresnel rim, so
  // the light spilling off the brain matches what is currently firing.
  const glowColor = useRef(dominantColor || DEFAULT_GLOW);
  // Set by the WebGL mount; the reset button calls it to animate the camera and
  // orientation back to their defaults.
  const resetView = useRef<() => void>(() => {});

  useEffect(() => {
    latestIntensity.current = intensity;
    if (familyLevels && familyLevels.length >= 4) {
      targetLevels.current = familyLevels.map((v) => Math.max(0, Math.min(1, v / 100)));
    }
    if (dominantColor) glowColor.current = dominantColor;
  }, [familyLevels, intensity, dominantColor]);

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

      // Respect reduced-motion: kill idle drift + the shimmer/flow animation
      // (the static glow stays — it is identity, not motion). Tracked live.
      const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
      let reduced = motionQuery.matches;

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setClearColor(0x000000, 0);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.05;
      const scene = new THREE.Scene();
      scene.fog = new THREE.Fog(0x060608, 3.8, 8.2);

      // Cinematic backdrop: a soft radial glow fading to deep space, rendered in
      // the canvas so the light feels like it emanates from behind the organ.
      const backdrop = (() => {
        const c = document.createElement("canvas"); c.width = 128; c.height = 128;
        const ctx = c.getContext("2d");
        if (ctx) {
          const g = ctx.createRadialGradient(64, 54, 6, 64, 70, 92);
          g.addColorStop(0, "#11131c");
          g.addColorStop(0.5, "#08080c");
          g.addColorStop(1, "#040405");
          ctx.fillStyle = g; ctx.fillRect(0, 0, 128, 128);
        }
        const tex = new THREE.CanvasTexture(c);
        tex.colorSpace = THREE.SRGBColorSpace;
        return tex;
      })();
      scene.background = backdrop;

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
      // Matte tissue lighting, dialled down from the old flat-lit look so the
      // emissive activation + fresnel rim read as the bright, blooming elements.
      scene.add(new THREE.HemisphereLight(0xfaf2df, 0x0a0c08, 0.9));
      const key = new THREE.DirectionalLight(0xfff1d8, 1.15); key.position.set(-3, 4, 5); scene.add(key);
      const fill = new THREE.DirectionalLight(0xbfcad8, 0.5); fill.position.set(3, -1.5, -2); scene.add(fill);
      // Coloured ambient spill from behind the brain, tinted by the dominant
      // system and pulsed by overall activation — drives the "energy" feel.
      const glowLight = new THREE.PointLight(0x9aa6f2, 0, 16, 1.7); glowLight.position.set(0, 0.4, -2.6); scene.add(glowLight);

      // Shared activation uniforms — one update per frame drives both hemispheres.
      const hotStops = HOT_STOPS.map((hex) => { const c = new THREE.Color(hex); return new THREE.Vector3(c.r, c.g, c.b); });
      const uniforms = {
        uLevels: { value: new THREE.Vector4(0, 0, 0, 0) },
        uTime: { value: 0 },
        uHot: { value: hotStops },
        uMotion: { value: reduced ? 0 : 1 },         // 1 = animate shimmer/flow, 0 = steady
        uRim: { value: 0.8 },                          // fresnel rim strength
        uRimColor: { value: new THREE.Color(DEFAULT_GLOW) },
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
          shader.uniforms.uMotion = uniforms.uMotion;
          shader.uniforms.uRim = uniforms.uRim;
          shader.uniforms.uRimColor = uniforms.uRimColor;
          shader.vertexShader = shader.vertexShader
            .replace("#include <common>", "#include <common>\nattribute float aFamily;\nattribute float aWeight;\nvarying float vFamily;\nvarying float vWeight;\nvarying vec3 vRimNormal;\nvarying vec3 vRimView;\nvarying vec3 vPosLocal;")
            .replace("#include <begin_vertex>", "#include <begin_vertex>\nvFamily = aFamily;\nvWeight = aWeight;\nvPosLocal = position;")
            .replace("#include <project_vertex>", "#include <project_vertex>\nvRimNormal = normalize(normalMatrix * objectNormal);\nvRimView = normalize(-mvPosition.xyz);");
          shader.fragmentShader = shader.fragmentShader
            .replace("#include <common>", "#include <common>\nuniform vec4 uLevels;\nuniform float uTime;\nuniform vec3 uHot[6];\nuniform float uMotion;\nuniform float uRim;\nuniform vec3 uRimColor;\nvarying float vFamily;\nvarying float vWeight;\nvarying vec3 vRimNormal;\nvarying vec3 vRimView;\nvarying vec3 vPosLocal;\nvec3 hotMap(float t){\n  t = clamp(t, 0.0, 1.0);\n  float s = t * 5.0;\n  if (s < 1.0) return mix(uHot[0], uHot[1], s);\n  if (s < 2.0) return mix(uHot[1], uHot[2], s - 1.0);\n  if (s < 3.0) return mix(uHot[2], uHot[3], s - 2.0);\n  if (s < 4.0) return mix(uHot[3], uHot[4], s - 3.0);\n  return mix(uHot[4], uHot[5], s - 4.0);\n}")
            .replace(
              "#include <emissivemap_fragment>",
              `#include <emissivemap_fragment>
{
  float level = 0.0;
  if (vFamily > 2.5) level = uLevels.w;
  else if (vFamily > 1.5) level = uLevels.z;
  else if (vFamily > 0.5) level = uLevels.y;
  else if (vFamily > -0.5) level = uLevels.x;
  // Gentle live shimmer + a travelling activation "flow" so active cortex reads
  // as firing energy moving across tissue, not a static decal. uMotion folds
  // both to steady under prefers-reduced-motion.
  float shimmer = mix(1.0, 0.86 + 0.14 * sin(uTime * 3.0 + vFamily * 1.7), uMotion);
  float flow = mix(1.0, 0.72 + 0.28 * sin(uTime * 1.7 - vPosLocal.y * 0.05 + vFamily * 0.9), uMotion);
  float a = clamp(level * shimmer, 0.0, 1.0) * smoothstep(0.0, 0.85, vWeight);
  vec3 heat = hotMap(a);
  float vis = smoothstep(0.04, 0.4, a);
  diffuseColor.rgb = mix(diffuseColor.rgb, heat, vis);
  totalEmissiveRadiance += heat * smoothstep(0.35, 1.0, a) * 1.7 * flow;
  // Fresnel rim: a glowing silhouette even where the cortex is quiet, tinted by
  // the dominant system so the organ's edge reads as alive.
  float fres = pow(1.0 - clamp(dot(normalize(vRimNormal), normalize(vRimView)), 0.0, 1.0), 2.25);
  totalEmissiveRadiance += uRimColor * fres * uRim;
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

      // HDR float target + MSAA so the emissive blooms cleanly and edges stay
      // crisp under the post pipeline.
      const bufferSize = renderer.getDrawingBufferSize(new THREE.Vector2());
      const renderTarget = new THREE.WebGLRenderTarget(bufferSize.x, bufferSize.y, { type: THREE.HalfFloatType, samples: 2 });
      const composer = new EffectComposer(renderer, renderTarget);
      composer.addPass(new RenderPass(scene, camera));
      // (resolution, strength, radius, threshold) — threshold sits above the lit
      // matte tissue so only the emissive activation + rim actually bleed light.
      const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.85, 0.5, 0.62);
      composer.addPass(bloom);
      composer.addPass(new OutputPass());

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
      // Idle auto-rotation resumes a short beat after the user lets go, so the
      // organ keeps breathing without fighting an active drag or reset.
      let idleHold = 0;
      // Screen-relative trackball. Yaw is taken about the screen's vertical axis
      // and pitch about its horizontal axis, and each delta is *pre*-multiplied
      // onto the brain's quaternion so it rotates about the world (camera) axes
      // rather than the brain's own. That is what keeps up/down consistent no
      // matter which way the brain is currently facing -- applying pitch to a
      // local axis inverted it once the brain had been turned roughly 180.
      const screenYaw = new THREE.Vector3(0, 1, 0);
      const screenPitch = new THREE.Vector3(1, 0, 0);
      const dq = new THREE.Quaternion();
      const idleQuat = new THREE.Quaternion();
      const down = (event: PointerEvent) => { resetT = 1; dragging = true; idleHold = 1.4; lastX = event.clientX; lastY = event.clientY; canvas.setPointerCapture(event.pointerId); };
      const move = (event: PointerEvent) => {
        if (!dragging) return;
        dq.setFromAxisAngle(screenYaw, (event.clientX - lastX) * .008);
        brain.quaternion.premultiply(dq);
        dq.setFromAxisAngle(screenPitch, (event.clientY - lastY) * .008);
        brain.quaternion.premultiply(dq);
        lastX = event.clientX; lastY = event.clientY;
      };
      const up = () => { dragging = false; idleHold = 1.4; };
      // Scroll / pinch-zoom: multiplicative so each notch feels even at any
      // distance. deltaY < 0 (scroll up) zooms in; preventDefault stops the
      // page from scrolling under the canvas.
      const wheel = (event: WheelEvent) => { event.preventDefault(); targetDist = THREE.MathUtils.clamp(targetDist * (1 + event.deltaY * .0012), MIN_DIST, MAX_DIST); };
      // The brain is the hero, so it must be operable without a pointer: the
      // canvas is focusable; arrows rotate, +/- zoom, 0/Home recentre.
      const onKey = (event: KeyboardEvent) => {
        const STEP = 0.2; let handled = true;
        if (event.key === "ArrowLeft") { dq.setFromAxisAngle(screenYaw, -STEP); brain.quaternion.premultiply(dq); }
        else if (event.key === "ArrowRight") { dq.setFromAxisAngle(screenYaw, STEP); brain.quaternion.premultiply(dq); }
        else if (event.key === "ArrowUp") { dq.setFromAxisAngle(screenPitch, -STEP); brain.quaternion.premultiply(dq); }
        else if (event.key === "ArrowDown") { dq.setFromAxisAngle(screenPitch, STEP); brain.quaternion.premultiply(dq); }
        else if (event.key === "+" || event.key === "=") { targetDist = THREE.MathUtils.clamp(targetDist * 0.9, MIN_DIST, MAX_DIST); }
        else if (event.key === "-" || event.key === "_") { targetDist = THREE.MathUtils.clamp(targetDist * 1.1, MIN_DIST, MAX_DIST); }
        else if (event.key === "0" || event.key === "Home") { resetView.current(); }
        else handled = false;
        if (handled) { event.preventDefault(); resetT = 1; idleHold = 1.4; }
      };
      canvas.addEventListener("pointerdown", down); canvas.addEventListener("pointermove", move); canvas.addEventListener("pointerup", up); canvas.addEventListener("pointerleave", up); canvas.addEventListener("wheel", wheel, { passive: false }); canvas.addEventListener("keydown", onKey);

      // Animate orientation + zoom back to the opening view.
      resetView.current = () => { dragging = false; fromQuat.copy(brain.quaternion); resetT = 0; targetDist = DEFAULT_DIST; };

      const onMotionChange = () => { reduced = motionQuery.matches; uniforms.uMotion.value = reduced ? 0 : 1; };
      motionQuery.addEventListener?.("change", onMotionChange);

      const resize = () => {
        const rect = canvas.getBoundingClientRect();
        renderer.setSize(rect.width, rect.height, false);
        composer.setSize(rect.width, rect.height);
        camera.aspect = rect.width / rect.height;
        camera.updateProjectionMatrix();
      };
      const observer = new ResizeObserver(resize); observer.observe(canvas); resize();

      const clock = new THREE.Clock();
      const levels = uniforms.uLevels.value;
      const tmpColor = new THREE.Color();
      const white = new THREE.Color(0xffffff);
      let lastGlow = ""; // only re-parse the dominant hue when it actually changes
      const render = () => {
        if (disposed) return;
        const dt = clock.getDelta();
        if (!reduced) uniforms.uTime.value += dt;
        // Ease the family levels toward their targets so activations flash in
        // and fade smoothly as the timeline scrubs.
        const t = targetLevels.current;
        const k = 1 - Math.pow(0.0008, dt); // frame-rate independent smoothing
        levels.x += (t[0] - levels.x) * k;
        levels.y += (t[1] - levels.y) * k;
        levels.z += (t[2] - levels.z) * k;
        levels.w += (t[3] - levels.w) * k;
        // Tint the back-glow + rim by the dominant system, and pulse the spill
        // light with overall activation so the stage brightens when cortex fires.
        if (glowColor.current !== lastGlow) {
          tmpColor.set(glowColor.current);
          glowLight.color.copy(tmpColor);
          uniforms.uRimColor.value.copy(tmpColor).lerp(white, 0.45);
          lastGlow = glowColor.current;
        }
        const act = Math.max(0, Math.min(1, latestIntensity.current / 100));
        glowLight.intensity = 0.35 + 2.4 * act;
        // Ease the camera dolly toward the current zoom target.
        camera.position.z += (targetDist - camera.position.z) * (1 - Math.pow(0.0025, dt));
        // Run the reset tumble if one was requested (ease-out cubic over ~0.45s).
        if (resetT < 1) {
          resetT = Math.min(1, resetT + dt / 0.45);
          brain.quaternion.slerpQuaternions(fromQuat, defaultQuat, 1 - Math.pow(1 - resetT, 3));
        } else if (!reduced && !dragging) {
          // Gentle idle drift once any interaction settles.
          idleHold = Math.max(0, idleHold - dt);
          if (idleHold === 0) {
            idleQuat.setFromAxisAngle(screenYaw, dt * 0.085);
            brain.quaternion.premultiply(idleQuat);
          }
        }
        // Orientation otherwise persists on brain.quaternion, set by the drag handler.
        composer.render();
        animation = requestAnimationFrame(render);
      };
      animation = requestAnimationFrame(render);
      setState("ready");
      cleanup = () => {
        observer.disconnect();
        motionQuery.removeEventListener?.("change", onMotionChange);
        canvas.removeEventListener("pointerdown", down); canvas.removeEventListener("pointermove", move); canvas.removeEventListener("pointerup", up); canvas.removeEventListener("pointerleave", up); canvas.removeEventListener("wheel", wheel); canvas.removeEventListener("keydown", onKey);
        meshes.forEach(({ mesh }) => { mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); });
        backdrop.dispose();
        // composer.dispose() frees only its own render targets — dispose every
        // pass (bloom, OutputPass, …) so their shader programs/geometry don't leak.
        composer.passes.forEach((p) => (p as { dispose?: () => void }).dispose?.());
        renderTarget.dispose(); composer.dispose(); renderer.dispose();
      };
    }
    mount();
    return () => { disposed = true; cancelAnimationFrame(animation); cleanup(); };
  }, []);

  return <div className="cortical-wrap">
    <canvas ref={canvasRef} className="cortical-canvas" tabIndex={0} role="img" aria-label="Interactive cortical surface. Drag or use arrow keys to rotate; scroll or +/- to zoom; press 0 to reset the view." />
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
