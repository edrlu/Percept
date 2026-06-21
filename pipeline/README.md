# Cerebra Stage 1 — Research-backed RAG Prompt Optimizer (Redis)

The **first stage of the Cerebra loop**. It turns a user's prompt (voice or
text) into a fully-optimized, evidence-backed payload for the video model —
ready for Stage 2 (Pika-hosted Seedance 2.0 generation) and TRIBE v2 scoring
loop. **It does not call Pika.**

```
 user prompt (voice/text)
        │
        ▼
 ┌─────────────────────────────────────────────────────────┐
 │ STAGE 1  (this package)                                  │
 │                                                          │
 │  intake ─▶ Redis vector retrieval (RAG) ─▶ live research │
 │             │  research-backed ad corpus    (web search) │
 │             ▼                                            │
 │      LLM prompt optimization (Claude Opus 4.8)           │
 │             ▼                                            │
 │  assemble: Seedance SYSTEM + CONTEXT + generation skill  │
 └─────────────────────────────────────────────────────────┘
        │  video_model_payload
        ▼
 Stage 2: Seedance 2.0 generates native audio-video → TRIBE v2 scores it → re-optimize
```

The output payload, by construction, is:

```
Seedance 2.0 SYSTEM prompt + CONTEXT (research + vector retrieval) + generation skill
```

## Redis, beyond caching (one connection, three jobs)

This is the part that qualifies for the Redis prize — Redis as a real-time
context engine, not a key/value cache:

| Capability | What it does | Where |
|---|---|---|
| **Vector search (RAG)** | RedisVL index over a research-backed corpus of proven ad patterns; hybrid-filtered by industry. | `AdKnowledgeStore` |
| **Semantic prompt cache** | LangCache pattern — a near-identical brief returns the prior result instead of re-running the LLM. | `PromptCache` |
| **Agent memory** | Append-only log of every optimization run for later stages / future sessions. | `AgentMemory` |

Embeddings are local and free (`sentence-transformers/all-MiniLM-L6-v2` via
RedisVL's HuggingFace vectorizer) — no embedding API key required.

## Research-backed knowledge

The seed corpus (`knowledge/seed_corpus.py`) distills advertising-effectiveness
research (Binet & Field / IPA, Ehrenberg-Bass), attention/neuro findings, and
the three reference films in `/downloads` (Coca-Cola *Masterpiece*, Coca-Cola
*For Everyone*, Apple *iPad Pro — Float*). The live-research module
(`research.py`) does web search on successful ads in the product's space and
**caches its findings into the same Redis index** (`source="research"`), so
retrieval keeps improving.

## Setup

```bash
cd pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start Redis Stack (vector search). Local Docker:
#   docker run -p 6379:6379 redis/redis-stack:latest
# or use Redis Cloud and set REDIS_URL.

# Optional, for the fully-optimized + live-research paths:
#   export ANTHROPIC_API_KEY=sk-ant-...   (or put it in ../.env.local)

python -m pipeline.seed          # build indices + load the corpus (idempotent)
```

### Configuration (env, all optional)

| Var | Default | Purpose |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis Stack / Redis Cloud connection |
| `REDIS_HOST` / `REDIS_PORT` | — | Workshop-style Redis Cloud endpoint |
| `REDIS_USER` | `default` | Workshop-style Redis Cloud username |
| `REDIS_PASSWORD` | — | Redis Cloud database password |
| `ANTHROPIC_API_KEY` | — | Enables the LLM optimizer + live research (template fallback without it) |
| `CEREBRA_LLM_MODEL` | `claude-opus-4-8` | Optimization model |
| `CEREBRA_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Local embedding model |
| `CEREBRA_TOP_K` | `6` | Retrieved docs per request |
| `CEREBRA_USE_CACHE` | `true` | Semantic prompt cache on/off |
| `CEREBRA_DURATION` | `10` | Default only when the brief contains no duration |
| `CEREBRA_VIDEO_MODEL` | `seedance-2.0` | Model identity used for cache isolation |
| `CEREBRA_SEEDANCE_BACKEND` | `ark` | Pika Seedance backend |
| `CEREBRA_SEEDANCE_RESOLUTION` | `1080p` | Output resolution |
| `CEREBRA_SEEDANCE_FAST` | `false` | Use the cheaper 720p-capped fast tier |
| `CEREBRA_SEEDANCE_SOUND` | `true` | Generate native synchronized audio |

## Run

```bash
# One-shot CLI:
python -m pipeline.cli "a 15s ad for a cold-brew coffee can, gen-z, energetic" --industry beverage
python -m pipeline.cli "promote my AI note-taking app" --industry saas --research

# Or the HTTP service (mirrors worker/):
uvicorn pipeline.app:app --port 8100
```

### Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | — | readiness + whether the LLM path is live |
| POST | `/optimize` | `OptimizeRequest` JSON | `OptimizeResponse` (incl. `video_model_payload`) |
| POST | `/transcribe` | multipart `audio` | `{ text }` (needs `faster-whisper`; or use browser Web Speech) |
| POST | `/research/refresh` | `OptimizeRequest` JSON | live research findings cached to Redis |
| GET | `/memory?n=20` | — | recent optimization runs |

### From the Next.js app

The Studio tab now runs its prompt-optimization path in-process through
`/api/optimize`, using the bundled ad-knowledge corpus. Running this FastAPI
service is optional for pipeline experiments and is no longer required for the
Studio button to work.

## Degradation

- **No `ANTHROPIC_API_KEY`** → deterministic template optimizer (offline, runnable; less creative).
- **No live web access** → seed corpus only; `live_research` is a no-op.
- **No `faster-whisper`** → `/transcribe` returns a 503 pointing you to browser Web Speech.

Redis is required (the retrieval store). Everything else degrades gracefully.
