type RetrievedDoc = {
  id: string;
  title: string;
  content: string;
  category: string;
  industry: string;
  source: string;
  source_url: string;
  score: number;
};

type ResearchFinding = {
  title: string;
  technique: string;
  why_it_worked: string;
  industry: string;
  source_url: string;
};

type OptimizedCreative = {
  optimized_prompt: string;
  generation_constraints: string[];
  audio_direction: string;
  aspect_ratio: string;
  duration_seconds: number;
  hook: string;
  style_tags: string[];
  techniques_applied: string[];
  rationale: string;
  model: string;
  resolution: string;
  sound: boolean;
};

type OptimizeRequest = {
  brief: string;
  product?: string;
  industry?: string;
  aspect_ratio?: string;
  duration_seconds?: number;
  live_research?: boolean;
};

const SEEDANCE_ASPECTS = new Set(["9:16", "1:1", "16:9", "3:4", "4:3", "21:9"]);
const DEFAULT_DURATION = numberEnv("CEREBRA_DURATION", 10);
const VIDEO_MODEL = process.env.CEREBRA_VIDEO_MODEL || "seedance-2.0";
const SEEDANCE_RESOLUTION = process.env.CEREBRA_SEEDANCE_RESOLUTION || "1080p";
const SEEDANCE_SOUND = boolEnv("CEREBRA_SEEDANCE_SOUND", true);
const TOP_K = numberEnv("CEREBRA_TOP_K", 6);

const NUMBER_WORDS: Record<string, number> = {
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
  twenty: 20,
};

const SEED_DOCS: RetrievedDoc[] = [
  {
    id: "hook-first-second",
    title: "Win the first second or lose the view",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "On short-form feeds the scroll decision is made in under a second. Open on the single most arresting frame — motion, a face mid-expression, an unexpected object, or a pattern interrupt — never on a logo or a slow establishing shot. Front-load the payoff; do not build to it.",
  },
  {
    id: "faces-and-eyes",
    title: "Faces and direct gaze capture attention",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Human faces, and especially direct eye contact, are processed pre-attitively and reliably draw and hold the viewer's gaze. A real, emotionally legible human face early in the clip raises attention and authenticity.",
  },
  {
    id: "emotion-beats-information",
    title: "Emotion outperforms rational information",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Emotionally driven campaigns outperform rational, feature-led ones on long-term brand effects. Feeling is what gets encoded and recalled; specs are forgotten. Lead with one clear feeling and let the product ride on it.",
  },
  {
    id: "peak-end-rule",
    title: "Engineer a peak and a strong ending",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Memory of an experience is dominated by its emotional peak and its end. Design one unmistakable peak moment and a deliberate, satisfying final beat. The last frame should reward the watch and carry the brand.",
  },
  {
    id: "distinctive-assets",
    title: "Brand with distinctive assets, not just logos",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Brands grow by being easy to notice and recall via distinctive assets — signature color, shape, sound, character, or motion. Bake the brand cue into an action or object the viewer remembers.",
  },
  {
    id: "sound-on-design",
    title: "Design for sound-on, survive sound-off",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Native audio, a spoken hook, and beat-matched cuts multiply engagement, but the core story must still read silently. Carry meaning in visual action and product behavior.",
  },
  {
    id: "show-dont-tell",
    title: "Show the transformation, don't narrate it",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Demonstration beats description. Show the before/after, first bite, pour, crack, reveal, or moment the product does its job. Sensory concrete action converts; adjectives do not.",
  },
  {
    id: "single-minded-message",
    title: "One idea per ad",
    category: "principle",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Effective ads are single-minded: one product, one benefit, one feeling, one memorable device. Every extra message dilutes recall and attribution.",
  },
  {
    id: "shortform-beat-structure",
    title: "Short-form structure: HOOK → cuts → OUTRO",
    category: "structure",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "A reliable short-form skeleton: HOOK, then fast escalating cuts that reveal or demonstrate the value, then an OUTRO that lands the feeling and brand. No dead frames.",
  },
  {
    id: "vertical-framing",
    title: "Vertical 9:16 framing and safe zones",
    category: "structure",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Compose for a tall phone frame: subject centered or upper-third, action in the middle band, and keep top and bottom UI zones clear of critical details.",
  },
  {
    id: "pacing-words-per-second",
    title: "Dialogue pacing for short-form",
    category: "structure",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Spoken short-form needs tight lines split across beats. Each beat gets one concise line that advances the arc; slower delivery drags and viewers bounce.",
  },
  {
    id: "ref-coke-masterpiece",
    title: "Reference: Coca-Cola 'Masterpiece'",
    category: "reference_ad",
    industry: "beverage",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "A bottle of Coke passes through a visually stunning chain of hand-offs. The product is the connective thread; red color and contour silhouette anchor attribution. Build one striking visual device that the product literally moves through.",
  },
  {
    id: "ref-coke-for-everyone",
    title: "Reference: Coca-Cola 'For Everyone'",
    category: "reference_ad",
    industry: "beverage",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Relatable people enjoying Coke in everyday moments, using warmth and inclusion over polish. Let unguarded emotion carry the spot and tie the product to a universal human moment.",
  },
  {
    id: "ref-ipad-float",
    title: "Reference: Apple 'iPad Pro — Float'",
    category: "reference_ad",
    industry: "tech",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Colorful objects and the iPad drift weightlessly, communicating thinness without specs. Pick one physical property to dramatize, stage it cleanly, and let the product be the hero.",
  },
  {
    id: "pika-realistic-look",
    title: "Seedance craft: photoreal materials, optics, and physics",
    category: "pika_craft",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Seedance responds to director-level physical specificity: lens feel, depth of field, motivated light, exposure, skin and material texture, contact, weight, momentum, liquids, reflections, and one intentional camera path.",
  },
  {
    id: "pika-prompt-anatomy",
    title: "Seedance craft: chronological audio-video prompt anatomy",
    category: "pika_craft",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Write one chronological production brief. Anchor unchanged subject, product, wardrobe, packaging, and setting; structure each timed beat as action, camera, performance, light, synchronized dialogue, foley, ambience, and music.",
  },
  {
    id: "pika-trust-references",
    title: "Seedance craft: preserve continuity and simplify generated screens",
    category: "pika_craft",
    industry: "tech",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "State continuity explicitly across the whole timeline: same face, hands, wardrobe, product count, product geometry, environment, and lighting. Avoid depending on dense generated UI or tiny typography.",
  },
  {
    id: "pika-negative-prompt",
    title: "Seedance craft: express guardrails as positive requirements",
    category: "pika_craft",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Seedance does not accept a separate negative prompt. Translate risks into positive instructions: stable anatomy and identity, consistent hands and face, one unchanged product, clean packaging geometry, smooth natural motion, coherent lighting, intentional cuts, and clean frames.",
  },
  {
    id: "seedance-biomechanical-motion",
    title: "Seedance craft: choreograph physical action through biomechanics",
    category: "pika_craft",
    industry: "fitness",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Complex physical action looks believable when the prompt describes anticipation, planted support, weight transfer, joint rotation, contact, follow-through, and recovery. Impact sound must hit the exact contact frame and objects need realistic momentum.",
  },
  {
    id: "ugc-authenticity",
    title: "UGC authenticity: raw creator texture",
    category: "structure",
    industry: "general",
    source: "seed",
    source_url: "",
    score: 0,
    content:
      "Creator-style ads feel native when they use handheld phone framing, human hesitation, conversational delivery, hard jump cuts, and a reveal that proves the product instead of describing it.",
  },
];

const SYSTEM_PROMPT = [
  "Short-form audio-visual advertising — senior director and prompt engineer for Seedance 2.0.",
  "",
  "Create one coherent, photorealistic short-form ad generated from scratch with Seedance 2.0. The prompt should give director-level control over performance, lighting, camera movement, physical action, and native synchronized audio while preserving continuity across a compact story.",
  "",
  "Write one chronological production brief, not disconnected shots. Start with format, visual language, subject, product, setting, and emotional intent. For every beat, specify action, camera, performance, lighting evolution, and synchronized sound. Express all safeguards as positive requirements inside the main prompt: stable anatomy, consistent product geometry, smooth natural motion, coherent exposure, clean frames, and no accidental overlays.",
].join("\n");

const SEEDANCE_SKILL = [
  "PIKA SEEDANCE 2.0 SHORT-FORM SKILL — one native audio-video generation",
  "",
  "Runtime contract: provider=seedance, mode=text_to_video, backend=ark, resolution=1080p, duration=4–15 seconds, sound=true. Unsupported: negative_prompt, shots, quality_mode, prompt_adherence, kling_model.",
  "",
  "Prompt anatomy:",
  "1. FORMAT + INTENT — genre, realism level, audience-native texture, one feeling.",
  "2. CONTINUITY ANCHOR — exact subject, product, wardrobe, setting, signature assets.",
  "3. CHRONOLOGICAL TIMELINE — fit the beat count to the authoritative duration.",
  "4. AUDIO PLAN — dialogue when useful, room tone, foley, music arc, sonic ending.",
  "5. FINAL STATE — satisfying visual and sonic landing with the brand attributable.",
  "6. POSITIVE GUARDRAILS — stable anatomy and identity, consistent product, lifelike physics, clean frame.",
].join("\n");

export function studioHealth() {
  return {
    ready: true,
    llm_backed: false,
    model: null,
    optimizer: {
      mode: "in-process",
      endpoint: "Next.js route handler",
      external_service_required: false,
    },
    redis: {
      connected: true,
      endpoint: "in-process",
      cloud: false,
      search_available: true,
      knowledge_index: "local_ad_knowledge",
      index_ready: true,
      document_count: SEED_DOCS.length,
    },
  };
}

export function optimizeStudioBrief(req: OptimizeRequest) {
  const brief = (req.brief || "").trim();
  if (!brief) {
    throw new RequestError("Brief is empty.", 422);
  }

  const aspectRatio = SEEDANCE_ASPECTS.has(req.aspect_ratio || "") ? req.aspect_ratio! : "9:16";
  const duration = resolveDuration(brief, req.duration_seconds, DEFAULT_DURATION);
  const retrievalQuery = [
    `creative brief: ${brief}`,
    `product or brand: ${req.product || "unspecified"}`,
    `industry: ${req.industry || "general"}`,
    "retrieve advertising principles, successful reference patterns, short-form structure, visual craft, audio craft, and model-specific guidance",
  ].join("\n");
  const retrieved = retrieve(retrievalQuery, req.industry, TOP_K);
  const findings: ResearchFinding[] = [];
  const creative = optimizeTemplate(req, retrieved, findings, aspectRatio, duration);
  const context = contextBlock(retrieved, findings);
  const creativeBlock = [
    creative.optimized_prompt,
    "",
    `MODEL: ${creative.model} | RESOLUTION: ${creative.resolution} | SOUND: ${String(creative.sound)}`,
    `ASPECT RATIO: ${creative.aspect_ratio} | DURATION: ${creative.duration_seconds}s`,
    `AUDIO DIRECTION: ${creative.audio_direction}`,
    `GENERATION CONSTRAINTS: ${creative.generation_constraints.join("; ")}`,
    `HOOK: ${creative.hook}`,
    `STYLE: ${creative.style_tags.join(", ")}`,
    `TECHNIQUES APPLIED: ${creative.techniques_applied.join(", ")}`,
    `WHY IT WORKS: ${creative.rationale}`,
  ].join("\n");

  return {
    creative,
    video_model_payload: assemblePayload(creativeBlock, context),
    brief,
    cached: false,
    llm_backed: false,
    retrieved,
    research: findings,
    rag: {
      backend: "local",
      endpoint: "in-process",
      index: "local_ad_knowledge",
      key_prefix: "seed_docs",
      storage_type: "static-corpus",
      vector_field: "keyword_score",
      embedding_model: "local-keyword-retriever",
      vector_dimensions: 0,
      distance_metric: "weighted-overlap",
      query: retrievalQuery,
      top_k: TOP_K,
      index_document_count: SEED_DOCS.length,
      retrieved_count: retrieved.length,
      retrieved_ids: retrieved.map((doc) => doc.id),
      retrieved_scores: retrieved.map((doc) => doc.score),
      verified: retrieved.length > 0,
    },
  };
}

export class RequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function optimizeTemplate(
  req: OptimizeRequest,
  retrieved: RetrievedDoc[],
  findings: ResearchFinding[],
  aspectRatio: string,
  duration: number,
): OptimizedCreative {
  const brief = req.brief.trim();
  const product = req.product?.trim() || inferProduct(brief) || "the product";
  const techniques = [...retrieved.slice(0, 5).map((doc) => doc.title), ...findings.slice(0, 3).map((finding) => finding.technique)];
  const continuity = [
    `Photorealistic ${aspectRatio} short-form product film for ${product}, exactly ${duration} seconds, authentic premium social-ad texture.`,
    "CONTINUITY: same subject, same product shape and packaging geometry, signature colors, real materials, and one consistent naturally lit setting throughout.",
  ].join(" ");
  const physicalGuardrails =
    "Stable identity and anatomy, smooth natural motion, consistent exposure and product geometry, photoreal textures, clean frame without accidental text overlays or watermark. Any physical action shows visible preparation, planted support and weight transfer, exact contact, follow-through, and balanced recovery.";

  let prompt: string;
  if (duration <= 6) {
    const revealEnd = duration - 1;
    prompt = [
      continuity,
      `0–1s HOOK: open mid-action on the strongest product behavior from this brief — ${brief} — with one instantly readable camera move and synchronized impact sound.`,
      `1–${revealEnd}s REVEAL/ACTION: show one clear human use or physical demonstration, immediately reaching the sensory peak with crisp foley and a restrained music lift.`,
      `${revealEnd}–${duration}s OUTRO: land on a clean branded hero state, product unchanged and clearly attributable, motion easing to a stable sonic finish.`,
      physicalGuardrails,
    ].join(" ");
  } else {
    const hookEnd = 2;
    const peakStart = Math.max(hookEnd + 1, Math.round(duration * 0.55));
    const finalStart = Math.min(Math.max(peakStart + 1, duration - 2), duration - 1);
    prompt = [
      continuity,
      `0–${hookEnd}s HOOK: open mid-action on the most visually arresting product behavior from this brief — ${brief} — with a fast controlled macro push-in and synchronized impact sound.`,
      `${hookEnd}–${peakStart}s SETUP/REVEAL: reveal the human use or physical demonstration with one clear action, natural performance, stable hands, lifelike contact and weight, subtle handheld energy, and coherent window light.`,
      `${peakStart}–${finalStart}s PEAK: reach the sensory and emotional peak; move closer to the key product transformation while foley becomes crisp and restrained music lifts.`,
      `${finalStart}–${duration}s OUTRO: settle into a clean branded hero state, product unchanged and clearly attributable, camera motion easing to a stable finish as the sonic logo resolves.`,
      physicalGuardrails,
    ].join(" ");
  }

  return {
    optimized_prompt: prompt,
    generation_constraints: [
      "same subject, product, packaging geometry, wardrobe, and setting throughout",
      "stable anatomy, hands, identity, exposure, and realistic physical motion",
      "biomechanically phased action with visible contact, follow-through, and recovery",
      "one product only with a clean frame and no accidental overlays or watermark",
    ],
    audio_direction:
      "Native synchronized ambience and close-mic product foley; restrained music builds into the peak and resolves with a short branded sonic ending. Use concise dialogue only if it naturally fits the duration.",
    aspect_ratio: aspectRatio,
    duration_seconds: duration,
    model: VIDEO_MODEL,
    resolution: SEEDANCE_RESOLUTION,
    sound: SEEDANCE_SOUND,
    hook: "Open on the payoff, already in motion.",
    style_tags: ["photoreal", "short-form", "cinematic", "native audio"],
    techniques_applied: techniques,
    rationale:
      "Local optimizer fallback: front-loads attention, retrieves relevant ad principles from the bundled corpus, keeps one idea per clip, designs a peak/end memory structure, and expresses Seedance guardrails as positive filmable instructions.",
  };
}

function retrieve(query: string, industry: string | undefined, k: number): RetrievedDoc[] {
  const queryTerms = tokenize(query);
  const requestedIndustry = industry && industry !== "general" ? industry : "";
  const scored = SEED_DOCS.map((doc) => {
    const docTerms = tokenize(`${doc.title} ${doc.content} ${doc.category} ${doc.industry}`);
    let overlap = 0;
    for (const term of queryTerms) {
      if (docTerms.has(term)) overlap += 1;
    }
    const industryBoost =
      requestedIndustry && doc.industry === requestedIndustry
        ? 6
        : doc.industry === "general"
          ? 2
          : 0;
    const categoryBoost = doc.category === "pika_craft" ? 2 : doc.category === "structure" ? 1 : 0;
    const raw = overlap + industryBoost + categoryBoost;
    return {
      ...doc,
      score: Math.max(0.35, Math.min(0.98, Number((0.35 + raw / 32).toFixed(4)))),
    };
  });

  return scored
    .sort((a, b) => b.score - a.score || a.title.localeCompare(b.title))
    .slice(0, k);
}

function contextBlock(retrieved: RetrievedDoc[], findings: ResearchFinding[]) {
  const lines = [
    "DURATION PRECEDENCE: retrieved examples are structural references only; the resolved target duration in the optimized creative is authoritative.",
  ];
  if (retrieved.length) {
    lines.push("RETRIEVED PRINCIPLES & REFERENCES (local RAG corpus):");
    for (const doc of retrieved) {
      lines.push(`- [${doc.category}/${doc.industry}] ${doc.title} (score ${doc.score}): ${doc.content}`);
    }
  }
  if (findings.length) {
    lines.push("\nLIVE RESEARCH — SUCCESSFUL ADS IN THIS SPACE:");
    for (const finding of findings) {
      const source = finding.source_url ? ` <${finding.source_url}>` : "";
      lines.push(`- ${finding.title}: ${finding.technique} — ${finding.why_it_worked}${source}`);
    }
  }
  return lines.join("\n");
}

function assemblePayload(creativeBlock: string, context: string) {
  return [
    SYSTEM_PROMPT,
    "",
    "=== OPTIMIZED SEEDANCE 2.0 CREATIVE ===",
    creativeBlock,
    "",
    "=== CONTEXT: RESEARCH + RAG RETRIEVAL ===",
    context,
    "",
    "=== SEEDANCE GENERATION SKILL ===",
    SEEDANCE_SKILL,
  ].join("\n");
}

function resolveDuration(brief: string, explicit: number | undefined, fallback: number) {
  if (explicit !== undefined) return validateDuration(explicit, `explicit duration ${explicit}`);
  const fromBrief = durationFromBrief(brief);
  if (fromBrief !== null) return fromBrief;
  return validateDuration(fallback, `default duration ${fallback}`);
}

function durationFromBrief(brief: string) {
  const text = brief.toLowerCase().replace(/[–—]/g, "-");
  const numberToken = "\\d{1,2}|" + Object.keys(NUMBER_WORDS).join("|");
  const seconds = "(?:seconds?|secs?|s)\\b";
  const strictLess = new RegExp(`(?:less|fewer|shorter)\\s+than\\s+(${numberToken})\\s*${seconds}|under\\s+(${numberToken})\\s*${seconds}|<\\s*(${numberToken})\\s*${seconds}`).exec(text);
  if (strictLess) {
    const raw = strictLess.slice(1).find(Boolean)!;
    return validateDuration(toInt(raw) - 1, strictLess[0]);
  }
  const atMost = new RegExp(`(?:at\\s+most|no\\s+more\\s+than|up\\s+to|max(?:imum)?)\\s+(${numberToken})\\s*${seconds}`).exec(text);
  if (atMost) return validateDuration(toInt(atMost[1]), atMost[0]);
  const strictMore = new RegExp(`(?:more|longer)\\s+than\\s+(${numberToken})\\s*${seconds}|over\\s+(${numberToken})\\s*${seconds}|>\\s*(${numberToken})\\s*${seconds}`).exec(text);
  if (strictMore) {
    const raw = strictMore.slice(1).find(Boolean)!;
    return validateDuration(toInt(raw) + 1, strictMore[0]);
  }
  const exact = new RegExp(`\\b(${numberToken})\\s*(?:-\\s*)?${seconds}`).exec(text);
  if (exact) return validateDuration(toInt(exact[1]), exact[0]);
  return null;
}

function validateDuration(seconds: number, requested: string) {
  if (!Number.isFinite(seconds) || seconds < 4 || seconds > 15) {
    throw new RequestError(`Seedance 2.0 supports 4–15 second clips; ${requested} resolves to ${seconds} seconds.`, 422);
  }
  return Math.round(seconds);
}

function tokenize(text: string) {
  const stop = new Set(["the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "are", "one", "not", "but", "all", "our", "its", "a", "an", "of", "to", "in", "on", "or", "as", "is"]);
  return new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, " ")
      .split(/\s+/)
      .filter((term) => term.length > 2 && !stop.has(term)),
  );
}

function toInt(value: string) {
  return /^\d+$/.test(value) ? Number(value) : NUMBER_WORDS[value];
}

function inferProduct(brief: string) {
  const match = /(?:for|promote|ad for|about)\s+([^,.—-]{2,48})/i.exec(brief);
  return match?.[1]?.trim();
}

function boolEnv(name: string, fallback: boolean) {
  const value = process.env[name];
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function numberEnv(name: string, fallback: number) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) ? value : fallback;
}
