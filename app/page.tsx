"use client";

import { ChangeEvent, DragEvent, type PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { CorticalBrain } from "./components/CorticalBrain";
import { Studio } from "./components/Studio";
import { COLOR_SCHEMES, DEFAULT_COLOR_SCHEME, type ColorSchemeId } from "./color-schemes";

type Region = { name: string; short: string; color: string; values: number[]; score: number };
type Analysis = {
  duration: number;
  frames: number;
  regions: Region[];
  global: number[];
  peak: { time: number; label: string; value: number };
  source: "demo" | "model";
  cognitiveSeries?: Record<string, number[]>;
};

// Cortical surface proxy regions, in the worker's family order. These are
// descriptive surface summaries, not direct measurements of cognitive states
// or subcortical structures.
const FAMILY_DETAILS = [
  { key: "auditory_engagement", name: "Auditory / speech-music", short: "AUD", reliability: "high", anatomy: "Auditory cortex (A1, belt areas, STG/STS)", impact: "Predicted response across auditory cortex — voiceover, music, sound design. TRIBE predicts this territory near its noise ceiling, so it is the most trustworthy signal.", blurb: "Tracks predicted engagement of auditory/speech regions as the clip plays." },
  { key: "language_message", name: "Language / message", short: "LANG", reliability: "high", anatomy: "Language network (IFG 44/45/47l, IFS/IFJ)", impact: "Predicted response across language regions — how the spoken/written message is processed. Among TRIBE's most reliably predicted areas.", blurb: "Tracks predicted engagement of language regions carrying the message." },
  { key: "attention_salience", name: "Attention + salience", short: "ATTN", reliability: "medium", anatomy: "Attention & salience (IPS/FEF, insula/dACC, TPJ)", impact: "Predicted response across attention and salience networks — the hook (capture) and sustained attention (hold). Well predicted by the multimodal model.", blurb: "Tracks predicted engagement of attention/salience regions over time." },
  { key: "visual_motion", name: "Visual / motion", short: "VIS", reliability: "medium", anatomy: "Higher visual & motion areas (MT/MST, V4t, FST, LO, V3CD)", impact: "Predicted response across motion-sensitive and higher visual areas — visual dynamics and cuts. Primary visual (V1) is excluded because TRIBE predicts it less reliably under the multimodal model.", blurb: "Tracks predicted engagement of motion/higher-visual regions." },
] as const;
const FAMILY_KEYS = FAMILY_DETAILS.map((f) => f.key);
const FILMSTRIP_FRAME_COUNT = 24;

type LogStatus = "active" | "done" | "error" | "note";
type LogEntry = { id: string; ts: number; title: string; detail?: string; status: LogStatus; bar?: boolean; href?: string; linkLabel?: string };
type RegenStatus = "extracting" | "awaiting_generation" | "generating" | "merging" | "done" | "error";
type RegenJobState = { status: RegenStatus; jobId?: string; startFrame?: string; endFrame?: string; downloadUrl?: string; logUrl?: string; logTail?: string; error?: string };
type Cut = { start: number; end: number; frameId?: string; preparing?: boolean; frameRequested?: boolean; frameError?: string };
const REGEN_LABEL: Record<RegenStatus, string> = {
  extracting: "Extracting frames…",
  awaiting_generation: "Generating via MCP agent + Pika…",
  generating: "Agent is generating clip…",
  merging: "Merging clip…",
  done: "Regenerated",
  error: "Failed",
};

// Video model used for clip regeneration, switchable in the settings menu.
type RegenProvider = "seedance" | "kling";
const REGEN_PROVIDERS: { id: RegenProvider; name: string; description: string }[] = [
  { id: "seedance", name: "Seedance 2.0", description: "Cinematic · 720p fast tier · start→end frames" },
  { id: "kling", name: "Kling", description: "Fast & cheap · pro/strict · start→end frames" },
];
const DEFAULT_REGEN_PROVIDER: RegenProvider = "seedance";

// Which MCP agent the worker spawns to drive Pika (both run the same prompt).
type RegenAgent = "claude" | "codex";
const REGEN_AGENTS: { id: RegenAgent; name: string; description: string }[] = [
  { id: "codex", name: "Codex MCP", description: "Codex CLI calls Pika" },
  { id: "claude", name: "Claude MCP", description: "Claude calls Pika" },
];
const DEFAULT_REGEN_AGENT: RegenAgent = "codex";

function familiesForScheme(scheme: ColorSchemeId) {
  return FAMILY_DETAILS.map((family, index) => ({ ...family, color: COLOR_SCHEMES[scheme].familyColors[index] }));
}

function withSchemeColors(analysis: Analysis, scheme: ColorSchemeId): Analysis {
  const families = familiesForScheme(scheme);
  return {
    ...analysis,
    regions: analysis.regions.map((region) => ({ ...region, color: families.find((family) => family.short === region.short)?.color ?? region.color })),
  };
}

function wave(base: number, amp: number, freq: number, phase: number): number[] {
  return Array.from({ length: 64 }, (_, i) => {
    const v = base + amp * Math.sin(i * freq + phase) + amp * 0.4 * Math.sin(i * freq * 2.1 + phase * 1.7);
    return Math.round(Math.max(8, Math.min(96, v)));
  });
}

function buildAnalysis(seriesArr: number[][], duration: number, source: "demo" | "model", scheme: ColorSchemeId = DEFAULT_COLOR_SCHEME): Analysis {
  const families = familiesForScheme(scheme);
  const global = Array.from({ length: 64 }, (_, i) => Math.round(seriesArr.reduce((s, a) => s + a[i], 0) / seriesArr.length));
  const regions: Region[] = families.map((f, i) => ({ name: f.name, short: f.short, color: f.color, values: seriesArr[i], score: Math.max(...seriesArr[i]) }));
  const cognitiveSeries = Object.fromEntries(families.map((f, i) => [f.key, seriesArr[i]]));
  let peakIndex = 0;
  global.forEach((v, i) => { if (v > global[peakIndex]) peakIndex = i; });
  const sorted = [...regions].sort((a, b) => b.score - a.score);
  return { duration, frames: 64, source, global, regions: sorted, cognitiveSeries, peak: { time: (peakIndex / 63) * duration, label: sorted[0].short, value: global[peakIndex] } };
}

function demoAnalysisFor(scheme: ColorSchemeId) { return buildAnalysis([
  wave(50, 26, 0.22, 0.4),
  wave(55, 30, 0.19, 1.7),
  wave(40, 22, 0.26, 3.1),
  wave(38, 20, 0.20, 5.0),
], 32, "demo", scheme); }

function createDemoForFile(file: File, scheme: ColorSchemeId): Analysis {
  const f = ((file.size % 31) + 76) / 100;
  const duration = Math.max(12, Math.min(58, Math.round(file.size / 900000) || 32));
  return buildAnalysis([
    wave(48 * f, 26, 0.22, 0.4 + file.size % 5),
    wave(54 * f, 30, 0.19, 1.7 + file.size % 3),
    wave(40 * f, 22, 0.26, 3.1 + file.size % 7),
    wave(38 * f, 20, 0.20, 5.0 + file.size % 4),
  ], duration, "demo", scheme);
}

function engagementScore(a: Analysis): number {
  const peaks = FAMILY_KEYS.map((k) => Math.max(0, ...(a.cognitiveSeries?.[k] ?? [0])));
  return Math.round(peaks.reduce((s, v) => s + v, 0) / peaks.length);
}

function Icon({ name, size = 18 }: { name: "upload" | "play" | "pause" | "chevron" | "reset" | "info" | "close" | "settings" | "check"; size?: number }) {
  const paths = {
    upload: <><path d="M12 15V3m0 0L7 8m5-5 5 5"/><path d="M5 14v5h14v-5"/></>,
    play: <path d="m9 7 8 5-8 5V7Z" fill="currentColor" stroke="none"/>,
    pause: <><path d="M9 7v10M15 7v10"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    reset: <path d="M20 11a8 8 0 1 0 1.6 4.8M20 4v7h-7"/>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.6v.4"/></>,
    close: <path d="M6 6l12 12M18 6 6 18"/>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06-2.12 2.12-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56v.09h-3v-.09A1.7 1.7 0 0 0 10.7 18.7a1.7 1.7 0 0 0-1.87.34l-.06.06-2.12-2.12.06-.06A1.7 1.7 0 0 0 7.05 15a1.7 1.7 0 0 0-1.56-1.03H5.4v-3h.09A1.7 1.7 0 0 0 7.05 9.94a1.7 1.7 0 0 0-.34-1.87l-.06-.06 2.12-2.12.06.06a1.7 1.7 0 0 0 1.87.34 1.7 1.7 0 0 0 1.03-1.56v-.09h3v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06 2.12 2.12-.06.06a1.7 1.7 0 0 0-.34 1.87 1.7 1.7 0 0 0 1.56 1.03h.09v3h-.09A1.7 1.7 0 0 0 19.4 15Z"/></>,
    check: <path d="m5 12 4.2 4.2L19 6.8"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[name]}</svg>;
}

function linePath(values: number[], width: number, height: number, min = 0, max = 100) {
  return values.map((value, i) => {
    const x = (i / (values.length - 1)) * width;
    const y = height - ((value - min) / (max - min)) * height;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

// A self-contained preview that plays only the [start, end] window of the
// source clip. Media-fragment URLs (#t=a,b) aren't reliably honoured for blob
// sources, so we clamp playback in JS and loop within the segment.
function SegmentPreview({ src, start, end }: { src: string; start: number; end: number }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  function seekToStart() { const v = ref.current; if (v) v.currentTime = start; }
  function onTime() {
    const v = ref.current; if (!v) return;
    if (v.currentTime >= end || v.currentTime < start - 0.05) v.currentTime = start;
  }
  function toggle() {
    const v = ref.current; if (!v) return;
    if (v.paused) { if (v.currentTime < start || v.currentTime >= end) v.currentTime = start; v.play().then(() => setPlaying(true)).catch(() => {}); }
    else { v.pause(); setPlaying(false); }
  }
  return <button type="button" className="segment-preview" onClick={toggle} aria-label={playing ? "Pause segment" : "Play segment"}>
    <video ref={ref} src={src} muted playsInline preload="metadata" onLoadedMetadata={seekToStart} onTimeUpdate={onTime} onEnded={() => { setPlaying(false); seekToStart(); }}/>
    <span className="segment-play"><Icon name={playing ? "pause" : "play"} size={14}/></span>
  </button>;
}

export default function Home() {
  const [colorScheme, setColorScheme] = useState<ColorSchemeId>(DEFAULT_COLOR_SCHEME);
  const [file, setFile] = useState<File | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Analysis>(() => demoAnalysisFor(DEFAULT_COLOR_SCHEME));
  const [time, setTime] = useState(0);
  const [isPlaying, setPlaying] = useState(false);
  const [isAnalyzing, setAnalyzing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [regionIndex, setRegionIndex] = useState(0);
  const [showInfo, setShowInfo] = useState(false);
  const [showAppearance, setShowAppearance] = useState(false);
  const [genModel, setGenModel] = useState<RegenProvider>(DEFAULT_REGEN_PROVIDER);
  const [genAgent, setGenAgent] = useState<RegenAgent>(DEFAULT_REGEN_AGENT);
  const [timelineMode, setTimelineMode] = useState<"net" | "split">("net");
  // Tabs: the Studio prompt optimizer ("studio") and the analysis report
  // ("sample"). Studio leads; "sample" renders the brain-response workspace.
  const [activeTab, setActiveTab] = useState<"studio" | "sample">("studio");
  const [spliceMode, setSpliceMode] = useState(false);
  // Each cut marks a region "trimmed" from playback while the full source video
  // is kept underneath, so the region can be regenerated by a model and dropped
  // back into the same slot later. Cuts are the segments-to-regenerate list.
  const [cuts, setCuts] = useState<Cut[]>([]);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [draftCut, setDraftCut] = useState<{ start: number; end: number } | null>(null);
  const [videoDuration, setVideoDuration] = useState(0);
  const [regenJobs, setRegenJobs] = useState<Record<string, RegenJobState>>({});
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ start: number; current: number } | null>(null);
  // The <video>'s true duration, mirrored to a ref so runAnalysis can stamp it
  // onto a freshly built analysis even when prediction resolves after metadata.
  const videoDurationRef = useRef(0);
  // Tracks an in-progress drag of an existing cut band (reposition, not redraw).
  const moveRef = useRef<{ index: number; pointerX: number; origStart: number; len: number; left: number; width: number; moved: boolean; lastStart: number } | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const activeScheme = COLOR_SCHEMES[colorScheme];
  const families = useMemo(() => familiesForScheme(colorScheme), [colorScheme]);

  // Activity feed shown in the ASK CEREBRA panel: every API generation
  // (analysis + clip regeneration) writes a live, status-tracked log line.
  function logUpsert(id: string, entry: Omit<LogEntry, "id" | "ts"> & { ts?: number }) {
    setLogs((prev) => {
      const existing = prev.find((l) => l.id === id);
      if (existing) return prev.map((l) => (l.id === id ? { ...l, ...entry, ts: l.ts } : l));
      return [...prev, { id, ts: entry.ts ?? Date.now(), ...entry }];
    });
  }
  useEffect(() => { logEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [logs]);

  // Regeneration settings (model + agent) are persisted server-side so they
  // survive a server restart and reach the worker. Load them once on mount,
  // and persist every change back to disk.
  useEffect(() => {
    fetch("/api/settings").then((r) => r.json()).then((s) => {
      if (s?.provider) setGenModel(s.provider);
      if (s?.agent) setGenAgent(s.agent);
    }).catch(() => undefined);
  }, []);
  function persistSettings(next: { provider?: RegenProvider; agent?: RegenAgent }) {
    fetch("/api/settings", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(next) }).catch(() => undefined);
  }
  function chooseModel(id: RegenProvider) { setGenModel(id); persistSettings({ provider: id }); }
  function chooseAgent(id: RegenAgent) { setGenAgent(id); persistSettings({ agent: id }); }

  const currentIndex = Math.min(analysis.global.length - 1, Math.max(0, Math.round((time / analysis.duration) * (analysis.global.length - 1))));
  const currentIntensity = analysis.global[currentIndex] ?? 0;
  // Normalized net engagement: rescale the current net (mean across systems)
  // against this clip's own floor and peak, so 0 = quietest moment, 100 = peak.
  const normalizedNet = useMemo(() => {
    const g = analysis.global;
    if (!g.length) return 0;
    let min = Infinity, max = -Infinity;
    for (const v of g) { if (v < min) min = v; if (v > max) max = v; }
    return max > min ? ((currentIntensity - min) / (max - min)) * 100 : currentIntensity;
  }, [analysis.global, currentIntensity]);
  const levels = FAMILY_KEYS.map((k) => analysis.cognitiveSeries?.[k]?.[currentIndex] ?? 0);
  const domIdx = levels.reduce((best, v, i) => (v > levels[best] ? i : best), 0);
  const dominant = families[domIdx];
  const active = analysis.regions[regionIndex] ?? analysis.regions[0];
  const activeFamily = families.find((family) => family.short === active.short) ?? families[0];
  const score = useMemo(() => engagementScore(analysis), [analysis]);
  const status = "TRIBE V2";

  useEffect(() => () => { if (videoUrl) URL.revokeObjectURL(videoUrl); }, [videoUrl]);
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    if (isPlaying) v.play().catch(() => setPlaying(false));
    else v.pause();
  }, [isPlaying]);

  // Drive the timeline (and therefore the live brain activation) from playback —
  // synced to the uploaded video's own clock when present, otherwise a
  // real-time sweep. setInterval keeps advancing even if rAF is throttled.
  useEffect(() => {
    if (!isPlaying) return;
    let last = performance.now();
    const id = setInterval(() => {
      const now = performance.now();
      const v = videoRef.current;
      if (v && v.src && v.duration && !Number.isNaN(v.duration)) {
        let at = (v.currentTime / v.duration) * analysis.duration;
        // Skip cut regions so playback behaves as if they were trimmed out,
        // while the underlying source frames stay intact for regeneration.
        const cut = cuts.find((c) => at >= c.start - 1e-3 && at < c.end);
        if (cut) {
          if (cut.end >= analysis.duration - 1e-3) { v.currentTime = 0; at = 0; }
          else { v.currentTime = (cut.end / analysis.duration) * v.duration; at = cut.end; }
        }
        setTime(at);
        if (v.ended) setPlaying(false);
      } else {
        const dt = (now - last) / 1000;
        setTime((t) => (t + dt >= analysis.duration ? 0 : t + dt));
      }
      last = now;
    }, 66);
    return () => clearInterval(id);
  }, [isPlaying, analysis.duration, cuts]);

  // Stamp the <video>'s real duration onto an analysis. The demo fallback only
  // guesses duration from file size and the model samples a fixed frame count,
  // so the actual clip length is the source of truth for the timeline, splice
  // windows and frame math. Returns the analysis unchanged when no real
  // duration is known yet (onLoadedMetadata patches it in once it loads).
  function withRealDuration(a: Analysis): Analysis {
    const d = videoDurationRef.current;
    if (!d || !Number.isFinite(d) || Math.abs(a.duration - d) < 0.05) return a;
    return { ...a, duration: d, peak: { ...a.peak, time: a.duration ? (a.peak.time / a.duration) * d : 0 } };
  }

  async function runAnalysis(selected: File) {
    setAnalyzing(true);
    setTime(0);
    const logId = `analysis_${Date.now()}`;
    logUpsert(logId, { title: "TRIBE v2 analysis", detail: `${selected.name} · running cortical prediction`, status: "active", bar: true });
    try {
      const body = new FormData(); body.append("video", selected);
      const response = await fetch("/api/predict", { method: "POST", body });
      if (response.ok) {
        const remote = await response.json();
        if (remote?.regions?.length && remote?.global?.length) {
          setAnalysis(withRealDuration(withSchemeColors(remote, colorScheme)));
          logUpsert(logId, { title: "TRIBE v2 analysis", detail: `Complete · ${remote.frames} frames · ${Number(remote.duration).toFixed(0)}s`, status: "done" });
        } else { setAnalysis(withRealDuration(createDemoForFile(selected, colorScheme))); logUpsert(logId, { title: "TRIBE v2 analysis", detail: "Live model is offline — showing a sample preview.", status: "note" }); }
      } else { setAnalysis(withRealDuration(createDemoForFile(selected, colorScheme))); logUpsert(logId, { title: "TRIBE v2 analysis", detail: "Live model is offline — showing a sample preview.", status: "note" }); }
    } catch { setAnalysis(withRealDuration(createDemoForFile(selected, colorScheme))); logUpsert(logId, { title: "TRIBE v2 analysis", detail: "Couldn't reach the model — showing a sample preview.", status: "note" }); }
    finally { setAnalyzing(false); }
  }

  async function storeSource(selected: File) {
    const body = new FormData(); body.append("action", "source"); body.append("video", selected);
    const response = await fetch("/api/regenerate", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Couldn't store video");
    setSourceId(data.sourceId);
    return data.sourceId as string;
  }

  function acceptFile(selected?: File) {
    if (!selected || !selected.type.startsWith("video/")) return;
    if (videoUrl) URL.revokeObjectURL(videoUrl);
    setFile(selected); setVideoUrl(URL.createObjectURL(selected)); setPlaying(false); setRegionIndex(0);
    setCuts([]); setSpliceMode(false); setDraftCut(null); setRegenJobs({}); setSourceId(null); setVideoDuration(0); videoDurationRef.current = 0;
    runAnalysis(selected);
    storeSource(selected).catch((error) => {
      const message = error instanceof Error ? error.message : "Couldn't store the source video";
      logUpsert(`source_${Date.now()}`, { title: "Video source", detail: message, status: "error" });
    });
  }
  function onDrop(event: DragEvent<HTMLDivElement>) { event.preventDefault(); setDragging(false); acceptFile(event.dataTransfer.files[0]); }
  function onFileChange(event: ChangeEvent<HTMLInputElement>) { acceptFile(event.target.files?.[0]); }
  function scrubTo(nt: number) {
    // Snap out of trimmed regions so the playhead never rests on cut footage.
    const cut = cuts.find((c) => nt > c.start + 1e-3 && nt < c.end - 1e-3);
    const t = cut ? (Math.abs(nt - cut.start) < Math.abs(nt - cut.end) ? cut.start : cut.end) : nt;
    setTime(t);
    const v = videoRef.current;
    if (v && v.duration && !Number.isNaN(v.duration)) v.currentTime = (t / analysis.duration) * v.duration;
  }
  function reset() {
    setAnalysis(demoAnalysisFor(colorScheme)); setTime(0); setFile(null); setPlaying(false); setRegionIndex(0);
    setCuts([]); setSpliceMode(false); setDraftCut(null); setRegenJobs({}); setSourceId(null); setVideoDuration(0); videoDurationRef.current = 0;
    if (videoUrl) { URL.revokeObjectURL(videoUrl); setVideoUrl(null); }
  }

  // Cuts snap to a fixed 5s or 10s slot, so the generated clip (produced at the
  // slot's exact length) drops back in without changing total length.
  function snapWindow(a: number, b: number) {
    const lo = Math.min(a, b), hi = Math.max(a, b);
    const rawLen = hi - lo;
    const opts = [5, 10].filter((s) => s <= analysis.duration + 1e-6);
    const target = opts.length ? opts.reduce((best, s) => (Math.abs(s - rawLen) < Math.abs(best - rawLen) ? s : best), opts[0]) : analysis.duration;
    let start = lo, end = lo + target;
    if (end > analysis.duration) { end = analysis.duration; start = Math.max(0, end - target); }
    return { start, end };
  }

  function fractionFromPointer(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  }
  // Move the playhead/time without snapping out of cuts — used while drawing a
  // cut so the time readout, brain and SYSTEM SIGNAL track the dragging edge.
  function previewSeek(t: number) {
    setTime(t);
    const v = videoRef.current;
    if (v && v.duration && !Number.isNaN(v.duration)) v.currentTime = (t / analysis.duration) * v.duration;
  }
  function onCutDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!spliceMode || !videoUrl) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const f = fractionFromPointer(event);
    dragRef.current = { start: f, current: f };
    setDraftCut({ start: f * analysis.duration, end: f * analysis.duration });
    previewSeek(f * analysis.duration); // anchor at the pointer, move the playhead there
  }
  function onCutMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    const f = fractionFromPointer(event);
    dragRef.current.current = f;
    setDraftCut(snapWindow(dragRef.current.start * analysis.duration, f * analysis.duration));
    previewSeek(f * analysis.duration); // playhead follows the dragging edge
  }
  const prepareCutFrames = useCallback(async (cut: Cut) => {
    if (!sourceId || cut.frameRequested) return;
    const sameCut = (item: Cut) => Math.abs(item.start - cut.start) < 1e-3 && Math.abs(item.end - cut.end) < 1e-3;
    const slot = `${formatTime(cut.start)}–${formatTime(cut.end)}`;
    const logId = `regen_${cut.start}-${cut.end}`;
    setCuts((prev) => prev.map((item) => sameCut(item) ? { ...item, preparing: true, frameRequested: true, frameError: undefined } : item));
    const factor = videoDuration > 0 ? videoDuration / analysis.duration : 1;
    const body = new FormData();
    body.append("action", "frames"); body.append("sourceId", sourceId);
    body.append("startSec", String(cut.start * factor)); body.append("endSec", String(cut.end * factor));
    try {
      const response = await fetch("/api/regenerate", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Frame preparation failed");
      setCuts((prev) => prev.map((item) => sameCut(item) ? { ...item, frameId: data.frameId, preparing: false } : item));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Frame preparation failed";
      setCuts((prev) => prev.map((item) => sameCut(item) ? { ...item, preparing: false, frameError: message } : item));
      logUpsert(logId, { title: `Regenerate ${slot}`, detail: message, status: "error" });
    }
  }, [analysis.duration, sourceId, videoDuration]);

  function onCutUp() {
    const drag = dragRef.current;
    dragRef.current = null;
    setDraftCut(null);
    if (!drag) return;
    const { start, end } = snapWindow(drag.start * analysis.duration, drag.current * analysis.duration);
    if (end - start < 0.1) return;
    const cut: Cut = { start, end, preparing: true };
    setCuts((prev) => {
      const overlaps = prev.some((c) => start < c.end - 1e-3 && end > c.start + 1e-3);
      return overlaps ? prev : [...prev, cut].sort((a, b) => a.start - b.start);
    });
    prepareCutFrames(cut);
  }
  function removeCut(index: number) { setCuts((prev) => prev.filter((_, i) => i !== index)); }

  // A completed job is already a complete, server-merged video. Promote that
  // file to the active editor source so playback includes the new AI clip,
  // rather than merely offering it as a download beside the original source.
  const replacePreviewWithRegeneratedVideo = useCallback(async (downloadUrl: string, seg: { start: number; end: number }, jobId: string) => {
    const response = await fetch(downloadUrl, { cache: "no-store" });
    if (!response.ok) throw new Error("Couldn't load the regenerated video");
    const blob = await response.blob();
    const baseName = file?.name.replace(/\.[^.]+$/, "") || "video";
    const mergedFile = new File([blob], `${baseName}-regenerated.mp4`, { type: "video/mp4" });

    setPlaying(false);
    setFile(mergedFile);
    setVideoUrl(URL.createObjectURL(mergedFile));
    setVideoDuration(0);
    videoDurationRef.current = 0;
    setTime(seg.start);
    setDraftCut(null);
    setCuts((prev) => prev.filter((cut) => Math.abs(cut.start - seg.start) > 1e-3 || Math.abs(cut.end - seg.end) > 1e-3));
    setSpliceMode(false);
    setSourceId(null);
    setRegenJobs((prev) => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    storeSource(mergedFile).catch(() => undefined);
  }, [file]);

  // Drag an existing cut band along the timeline to reposition it (keeping its
  // length). A press that doesn't move past a small threshold is treated as a
  // click and removes the cut instead, so both gestures live on the same band.
  function onBandDown(event: ReactPointerEvent<HTMLSpanElement>, index: number) {
    if (!spliceMode) return;
    event.stopPropagation();
    event.preventDefault();
    const layer = event.currentTarget.parentElement as HTMLElement | null;
    const rect = layer?.getBoundingClientRect();
    if (!rect) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const c = cuts[index];
    moveRef.current = { index, pointerX: event.clientX, origStart: c.start, len: c.end - c.start, left: rect.left, width: rect.width, moved: false, lastStart: c.start };
  }
  function onBandMove(event: ReactPointerEvent<HTMLSpanElement>) {
    const m = moveRef.current;
    if (!m) return;
    event.stopPropagation();
    if (Math.abs(event.clientX - m.pointerX) > 3) m.moved = true;
    const deltaSec = ((event.clientX - m.pointerX) / m.width) * analysis.duration;
    const start = Math.max(0, Math.min(analysis.duration - m.len, m.origStart + deltaSec));
    const end = start + m.len;
    // Block sliding into another cut — stop at the neighbour's edge instead.
    if (cuts.some((c, i) => i !== m.index && start < c.end - 1e-3 && end > c.start + 1e-3)) return;
    m.lastStart = start;
    // Preserve frame metadata while dragging; it's refreshed on release.
    setCuts((prev) => prev.map((c, i) => (i === m.index ? { ...c, start, end } : c)));
    previewSeek(start);
  }
  function onBandUp(event: ReactPointerEvent<HTMLSpanElement>) {
    const m = moveRef.current;
    moveRef.current = null;
    if (!m) return;
    event.stopPropagation();
    if (!m.moved) { removeCut(m.index); return; }
    // The slot moved, so its prepared frames (extracted at the old position) are
    // stale. Drop them and re-extract for the new spot, otherwise Regenerate
    // would stay disabled (no frameId) or splice the wrong footage.
    const moved = { start: m.lastStart, end: m.lastStart + m.len };
    const isMoved = (c: Cut) => Math.abs(c.start - moved.start) < 1e-3 && Math.abs(c.end - moved.end) < 1e-3;
    setCuts((prev) => prev.map((c) => (isMoved(c) ? { start: c.start, end: c.end } : c)).sort((a, b) => a.start - b.start));
    prepareCutFrames(moved);
  }

  // Step 1 of regeneration: ship the source + cut timing to the backend, which
  // extracts the slot's start/end frames and queues a job. The agent then runs
  // the Codex prompt → Pika generate_video → /complete merge out-of-band.
  async function regenerate(seg: Cut) {
    const key = `${seg.start}-${seg.end}`;
    const slot = `${formatTime(seg.start)}–${formatTime(seg.end)}`;
    const logId = `regen_${key}`;
    if (!sourceId) {
      logUpsert(logId, { title: `Regenerate ${slot}`, detail: "Source video is unavailable. Upload it again before creating a splice.", status: "error" });
      return;
    }
    if (!seg.frameId) {
      logUpsert(logId, { title: `Regenerate ${slot}`, detail: seg.frameError || "Splice frames were not prepared. Remove and redraw this splice.", status: "error" });
      return;
    }
    const factor = videoDuration > 0 ? videoDuration / analysis.duration : 1;
    const durationSec = seg.end - seg.start > 7.5 ? 10 : 5;
    setRegenJobs((j) => ({ ...j, [key]: { status: "extracting" } }));
    logUpsert(logId, { title: `Regenerate ${slot}`, detail: "Frames ready · queuing generation", status: "active", bar: true });
    try {
      const body = new FormData();
      body.append("action", "job"); body.append("sourceId", sourceId); body.append("frameId", seg.frameId);
      body.append("startSec", String(seg.start * factor));
      body.append("endSec", String(seg.end * factor));
      body.append("durationSec", String(durationSec));
      body.append("provider", genModel);
      body.append("agent", genAgent);
      body.append("label", slot);
      const response = await fetch("/api/regenerate", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Generation setup failed");
      setRegenJobs((j) => ({ ...j, [key]: { status: "awaiting_generation", jobId: data.jobId, startFrame: data.startFrame, endFrame: data.endFrame, logUrl: data.jobId ? `/api/regenerate/file?job=${data.jobId}&name=job.log` : undefined } }));
      const modelName = REGEN_PROVIDERS.find((p) => p.id === genModel)?.name ?? genModel;
      const agentName = genAgent === "claude" ? "Claude" : "Codex";
      logUpsert(logId, { title: `Regenerate ${slot}`, detail: `Frames ready · generating ${durationSec}s clip via ${agentName} + Pika (${modelName})`, status: "active", bar: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed";
      setRegenJobs((j) => ({ ...j, [key]: { status: "error", error: message } }));
      logUpsert(logId, { title: `Regenerate ${slot}`, detail: message, status: "error" });
    }
  }

  // Poll queued jobs until the agent finishes the generation + merge.
  useEffect(() => {
    const active = Object.entries(regenJobs).filter(([, v]) => v.jobId && (v.status === "awaiting_generation" || v.status === "generating" || v.status === "merging"));
    if (!active.length) return;
    const id = setInterval(async () => {
      for (const [key, v] of active) {
        try {
          const response = await fetch(`/api/regenerate?job=${v.jobId}`, { cache: "no-store" });
          if (!response.ok) continue;
          const job = await response.json();
          if (job.status !== v.status || job.logTail !== v.logTail) {
            const downloadUrl = `/api/regenerate/file?job=${v.jobId}&name=final.mp4`;
            setRegenJobs((prev) => ({ ...prev, [key]: { ...prev[key], status: job.status, error: job.error, logTail: job.logTail, logUrl: job.logUrl,
              downloadUrl: job.status === "done" ? downloadUrl : prev[key]?.downloadUrl } }));
            const slot = key.split("-").map((s) => formatTime(Number(s))).join("–");
            const logId = `regen_${key}`;
            if (job.status === "generating") logUpsert(logId, { title: `Regenerate ${slot}`, detail: "Agent picked up the job · generating with Pika", status: "active", bar: true });
            else if (job.status === "merging") logUpsert(logId, { title: `Regenerate ${slot}`, detail: "Clip generated · merging into the video", status: "active", bar: true });
            else if (job.status === "done") {
              try {
                await replacePreviewWithRegeneratedVideo(downloadUrl, { start: Number(key.split("-")[0]), end: Number(key.split("-")[1]) }, key);
                logUpsert(logId, { title: `Regenerate ${slot}`, detail: "Regenerated in place · ready to play", status: "done", href: downloadUrl });
              } catch (error) {
                const message = error instanceof Error ? error.message : "Couldn't load the regenerated video";
                logUpsert(logId, { title: `Regenerate ${slot}`, detail: message, status: "error" });
              }
            }
            else if (job.status === "error") logUpsert(logId, { title: `Regenerate ${slot}`, detail: `${job.error || "Regeneration failed"}${job.logUrl ? " · log saved" : ""}`, status: "error", href: job.logUrl, linkLabel: "View job log" });
          }
        } catch { /* keep polling */ }
      }
    }, 2500);
    return () => clearInterval(id);
  }, [regenJobs, replacePreviewWithRegeneratedVideo]);

  const totalCut = cuts.reduce((sum, c) => sum + (c.end - c.start), 0);
  const trimmedDuration = Math.max(0, analysis.duration - totalCut);
  // Sorted cuts = the segments-to-regenerate list, each previewable as a
  // temporal fragment of the source clip (#t=start,end), no re-export needed.
  const segments = useMemo(() => [...cuts].sort((a, b) => a.start - b.start), [cuts]);
  const cutLayer = (cuts.length > 0 || draftCut) ? <div className={`cut-layer ${spliceMode ? "armed" : ""}`} onPointerDown={onCutDown} onPointerMove={onCutMove} onPointerUp={onCutUp} onPointerCancel={onCutUp}>
    {cuts.map((c, i) => <span className={`cut-band ${spliceMode ? "draggable" : ""}`} key={i} title={spliceMode ? "Drag to move · click to remove" : undefined} onPointerDown={spliceMode ? (e) => onBandDown(e, i) : undefined} onPointerMove={spliceMode ? onBandMove : undefined} onPointerUp={spliceMode ? onBandUp : undefined} onPointerCancel={spliceMode ? onBandUp : undefined} style={{ left: `${(c.start / analysis.duration) * 100}%`, width: `${((c.end - c.start) / analysis.duration) * 100}%` }}/>) }
    {draftCut && <span className="cut-band draft" style={{ left: `${(draftCut.start / analysis.duration) * 100}%`, width: `${((draftCut.end - draftCut.start) / analysis.duration) * 100}%` }}/>}
  </div> : (spliceMode ? <div className="cut-layer armed" onPointerDown={onCutDown} onPointerMove={onCutMove} onPointerUp={onCutUp} onPointerCancel={onCutUp}/> : null);

  const netTimelinePath = useMemo(() => linePath(analysis.global, 700, 126), [analysis.global]);
  const timelineSeries = useMemo(() => families.map((family) => ({
    ...family,
    values: analysis.cognitiveSeries?.[family.key] ?? Array.from({ length: analysis.frames }, () => 0),
  })), [analysis.cognitiveSeries, analysis.frames, families]);
  // Every thumbnail is anchored to a real analysis frame. This makes the
  // frame strip, graph markers, playhead and video seeking use one timebase.
  const filmstripFrames = useMemo(() => {
    const lastSample = Math.max(analysis.frames - 1, 1);
    return Array.from({ length: FILMSTRIP_FRAME_COUNT }, (_, slot) => {
      const sampleIndex = Math.round((slot / (FILMSTRIP_FRAME_COUNT - 1)) * lastSample);
      const position = sampleIndex / lastSample;
      return { sampleIndex, position, time: position * analysis.duration };
    });
  }, [analysis.duration, analysis.frames]);
  const playhead = (time / analysis.duration);

  function chooseScheme(scheme: ColorSchemeId) {
    setColorScheme(scheme);
    setAnalysis((current) => withSchemeColors(current, scheme));
    setShowAppearance(false);
  }

  function selectFamily(short: string) {
    const index = analysis.regions.findIndex((region) => region.short === short);
    if (index >= 0) setRegionIndex(index);
  }

  return <main className="app-shell" style={activeScheme.tokens as CSSProperties}>
    <header className="topbar">
      <div className="wordmark"><span className="wordmark-mark"><i/><i/><i/></span><span>cerebra<span className="wordmark-dot">.</span></span></div>
      <nav className="topbar-tabs" role="tablist" aria-label="Workspace tabs">
        <button role="tab" aria-selected={activeTab === "studio"} className={`topbar-tab ${activeTab === "studio" ? "active" : ""}`} onClick={() => setActiveTab("studio")}>
          <span className="tab-dot"/>Studio
        </button>
        <button role="tab" aria-selected={activeTab === "sample"} className={`topbar-tab ${activeTab === "sample" ? "active" : ""}`} onClick={() => setActiveTab("sample")}>
          <span className="tab-dot"/>{file ? file.name : "Sample clip"}
        </button>
      </nav>
      <div className="topbar-right">
        <div className="model-pill"><span className="live-dot"/>{status}</div>
        <div className="topbar-score">
        <div><span className="score-label">ENGAGEMENT SCORE</span><span className="score-foot">Four-system peak</span></div>
        <strong>{score}<small>/100</small></strong>
        </div>
        <div className="appearance-control">
          <button className={`icon-button ${showAppearance ? "active" : ""}`} onClick={() => setShowAppearance(!showAppearance)} aria-label="Color scheme settings" aria-expanded={showAppearance} aria-haspopup="dialog"><Icon name="settings" size={17}/></button>
          {showAppearance && <div className="appearance-menu" role="dialog" aria-label="Settings">
            <div className="appearance-menu-head"><span>APPEARANCE</span><small>Color scheme</small></div>
            {Object.values(COLOR_SCHEMES).map((scheme) => <button className={`scheme-option ${scheme.id === colorScheme ? "selected" : ""}`} onClick={() => chooseScheme(scheme.id)} key={scheme.id}>
              <span className="scheme-swatches">{scheme.swatches.map((swatch) => <i key={swatch} style={{ background: swatch }}/>)}</span>
              <span className="scheme-copy"><b>{scheme.name}</b><small>{scheme.description}</small></span>
              <span className="scheme-check">{scheme.id === colorScheme && <Icon name="check" size={14}/>}</span>
            </button>)}
            <div className="appearance-menu-head"><span>GENERATION MODEL</span><small>Clip regeneration</small></div>
            {REGEN_PROVIDERS.map((model) => <button className={`scheme-option model-option ${model.id === genModel ? "selected" : ""}`} onClick={() => chooseModel(model.id)} key={model.id} aria-pressed={model.id === genModel}>
              <span className="scheme-copy"><b>{model.name}</b><small>{model.description}</small></span>
              <span className="scheme-check">{model.id === genModel && <Icon name="check" size={14}/>}</span>
            </button>)}
            <div className="appearance-menu-head"><span>REGENERATION AGENT</span><small>Drives Pika</small></div>
            {REGEN_AGENTS.map((a) => <button className={`scheme-option model-option ${a.id === genAgent ? "selected" : ""}`} onClick={() => chooseAgent(a.id)} key={a.id} aria-pressed={a.id === genAgent}>
              <span className="scheme-copy"><b>{a.name}</b><small>{a.description}</small></span>
              <span className="scheme-check">{a.id === genAgent && <Icon name="check" size={14}/>}</span>
            </button>)}
          </div>}
        </div>
        <button className="icon-button" onClick={() => setShowInfo(true)} aria-label="About this analysis"><Icon name="info" size={18}/></button>
      </div>
    </header>

    {activeTab === "studio" ? <Studio/> : <section className="workspace-grid">
      <aside className="left-rail">
        <div className="panel details-panel"><div className="panel-head"><span>RUN DETAILS</span><button onClick={reset} aria-label="Reset"><Icon name="reset" size={16}/></button></div><dl><div><dt>Model</dt><dd>facebook/tribev2</dd></div><div><dt>Surface</dt><dd>fsaverage5</dd></div><div><dt>Resolution</dt><dd>0.5 s / frame</dd></div><div><dt>Readout</dt><dd>Population average</dd></div></dl></div>
        {videoUrl && <div className="panel segments-panel"><div className="panel-head"><span>04 / SEGMENTS TO REGENERATE</span>{segments.length > 0 && <span className="segments-count">{segments.length}</span>}</div>
          {segments.length === 0
            ? <p className="panel-subtitle">{spliceMode ? "Drag across the timeline to cut a segment out. Each cut is queued here for regeneration." : "Turn on Splice, then drag the timeline to cut segments out for AI regeneration."}</p>
            : <div className="segment-list">{segments.map((seg, i) => { const factor = videoDuration > 0 ? videoDuration / analysis.duration : 1; const key = `${seg.start}-${seg.end}`; const job = regenJobs[key]; const busy = Boolean(seg.preparing) || Boolean(job && (job.status === "extracting" || job.status === "awaiting_generation" || job.status === "generating" || job.status === "merging")); return <div className={`segment-card ${job?.status === "done" ? "done" : ""}`} key={key}>
              <SegmentPreview src={videoUrl} start={seg.start * factor} end={seg.end * factor}/>
              <div className="segment-meta"><span className="segment-tag">SEG {i + 1}</span><b>{formatTime(seg.start)} – {formatTime(seg.end)}</b><small>{Math.round(seg.end - seg.start)}s slot · regenerated in place</small></div>
              <div className="segment-actions">
                {job?.status === "done"
                  ? <a className="segment-download" href={job.downloadUrl} download>Download <Icon name="upload" size={11}/></a>
                  : busy
                    ? <span className="segment-status"><i className="regen-dot"/>{seg.preparing ? "Preparing frames…" : REGEN_LABEL[job!.status]}</span>
                    : <button className="segment-regen" onClick={() => regenerate(seg)} disabled={!seg.frameId} title={job?.status === "error" ? job.error : seg.frameError || (seg.frameId ? "Regenerate this slot with AI" : "Frames are being prepared with this splice")}>{job?.status === "error" ? "Retry" : "Regenerate"} <Icon name="reset" size={11}/></button>}
                <button className="segment-remove" onClick={() => removeCut(cuts.indexOf(seg))} aria-label="Remove segment"><Icon name="close" size={13}/></button>
              </div>
            </div>; })}</div>}
        </div>}
      </aside>

      <section className="editor-deck">
        <div className="timeline-card">
          <div className="timeline-top"><div><span className="eyebrow">TIMELINE</span><strong>{time.toFixed(1)}<small>s</small></strong></div><div className="timeline-actions">{(spliceMode || cuts.length > 0) && <div className="splice-controls"><span>{cuts.length ? `${cuts.length} cut${cuts.length > 1 ? "s" : ""} · ${trimmedDuration.toFixed(1)}s left` : "Drag the timeline to cut"}</span>{cuts.length > 0 && <button className="splice-clear" onClick={() => setCuts([])}>Clear</button>}</div>}<div className="view-toggle" role="group" aria-label="Timeline view"><button className={timelineMode === "net" ? "selected" : ""} onClick={() => setTimelineMode("net")}>Net</button><button className={timelineMode === "split" ? "selected" : ""} onClick={() => setTimelineMode("split")}>Split</button></div><button className={`splice-toggle ${spliceMode ? "selected" : ""}`} onClick={() => setSpliceMode((m) => !m)} disabled={!videoUrl || !sourceId} title={!videoUrl ? "Upload a video to splice" : sourceId ? "Mark portions to cut out" : "Preparing video for splicing"}>{spliceMode ? "Cutting…" : sourceId ? "Splice" : "Preparing video…"}</button><button className="play-button" onClick={() => setPlaying(!isPlaying)}><Icon name={isPlaying ? "pause" : "play"} size={17}/>{isPlaying ? "Pause" : (videoUrl ? "Play video" : "Play")}</button></div></div>
          {timelineMode === "net" ? <><div className="timeline-section-label"><span>NET ENGAGEMENT</span><small>Mean response across all systems</small></div><div className="timeline-graph net-graph"><svg viewBox="0 0 700 126" preserveAspectRatio="none"><defs><linearGradient id="netAreaFill" x1="0" x2="0" y1="0" y2="1"><stop stopColor="var(--chart-fill)" stopOpacity=".35"/><stop offset="1" stopColor="var(--chart-fill)" stopOpacity="0"/></linearGradient></defs><path className="chart-grid" d="M0 25H700M0 63H700M0 101H700"/><g className="frame-markers">{filmstripFrames.map((frame) => <line key={frame.sampleIndex} x1={frame.position * 700} x2={frame.position * 700} y1="0" y2="126"/>)}</g><path d={`${netTimelinePath} L700,126 L0,126 Z`} fill="url(#netAreaFill)"/><path d={netTimelinePath} fill="none" stroke="var(--chart-line)" strokeWidth="2.5"/><line x1={playhead * 700} x2={playhead * 700} y1="0" y2="126" className="time-line"/></svg><input aria-label="Net engagement timeline" type="range" min="0" max={analysis.duration} step="0.1" value={time} onChange={(e) => scrubTo(Number(e.target.value))}/>{cutLayer}</div></> : <><div className="timeline-section-label"><span>SPLIT VIEW</span><small>Four response systems</small></div><div className="timeline-graph systems-graph"><svg viewBox="0 0 700 126" preserveAspectRatio="none"><path className="chart-grid" d="M0 25H700M0 63H700M0 101H700"/><g className="frame-markers">{filmstripFrames.map((frame) => <line key={frame.sampleIndex} x1={frame.position * 700} x2={frame.position * 700} y1="0" y2="126"/>)}</g>{timelineSeries.map((series) => <path className={active.short === series.short ? "timeline-line active" : "timeline-line"} d={linePath(series.values, 700, 126)} fill="none" stroke={series.color} strokeWidth={active.short === series.short ? "2.8" : "1.65"} key={series.key}/>) }<line x1={playhead * 700} x2={playhead * 700} y1="0" y2="126" className="time-line"/></svg><input aria-label="System comparison timeline" type="range" min="0" max={analysis.duration} step="0.1" value={time} onChange={(e) => scrubTo(Number(e.target.value))}/>{cutLayer}</div><div className="timeline-legend">{timelineSeries.map((series) => <button className={active.short === series.short ? "selected" : ""} onClick={() => selectFamily(series.short)} aria-pressed={active.short === series.short} key={series.key}><i style={{ background: series.color }}/><span>{series.name}</span><b>{Math.round(series.values[currentIndex] ?? 0)}</b></button>)}</div></>}
          <div className="axis"><span>0:00</span><span>0:{String(Math.round(analysis.duration / 2)).padStart(2, "0")}</span><span>0:{String(Math.round(analysis.duration)).padStart(2, "0")}</span></div>
        </div>
        <section className="video-editor-strip" aria-label="Video frame timeline"><div className="editor-strip-head"><span>VIDEO</span><small>{file?.name ?? "Sample clip"} · click a frame to seek</small><b>{formatTime(time)} / {formatTime(analysis.duration)}</b></div><div className="editor-ruler"><span>0:00</span><span>{formatTime(analysis.duration / 4)}</span><span>{formatTime(analysis.duration / 2)}</span><span>{formatTime(analysis.duration * .75)}</span><span>{formatTime(analysis.duration)}</span></div><div className="editor-track"><i className="editor-playhead" style={{ left: `${playhead * 100}%` }}/>{(cuts.length > 0 || draftCut) && <div className="track-cuts">{cuts.map((c, i) => <span className="cut-band" key={i} style={{ left: `${(c.start / analysis.duration) * 100}%`, width: `${((c.end - c.start) / analysis.duration) * 100}%` }}/>) }{draftCut && <span className="cut-band draft" style={{ left: `${(draftCut.start / analysis.duration) * 100}%`, width: `${((draftCut.end - draftCut.start) / analysis.duration) * 100}%` }}/>}</div>}<div className="filmstrip">{filmstripFrames.map((frame, index) => { const inCut = cuts.some((c) => frame.time >= c.start - 1e-3 && frame.time < c.end); return <button className={`${Math.abs(time - frame.time) < analysis.duration / (FILMSTRIP_FRAME_COUNT * 2) ? "selected " : ""}${inCut ? "cut-frame" : ""}`} onClick={() => scrubTo(frame.time)} key={frame.sampleIndex} aria-label={`Seek to ${formatTime(frame.time)}${inCut ? " (trimmed)" : ""}`}><span className="frame-visual">{videoUrl ? <video src={`${videoUrl}#t=${frame.time.toFixed(2)}`} muted playsInline preload="metadata"/> : <i className={`sample-frame sample-frame-${index % 4}`}/>}</span><small>{formatTime(frame.time)}</small></button>; })}</div></div></section>
      </section>

      <aside className="right-rail">
        <div className="brain-systems">
          <div className="brain-stage">
            <div className="stage-grid"/>
            <div className="stage-meta"><span>02 / LIVE NEURAL RESPONSE</span><span className="hot-key"><i/> HIGH ACTIVITY</span></div>
            <CorticalBrain familyLevels={levels} intensity={currentIntensity}/>
            <div className="brain-caption"><span className="pulse-ring"/><span>Now driving</span><b style={{ color: dominant.color }}>{dominant.name}</b></div>
          </div>
          <div className="panel activations-panel"><div className="panel-head"><span>03 / LIVE ENGAGEMENT</span><span className="live-engagement-score" aria-label={`Normalized net engagement ${Math.round(normalizedNet)} out of 100`}><i/>{Math.round(normalizedNet)}</span></div><p className="panel-subtitle">Normalized net response · 0 = clip floor, 100 = clip peak</p><div className="region-list">{analysis.regions.map((region, index) => { const family = families.find((item) => item.short === region.short); return <button className={`region-item ${index === regionIndex ? "selected" : ""}`} onClick={() => setRegionIndex(index)} key={region.short}><span className="region-bullet" style={{ background: region.color }}/><span className="region-name"><b style={{ color: region.color }}>{region.name}</b><small>{family?.anatomy}</small><em>{region.short} · live now {Math.round((analysis.cognitiveSeries?.[family?.key ?? ""]?.[currentIndex]) ?? 0)}</em></span><span className="region-score">{Math.round(region.score)}</span><Icon name="chevron" size={15}/></button>; })}</div></div>
        </div>
        <div className="system-insights">
          <div className="panel signal-panel"><div className="signal-title"><div><span className="eyebrow">SYSTEM SIGNAL</span><strong style={{ color: active.color }}>{active.name} <span>●</span></strong></div><span className="signal-number">{(active.values[currentIndex] ?? 0).toFixed(0)}</span></div><div className="circuitry"><span>CIRCUITRY</span><strong>{activeFamily.anatomy}</strong><p>{activeFamily.impact}</p></div><svg className="mini-chart" viewBox="0 0 260 56" preserveAspectRatio="none"><path d="M0 16H260M0 38H260" className="chart-grid"/><path d={linePath(active.values, 260, 52)} fill="none" stroke={active.color} strokeWidth="2.5"/><line x1={playhead * 260} x2={playhead * 260} y1="0" y2="56" className="time-line"/></svg></div>
          <div className="panel upload-panel">
            <div className="panel-head"><span>01 / STIMULUS</span><span className={file ? "ready-tag" : ""}>{file ? "READY" : "VIDEO"}</span></div>
            <div className={`dropzone ${dragging ? "dragging" : ""}`} onDrop={onDrop} onDragOver={(e) => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onClick={() => inputRef.current?.click()}>
              <input ref={inputRef} onChange={onFileChange} accept="video/mp4,video/quicktime,video/webm" type="file" hidden/>
              {videoUrl ? <video ref={videoRef} className="video-preview" src={videoUrl} muted playsInline onLoadedMetadata={(e) => {
                const d = e.currentTarget.duration || 0;
                setVideoDuration(d);
                videoDurationRef.current = d;
                // Patch the duration onto whatever analysis is already showing
                // (covers metadata arriving after prediction has resolved).
                if (d && Number.isFinite(d)) setAnalysis((a) => (Math.abs(a.duration - d) < 0.05 ? a : { ...a, duration: d, peak: { ...a.peak, time: a.duration ? (a.peak.time / a.duration) * d : 0 } }));
              }}/> : <><div className="upload-orb"><Icon name="upload" size={22}/></div><strong>Drop a video here</strong><small>MP4, MOV, or WebM · up to 1 GB</small></>}
              {videoUrl && <span className="replace-label"><Icon name="upload" size={14}/> Replace</span>}
            </div>
            {file && <div className="file-row"><span className="file-kind">{(file.name.split(".").pop() || "MP4").toUpperCase().slice(0, 4)}</span><span className="file-name">{file.name}</span><span className="file-size">{(file.size / 1024 / 1024).toFixed(1)} MB</span></div>}
          </div>
          <div className="panel cognitive-panel"><div className="panel-head"><span>CORTICAL PROXY BREAKDOWN</span></div>{families.map((f) => { const peak = Math.max(0, ...(analysis.cognitiveSeries?.[f.key] ?? [0])); return <div className="cue-row" key={f.key}><span>{f.name}</span><div><i style={{ width: `${peak}%`, background: f.color }}/></div><b>{Math.round(peak)}</b></div>; })}<div className="breakdown-log" role="note" aria-label="Model interpretation notes"><div className="log-stamps"><span><b>MODEL</b> TRIBE v2</span><span><b>READOUT</b> CORTICAL SURFACE</span><span className="caution-stamp"><b>LIMIT</b> PROXIES ONLY</span></div><p className="log-note">Display-only cortical summaries · no emotion, intent, memory, or subcortical-state measurement.</p></div></div>
        </div>
        <div className="panel chat-panel"><div className="panel-head"><span>ASK CEREBRA</span><span className="log-count">{logs.length ? `${logs.length} EVENT${logs.length > 1 ? "S" : ""}` : "ACTIVITY"}</span></div>
          <div className="chat-log">
            {logs.length === 0
              ? <div className="chat-empty">Generation logs stream here — TRIBE&nbsp;v2 analysis and clip regenerations report live as they run.</div>
              : logs.map((log) => <div className={`log-row ${log.status}`} key={log.id}>
                  <span className="log-icon">{log.status === "active" ? <span className="log-spinner"/> : <Icon name={log.status === "done" ? "check" : log.status === "note" ? "info" : "close"} size={12}/>}</span>
                  <div className="log-body">
                    <div className="log-head"><b>{log.title}</b><time>{new Date(log.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></div>
                    {log.detail && <p>{log.detail}</p>}
                    {log.status === "active" && log.bar && <span className="log-bar"><i/></span>}
                    {log.href && <a className="log-download" href={log.href} download>{log.linkLabel ?? "Download mp4"}</a>}
                  </div>
                </div>)}
            <div ref={logEndRef}/>
          </div>
          <div className="chat-composer"><input aria-label="Ask Cerebra" placeholder="Ask about this response…"/><button type="button">Prompt</button></div></div>
      </aside>
    </section>}

    <footer><span>Population-model estimate from facebook/tribev2 · not an individual measurement or diagnosis.</span><button className="footer-link" onClick={() => setShowInfo(true)}>How this works</button></footer>

    {showInfo && <div className="info-backdrop" onClick={() => setShowInfo(false)}>
      <div className="info-modal" onClick={(e) => e.stopPropagation()}>
        <div className="info-head"><h2>How Cerebra reads an ad</h2><button className="icon-button" onClick={() => setShowInfo(false)} aria-label="Close"><Icon name="close" size={18}/></button></div>
        <p>Cerebra runs your video through Meta&apos;s <b>TRIBE v2</b> model, which predicts a population-average cortical response to the clip. We summarise that response over four manually defined cortical surface proxies, shown live as the video plays. These proxies do not measure emotional state, intent, memory, or subcortical activity.</p>
        <ul className="info-systems">{families.map((f) => <li key={f.key}><span className="info-dot" style={{ background: f.color }}/><div><b>{f.name}</b><span>{f.blurb}</span></div></li>)}</ul>
        <p className="info-foot">TRIBE v2 provides modeled population-average cortical predictions. This interface adds display-only cortical proxy summaries; it is not a measurement of any individual viewer, a cognitive-state detector, or a medical/diagnostic tool.</p>
      </div>
    </div>}
  </main>;
}
