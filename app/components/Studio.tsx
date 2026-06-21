"use client";

import { Fragment, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

// Stage 1 of the Cerebra loop. Input a brief (voice or text) → the Python RAG
// optimizer returns the assembled Seedance 2.0 payload (SYSTEM + research +
// retrieval + Pika skill). Stage 2 sends the optimized creative to Pika's
// Seedance provider, then plays the returned render directly in the phone panel.
//
// This renders inside the workspace as the "Studio" tab, styled to match the
// Sample-clip report (cerebra surfaces, DM Mono labels, the active scheme's
// accent) rather than the original standalone agency theme.

type Retrieved = {
  id: string; title: string; content: string; category: string;
  industry: string; source: string; source_url: string; score: number;
};
type Finding = { title: string; technique: string; why_it_worked: string; industry: string; source_url: string };
type RAGTrace = {
  backend: string; endpoint: string; index: string; key_prefix: string;
  storage_type: string; vector_field: string; embedding_model: string;
  vector_dimensions: number; distance_metric: string; query: string; top_k: number;
  index_document_count: number; retrieved_count: number; retrieved_ids: string[];
  retrieved_scores: number[]; verified: boolean;
};
type Creative = {
  optimized_prompt: string; generation_constraints: string[]; audio_direction: string;
  aspect_ratio: string;
  duration_seconds: number; hook: string; style_tags: string[];
  techniques_applied: string[]; rationale: string;
  model: string; resolution: string; sound: boolean;
};
type OptimizeResponse = {
  creative: Creative; video_model_payload: string; brief: string;
  cached: boolean; llm_backed: boolean; retrieved: Retrieved[]; research: Finding[];
  rag: RAGTrace;
};
type Health = {
  ready: boolean; llm_backed?: boolean; model?: string | null;
  redis?: {
    connected: boolean; endpoint: string; cloud: boolean; search_available: boolean;
    knowledge_index: string; index_ready: boolean; document_count: number;
  };
};

const INDUSTRIES = ["", "beverage", "tech", "beauty", "food", "saas", "fitness", "general"];
const ASPECTS = ["9:16", "3:4", "1:1", "16:9"];

// The four RAG stages the pipeline visualizes, in execution order. These mirror
// exactly what the Python service does server-side: embed the brief → query
// Redis Vector Search → assemble the retrieved context → generate with the LLM.
type StepIcon = "db" | "search" | "layers" | "spark";
const RAG_STEPS: { key: string; n: string; label: string; sub: string; icon: StepIcon }[] = [
  { key: "embed", n: "01", label: "Redis Vector DB", sub: "Embed brief → query vector", icon: "db" },
  { key: "retrieve", n: "02", label: "Retrieve", sub: "KNN cosine vector search", icon: "search" },
  { key: "append", n: "03", label: "Append", sub: "Assemble SYSTEM + context", icon: "layers" },
  { key: "generate", n: "04", label: "Generate", sub: "Claude Opus 4.8 · RAG-grounded", icon: "spark" },
];

function Icon({ name, size = 16 }: { name: "mic" | "spark" | "copy" | "film" | "send" | "db" | "search" | "layers" | "check" | "redis"; size?: number }) {
  const p: Record<string, ReactNode> = {
    mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
    spark: <path d="M12 3v4m0 10v4m9-9h-4M7 12H3m13.5-6.5-2.8 2.8M8.3 15.7l-2.8 2.8m11 0-2.8-2.8M8.3 8.3 5.5 5.5" />,
    copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></>,
    film: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M8 4v16M16 4v16M3 9h5M3 15h5M16 9h5M16 15h5" /></>,
    send: <path d="M4 12 20 4l-6 16-3-7-7-1Z" />,
    db: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6" /></>,
    search: <><circle cx="11" cy="11" r="7" /><path d="m21 21-4.35-4.35" /></>,
    layers: <><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5M3 17l9 5 9-5" /></>,
    check: <path d="M20 6 9 17l-5-5" />,
    redis: <><path d="M3 6c0 1.1 4 2 9 2s9-.9 9-2-4-2-9-2-9 .9-9 2Z" /><path d="M3 6v6c0 1.1 4 2 9 2s9-.9 9-2V6" /><path d="M3 12v6c0 1.1 4 2 9 2s9-.9 9-2v-6" /></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">{p[name]}</svg>
  );
}

export function Studio() {
  const [brief, setBrief] = useState("");
  const [product, setProduct] = useState("");
  const [industry, setIndustry] = useState("");
  const [aspect, setAspect] = useState("9:16");
  const [liveResearch, setLiveResearch] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [videoUrl, setVideoUrl] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genStatus, setGenStatus] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  // RAG pipeline animation state machine.
  const [ragActive, setRagActive] = useState(false);
  const [ragStep, setRagStep] = useState(0);
  const [ragDone, setRagDone] = useState(false);
  const [ragErr, setRagErr] = useState(false);
  const ragTimers = useRef<number[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // Pull live Redis vector-store status so the pipeline shows real numbers
  // (endpoint, index, vector count) even before the first run.
  useEffect(() => {
    let alive = true;
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => { if (alive) setHealth(d as Health); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => () => { ragTimers.current.forEach((t) => clearTimeout(t)); }, []);

  function clearRagTimers() {
    ragTimers.current.forEach((t) => clearTimeout(t));
    ragTimers.current = [];
  }
  // Drive the visible stages while the single /optimize request is in flight.
  // Embed + retrieve + append are fast server-side; the LLM generate is the
  // long tail, so we hold on step 4 until the response lands.
  function startRag() {
    clearRagTimers();
    setRagErr(false); setRagDone(false); setRagActive(true); setRagStep(0);
    ragTimers.current = [
      window.setTimeout(() => setRagStep(1), 1100),
      window.setTimeout(() => setRagStep(2), 2400),
      window.setTimeout(() => setRagStep(3), 3300),
    ];
  }
  function finishRag() { clearRagTimers(); setRagStep(4); setRagDone(true); setRagActive(false); }
  function failRag() { clearRagTimers(); setRagErr(true); setRagActive(false); }

  function ragStatus(i: number): "idle" | "active" | "done" | "error" {
    if (ragErr && i === ragStep) return "error";
    if (ragDone || i < ragStep) return "done";
    if (i === ragStep && ragActive) return "active";
    return "idle";
  }
  function ragStat(i: number): string {
    const rag = result?.rag;
    const red = health?.redis;
    if (i === 0) {
      if (rag) return `${rag.vector_dimensions}-d · ${rag.embedding_model.split("/").pop()}`;
      return red ? red.endpoint : "MiniLM · 384-d";
    }
    if (i === 1) {
      if (rag) return `top ${rag.retrieved_count} of ${rag.index_document_count} · ${rag.distance_metric}`;
      return red ? `${red.document_count} vectors · cosine` : "cosine KNN";
    }
    if (i === 2) {
      if (result) return `${result.video_model_payload.length.toLocaleString()} chars assembled`;
      return "SYSTEM + evidence + skill";
    }
    if (result) return `${result.creative.model} · ${result.cached ? "cached" : "fresh"}`;
    return "Claude Opus 4.8";
  }

  async function toggleMic() {
    // Stop an in-progress recording → triggers onstop → Whisper transcription.
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This browser can't record audio. Type the brief instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : MediaRecorder.isTypeSupported("audio/mp4")
          ? "audio/mp4"
          : "";
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        const type = mr.mimeType || "audio/webm";
        transcribeAudio(new Blob(chunksRef.current, { type }), type);
      };
      mediaRecorderRef.current = mr;
      setError(null);
      setRecording(true);
      mr.start();
    } catch {
      setError("Microphone blocked. Allow mic access for localhost, then try again.");
      setRecording(false);
    }
  }

  async function transcribeAudio(blob: Blob, mime: string) {
    setTranscribing(true);
    try {
      const ext = mime.includes("mp4") ? "mp4" : mime.includes("ogg") ? "ogg" : "webm";
      const fd = new FormData();
      fd.append("audio", blob, `clip.${ext}`);
      const res = await fetch("/api/transcribe", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || data?.detail || `Transcription failed (${res.status})`);
      const text = (data.text || "").trim();
      if (text) setBrief((b) => (b ? b + " " : "") + text);
      else setError("Didn't catch any speech — try again, a bit closer to the mic.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transcription failed.");
    } finally {
      setTranscribing(false);
    }
  }

  async function optimize() {
    if (!brief.trim()) { setError("Enter a brief first."); return; }
    setLoading(true); setError(null); setResult(null);
    startRag();
    try {
      const res = await fetch("/api/optimize", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          brief,
          product: product || undefined,
          industry: industry || undefined,
          aspect_ratio: aspect,
          live_research: liveResearch,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || data?.detail || `Request failed (${res.status})`);
      setResult(data as OptimizeResponse);
      finishRag();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Optimization failed.");
      failRag();
    } finally {
      setLoading(false);
    }
  }

  function copyPayload() {
    if (!result) return;
    navigator.clipboard?.writeText(result.video_model_payload);
    setCopied(true); setTimeout(() => setCopied(false), 1400);
  }

  async function generate() {
    if (!result) return;
    setGenerating(true); setGenStatus(null); setVideoUrl("");
    try {
      const cr = result.creative;
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          prompt: cr.optimized_prompt,
          aspect_ratio: cr.aspect_ratio,
          duration_seconds: cr.duration_seconds,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || data?.detail || `Generate failed (${res.status})`);
      if (data.status === "completed" && data.video_url) setVideoUrl(data.video_url);
      else setGenStatus(data.message || "Generation unavailable.");
    } catch (e) {
      setGenStatus(e instanceof Error ? e.message : "Generation failed.");
    } finally {
      setGenerating(false);
    }
  }

  const c = result?.creative;
  const redis = health?.redis;
  const ragVisible = ragActive || ragDone || ragErr;

  return (
    <section className="studio-shell" role="tabpanel" aria-label="Studio">
      <div className="studio-inner">
        <div className="studio-nav">
          <span className="eyebrow">STAGE 1 / PROMPT OPTIMIZER</span>
          <div className="model-pill">
            <span className="live-dot" />
            {result ? (result.llm_backed ? "LLM BACKED" : "TEMPLATE") : "READY"}
          </div>
        </div>

        <header className="studio-hero">
          <span className="studio-eyebrow"><i /> RESEARCH-BACKED · REDIS RAG · REALISTIC SHORT-FORM</span>
          <h1>Brief to broadcast, <em>engineered.</em></h1>
          <p>
            Speak or type a brief. Cerebra retrieves proven ad patterns from Redis, researches what&apos;s
            winning in your space, and engineers the exact payload your video model needs — system prompt,
            evidence, and the Seedance 2.0 generation skill, assembled.
          </p>
        </header>

        {/* ── RAG PIPELINE — live Redis Vector Search visualization ───────── */}
        <section className={`rag${ragActive ? " running" : ""}${ragDone ? " done" : ""}${ragErr ? " errored" : ""}`}>
          <div className="rag-head">
            <div className="rag-title">
              <span className="rag-redis"><Icon name="redis" size={14} /> REDIS</span>
              <b>RAG PIPELINE</b>
              <small>vector retrieval → context assembly → generation</small>
            </div>
            <div className={`rag-conn-badge ${redis?.connected ? "ok" : "off"}`}>
              <span className="dot" />
              {redis
                ? <>VECTOR DB LIVE · {redis.endpoint} · {redis.knowledge_index} · {redis.document_count} vectors</>
                : "connecting to redis vector store…"}
            </div>
          </div>
          <div className="rag-track">
            {RAG_STEPS.map((s, i) => {
              const st = ragStatus(i);
              return (
                <Fragment key={s.key}>
                  {i > 0 && (
                    <div className={`rag-conn${ragStatus(i - 1) === "done" ? " filled" : ""}${st === "active" ? " flowing" : ""}`}>
                      <span className="pkt" />
                    </div>
                  )}
                  <div className={`rag-node ${st}`}>
                    <div className="rag-ic">
                      {st === "done"
                        ? <Icon name="check" size={18} />
                        : st === "active"
                          ? <span className="rag-spin" />
                          : <Icon name={s.icon} size={18} />}
                    </div>
                    <div className="rag-body">
                      <div className="rag-row"><span className="rag-n">{s.n}</span><b>{s.label}</b></div>
                      <small>{s.sub}</small>
                      <em className="rag-stat">{ragStat(i)}</em>
                    </div>
                  </div>
                </Fragment>
              );
            })}
          </div>
          {ragVisible && (
            <div className="rag-foot">
              {ragErr
                ? <span className="bad">pipeline error — see message below</span>
                : ragDone
                  ? <span className="good">✓ context grounded in {result?.rag.retrieved_count ?? 0} Redis vectors · {result?.rag.verified ? "provenance verified" : "unverified"}</span>
                  : <span className="run"><span className="rag-spin sm" /> running retrieval-augmented generation…</span>}
            </div>
          )}
        </section>

        <div className="studio-grid">
          {/* ── INPUT ─────────────────────────────────────────── */}
          <section className="panel studio-card">
            <div className="panel-head"><span>BRIEF — VOICE OR TEXT</span></div>
            <div className="card-body">
              <div className="fld brief-wrap">
                <label>WHAT AD DO YOU WANT?</label>
                <textarea rows={6} value={brief} placeholder="e.g. a 4-second refreshing Pepsi ad — energetic, realistic, instant payoff…"
                  onChange={(e) => setBrief(e.target.value)} />
                <button className={`mic${recording ? " live" : ""}${transcribing ? " busy" : ""}`}
                  onClick={toggleMic} disabled={transcribing}
                  title={recording ? "Stop & transcribe" : transcribing ? "Transcribing…" : "Record voice (Whisper)"}
                  aria-label="Voice input">
                  {transcribing ? <span className="spin" /> : <Icon name="mic" />}
                </button>
              </div>
              {(recording || transcribing) && (
                <div className="mic-status">
                  {recording
                    ? <><span className="rec-dot" /> Recording… tap the mic to stop &amp; transcribe</>
                    : <><span className="spin" /> Transcribing with Whisper…</>}
                </div>
              )}

              <div className="two">
                <div className="fld">
                  <label>PRODUCT / BRAND</label>
                  <input type="text" value={product} placeholder="optional" onChange={(e) => setProduct(e.target.value)} />
                </div>
                <div className="fld">
                  <label>INDUSTRY</label>
                  <select value={industry} onChange={(e) => setIndustry(e.target.value)}>
                    {INDUSTRIES.map((v) => <option key={v} value={v}>{v === "" ? "auto" : v}</option>)}
                  </select>
                </div>
              </div>

              <div className="fld">
                <label>ASPECT RATIO</label>
                <select value={aspect} onChange={(e) => setAspect(e.target.value)}>
                  {ASPECTS.map((v) => <option key={v} value={v}>{v}{v === "9:16" ? " · vertical (default)" : ""}</option>)}
                </select>
                <small className="hint">Duration is read from your brief. If omitted, the default is 10 seconds.</small>
              </div>

              <button className="toggle" onClick={() => setLiveResearch((v) => !v)} type="button">
                <span>Live ad research<small>Web-search winning ads, cache to Redis</small></span>
                <span className={`sw${liveResearch ? " on" : ""}`}><i /></span>
              </button>

              <button className="btn" onClick={optimize} disabled={loading}>
                <Icon name="spark" />{loading ? "Running RAG…" : "Run RAG · optimize prompt"}
              </button>
              {loading && (
                <div className="loading-note">
                  <span className="spin" /> Retrieving from Redis + running Claude Opus 4.8 —
                  this takes ~15–25s on a fresh brief.
                </div>
              )}
              {error && <div className="err">{error}</div>}
              <p className="hint">Stage 1 engineers the Seedance 2.0 audio-video payload. Stage 2 renders it through Pika.</p>
            </div>
          </section>

          {/* ── PAYLOAD + EVIDENCE ────────────────────────────── */}
          <section className="studio-col">
            <div className="panel studio-card">
              <div className="panel-head">
                <span>VIDEO-MODEL PAYLOAD {result && <span className={result.cached ? "warn" : "ready-tag"}>{result.cached ? "● CACHED" : "● FRESH"}</span>}</span>
                {result && <button className="copy" onClick={copyPayload}>{copied ? "COPIED ✓" : "COPY"}</button>}
              </div>
              {result
                ? <pre className="payload">{result.video_model_payload}</pre>
                : <div className="empty-note">Run an optimization to assemble <b>Seedance 2.0 SYSTEM prompt + context (research + vector retrieval) + generation skill</b> — the exact model-ready payload.</div>}
            </div>

            {c && (
              <div className="panel studio-card">
                <div className="panel-head"><span>OPTIMIZED CREATIVE</span></div>
                <div className="meta">
                  <div className="kv"><span>HOOK · 0–3s</span><b>{c.hook || "—"}</b></div>
                  <div className="kv"><span>WHY IT WORKS</span><p>{c.rationale}</p></div>
                  <div className="kv"><span>TECHNIQUES APPLIED</span>
                    <div className="chips">{c.techniques_applied.map((t, i) => <span key={i} className="chip">{t}</span>)}</div></div>
                  <div className="kv"><span>STYLE</span>
                    <div className="chips">{c.style_tags.map((t, i) => <span key={i} className="chip">{t}</span>)}
                      <span className="chip">{c.aspect_ratio}</span><span className="chip">{c.duration_seconds}s</span>
                      <span className="chip">{c.model}</span><span className="chip">{c.resolution}</span>
                      <span className="chip">{c.sound ? "native sound" : "silent"}</span></div></div>
                  <div className="kv"><span>NATIVE AUDIO DIRECTION</span><p>{c.audio_direction}</p></div>
                  <div className="kv"><span>GENERATION GUARDRAILS</span>
                    <div className="chips">{c.generation_constraints.map((t, i) => <span key={i} className="chip">{t}</span>)}</div></div>
                </div>
              </div>
            )}

            {result && (result.retrieved.length > 0 || result.research.length > 0) && (
              <div className="panel studio-card">
                <div className="panel-head">
                  <span>EVIDENCE — REDIS VECTOR RETRIEVAL{result.research.length ? " + LIVE RESEARCH" : ""}</span>
                  <span className={result.rag.verified ? "ready-tag" : "warn"}>
                    {result.rag.verified ? "● VERIFIED" : "● UNVERIFIED"}
                  </span>
                </div>
                <div className="meta">
                  <div className="kv"><span>RAG STORE</span>
                    <p>{result.rag.endpoint} · {result.rag.index} · {result.rag.index_document_count} vectors</p></div>
                  <div className="kv"><span>VECTOR SEARCH</span>
                    <div className="chips">
                      <span className="chip">{result.rag.vector_dimensions}D</span>
                      <span className="chip">{result.rag.distance_metric}</span>
                      <span className="chip">top {result.rag.top_k}</span>
                      <span className="chip">{result.rag.embedding_model.split("/").pop()}</span>
                    </div></div>
                  <div className="kv"><span>EMBEDDED RETRIEVAL QUERY</span><p>{result.rag.query}</p></div>
                </div>
                <div className="ev">
                  {result.retrieved.map((d) => (
                    <div className="ev-item" key={d.id}>
                      <div className="ev-score">{d.score.toFixed(2)}</div>
                      <div className="ev-body">
                        <b>{d.title}</b>
                        <small>{d.content.length > 150 ? d.content.slice(0, 150) + "…" : d.content}</small>
                        <span className={`ev-tag${d.source === "research" ? " research" : ""}`}>{d.category} · {d.industry} · {d.source}</span>
                      </div>
                    </div>
                  ))}
                  {result.research.map((f, i) => (
                    <div className="ev-item" key={`r${i}`}>
                      <div className="ev-score research">NEW</div>
                      <div className="ev-body">
                        <b>{f.title}</b>
                        <small>{f.technique} — {f.why_it_worked}</small>
                        <span className="ev-tag research">research · {f.industry}{f.source_url ? ` · ${f.source_url}` : ""}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* ── PIKA OUTPUT (Stage 2 preview) ─────────────────── */}
          <aside className="panel studio-card right">
            <div className="panel-head"><span>PIKA OUTPUT</span><span className="warn">SEEDANCE 2.0</span></div>
            <div className="card-body" style={{ paddingBottom: 0 } as CSSProperties}>
              <button className="btn" onClick={generate} disabled={!result || generating}>
                <Icon name="film" size={14} />{generating ? "Generating…" : "Generate video"}
              </button>
              {!result && <p className="hint">Optimize a brief first, then Generate.</p>}
              {generating && <div className="loading-note"><span className="spin" /> Rendering Seedance 2.0 at 1080p — usually 1–5 min.</div>}
              {genStatus && <div className="err">{genStatus}</div>}
            </div>
            <div className="phone">
              {videoUrl
                ? <video src={videoUrl} controls autoPlay playsInline loop />
                : <div className="phone-empty">
                    <div className="orb"><Icon name="film" size={20} /></div>
                    <b>{generating ? "Rendering on Seedance 2.0…" : result ? "Ready to generate" : "Awaiting a brief"}</b>
                    <small>{generating
                      ? "Your video plays here automatically."
                      : result
                        ? "Hit Generate — the render plays here, no URLs."
                        : "Optimize a brief first."}</small>
                  </div>}
            </div>
            <div className="phone-cap"><span>{aspect} · 1080P · SEEDANCE 2.0</span><span>{c ? `${c.duration_seconds}s` : "10s default"}</span></div>
          </aside>
        </div>
      </div>
    </section>
  );
}
