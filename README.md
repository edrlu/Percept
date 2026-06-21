# Cerebra

**An interactive explorer for population-average cortical-response predictions from video.**

Cerebra turns an uploaded video into a time-scrubbable view of Meta's [TRIBE v2](https://huggingface.co/facebook/tribev2) cortical-response predictions. It pairs an anatomical WebGL cortex with frame-level response charts, cortical proxy summaries, and a compact video timeline — and closes the loop by feeding those neuro-signals back into a Redis-native engine that generates and continuously improves short-form video ads.

---

# 🔴 Redis is the brain of Cerebra

> **Submission for "Best Use of Redis."** Redis is not a cache we bolted on. It is the **entire memory, retrieval, and learning substrate** of Cerebra — the connective tissue of a closed-loop, self-improving creative engine. Pull Redis out and there is no product: no grounding, no memory, no learning, no video delivery.

Cerebra uses Redis as a **real-time context engine** across **four Redis vector indexes**, **a reward-weighted property graph**, **per-session conversation memory**, **a semantic LLM cache**, and **a binary video store** — exercising hashes, sets, sorted sets, lists, RedisVL vector search, hybrid tag-filtered search, ranking queries, and atomic reinforcement counters. One database does the work most stacks split across Pinecone + Postgres + Neo4j + a blob store + Redis.

### The whole stack on one engine

| Redis capability | Where we use it | What it powers |
|---|---|---|
| **Vector search** (RedisVL, cosine, flat) | 4 indexes: ad-knowledge, prompt-cache, graph nodes, video frames | Semantic RAG over proven ad science, cache lookup, GraphRAG entry points, natural-language video search |
| **Hybrid search** (vector + tag filters) | `industry`, `graph`, `video_id`, `objects` tags | "Beverage principles only", "frames of a person holding a phone in *this* video" |
| **Sorted sets** (`ZADD`/`ZREVRANGE`/`ZINCRBY`) | Reward-weighted graph adjacency | Beam-search graph traversal **and** the reinforcement-learning signal |
| **Atomic counters** (`HINCRBYFLOAT`) | Node performance scores | Creative patterns that win get measurably stronger over time |
| **Lists** (`RPUSH`/`LRANGE`/`LTRIM` + TTL) | Per-session working memory, global agent log | Conversation history that makes re-optimization context-aware |
| **Semantic cache** (vector distance threshold) | LLM response cache | **~40–77s → ~1s** on a similar brief; skips the Opus call entirely |
| **Binary blob store** (`SET`/`GET` bytes) | Rendered MP4s served via HTTP Range | The video player streams **directly out of Redis** |
| **Sets + hashes** (registries, dedup) | Video/object node registries, edge metadata | Idempotent re-ingestion, multi-tenancy, cross-plugin linking |

---

## 1 · Retrieval-Augmented Generation, grounded in Redis Vector Search

Every ad Cerebra generates is grounded in a **research-backed corpus of proven advertising science** (attention, memory, peak-end, brand distinctiveness, short-form structure, model-specific craft) stored as **59+ vectors** in the RedisVL index `cerebra_ad_knowledge`.

- **Query is embedded, not keyword-matched.** The brief, product, and industry are embedded with `sentence-transformers/all-MiniLM-L6-v2` (384-dim) and run through a RedisVL `VectorQuery` — **cosine** distance, **flat** index, top-k — `pipeline/redis_store.py › AdKnowledgeStore.search()`.
- **Hybrid retrieval.** Industry is applied as a RedisVL `Tag` filter (`Tag("industry") == [industry, "general"]`) so retrieval stays on-topic while still surfacing universal principles.
- **Live research compounds the index.** When enabled, Opus 4.8 web-researches winning ads in the space, structures the findings, and **upserts them back into the same Redis index** — so the knowledge base grows every time it runs (`pipeline/research.py`).
- **Provenance is auditable.** Every response carries a `RAGTrace` — endpoint, index, embedding model, dimensions, distance metric, retrieved IDs, and live cosine scores — so the UI can *prove* the creative was grounded in Redis Vector Search, not hallucinated. The Studio panel renders this as **"REDIS VECTOR RETRIEVAL"** with the real scores.

```
brief ─► embed (384-d) ─► Redis VectorQuery (cosine + tag filter) ─► top-k ad science ─► Opus 4.8 ─► optimized creative
```

## 2 · Semantic Caching — meaning, not strings

Opus optimization is slow and expensive. Cerebra fronts it with a **semantic cache** (`pipeline/redis_store.py › PromptCache`, index `cerebra_prompt_cache_verified_rag_v4`): the brief is embedded and matched against prior briefs by **cosine distance under a 0.12 threshold**. A semantically similar brief — even reworded — returns the cached, fully-assembled creative **without touching the LLM**.

- **Measured in our live demo: ~40–77s (fresh Opus call) → ~1s (cache hit).**
- Cache keys are **generation-profile-aware** (model, resolution, duration, aspect) so a cached prompt can never leak across incompatible render settings.

## 3 · Conversation Memory — a creative session that remembers

Cerebra is iterative: a brief becomes a creative, a render, a critique, a re-optimization. Redis gives that loop a memory (`pipeline/redis_store.py › SessionMemory`).

- Each turn is `RPUSH`ed to a **per-session list** `cerebra:session:{id}`, **capped** and given a **24h TTL** so sessions self-expire.
- Prior turns are replayed into Opus's context ("CONVERSATION SO FAR…"), so refinements build on history instead of starting cold.
- A global **agent-memory log** (`cerebra:runs`, `LPUSH` + `LTRIM` to 500) records every optimization across all sessions — long-term institutional memory for the system.

## 4 · The video itself lives in Redis

Stage 2 renders 1080p audio-video through Seedance 2.0. Redis is the delivery layer:

- **Prompt→URL generation cache** (`cerebra:gen:seedance2:{hash}`) means an identical creative never re-renders.
- **Full MP4 bytes are stored in Redis** (`cerebra:vid:{hash}`) and streamed to the browser **with HTTP Range support straight from Redis** — the `<video>` element scrubs against Redis-backed bytes, with CDN self-healing if a key is evicted (`pipeline/pika.py`).

---

## 🧩 Two Redis-native MCP plugins

We packaged Cerebra's most novel Redis work as **two installable MCP servers** — so any agent (Claude included) can use Redis as a graph + vision substrate.

### Plugin 1 — **Percept Context**: GraphRAG on Redis

A **reward-weighted property graph** built entirely from Redis primitives — no graph database. It treats Redis as a learning knowledge graph of creative strategy.

- **Nodes** are a RedisVL vector index (`percept_nodes`, 384-dim, cosine) — every principle, technique, video, and object is semantically searchable.
- **Edges** are **sorted sets**: `percept:adj:{graph}:out:{src}:{type}` with the edge weight as the score. Traversal is a `ZREVRANGE` (highest-weight neighbors first) — beam search in one command. A reverse `in:` index makes it bidirectional; a `SADD` edge-type registry enumerates relations.
- **GraphRAG retrieval** = vector search for entry nodes → multi-hop sorted-set traversal → assembled subgraph context (`graph_rag_query`, `compose_brief`).
- **It learns.** `record_outcome(path, reward)` walks a winning creative's path and does `ZINCRBY` on every edge and `HINCRBYFLOAT` on every node score. **Strategies that produce high-performing ads literally gain weight in Redis**, so `top_performers` (a RedisVL `FilterQuery` sorted by score) surfaces what actually works. This is where the TRIBE v2 neuro-score closes the loop — the reward signal that reinforces the graph.
- **Multi-tenant** by a `graph` tag, so isolated projects share one Redis without collision.

> MCP tools: `graph_rag_query`, `search_nodes`, `add_node`, `link_nodes`, `neighbors`, `record_outcome`, `top_performers`, `graph_stats`, `compose_brief`, `seed_demo_graph`.

### Plugin 2 — **Percept Vision**: video understanding as Redis vectors

Turns any video into **searchable moments** in Redis.

- Frames are sampled, **YOLO**-detected for objects, and **CLIP ViT-B-32** embedded (512-dim) into the RedisVL index `percept_frames`.
- **Natural-language moment search** is a `VectorQuery` over frame embeddings with **hybrid tag filters** on `video_id` and pipe-separated `objects` — e.g. *"the moment someone smiles holding the can"* returns timestamps, deep-links (`#t=seconds`), thumbnails, and similarity scores (`search_moments`, `ask_video`).
- Video metadata (`pv:video:{id}`), an object-count hash, and a `pv:videos` **set** registry round out the store.
- **Cross-plugin magic:** ingestion writes **video** and **object** nodes into Percept Context's graph and links them with `CONTAINS` edges weighted by detection count — deduped through Redis hash registries. So GraphRAG over creative strategy can now reason over **what's actually on screen** in real footage. Two plugins, one Redis graph.

> MCP tools: `ingest_video`, `search_moments`, `ask_video`, `list_video_objects`, `list_videos`, `vision_stats`.

---

## Why this is the Best Use of Redis

Cerebra is a **closed neuro-optimized loop** and **Redis is every link in it**:

```
brief ─► Redis Vector RAG (ad science) ─► Opus creative ─► Seedance render (stored in Redis)
   ▲                                                                      │
   │                                                                      ▼
Redis reward-weighted graph ◄─ TRIBE v2 neuro-score ◄─ Percept Vision (Redis frame vectors)
   (ZINCRBY / HINCRBYFLOAT: the system gets smarter every cycle)
```

- **Breadth:** vector search, hybrid search, sorted-set graph traversal, atomic reinforcement counters, lists + TTL, sets, hashes, and binary blob streaming — in one app.
- **Depth:** a *learning* graph (sorted sets as a trainable weight matrix) and a *semantic* cache that turns 40–77s into 1s.
- **Creativity:** Redis as a GraphRAG engine **and** a video frame store **and** a self-improving knowledge graph — wrapped as reusable MCP plugins.
- **Verifiable:** every generation ships a Redis `RAGTrace` proving the grounding is real.

Quick proof, live:

```bash
# RAG is real (cosine scores from Redis, not keyword matching)
curl -s localhost:8100/optimize -d '{"brief":"15s cold-brew ad","industry":"beverage","session_id":"s1"}' \
  -H 'content-type: application/json' | python3 -m json.tool   # → rag.backend: "redis"

# Conversation memory is in Redis
redis-cli LRANGE cerebra:session:s1 0 -1     # the turns
redis-cli TTL   cerebra:session:s1           # 24h auto-expiry

# Semantic cache hit (same call again returns cached:true in ~1s)
redis-cli FT.INFO cerebra_prompt_cache_verified_rag_v4 | grep num_docs
```

## What it includes

- Video upload, playback, and frame-accurate timeline scrubbing
- Live WebGL view of the fsaverage5 pial cortical surface
- Four explicitly labelled cortical surface-proxy summaries
- Response charts, proxy breakdowns, and a model-provenance audit strip
- Multiple visual colour schemes

## How it works

With the local worker running, Cerebra sends an uploaded video to `facebook/tribev2`. The model extracts video, audio, and language features and returns predicted average-subject fMRI-style responses on the fsaverage5 cortical mesh. The worker then aggregates the surface output over four manually defined display regions for the browser.

The interface also works without the worker as a clearly labelled visual preview using synthetic data.

## Run locally

### One-command setup

Requirements: Node.js 20+, Python 3.11 or 3.12, and `ffmpeg`.

```bash
chmod +x run.sh
./run.sh
```

The launcher creates a local Python environment, installs worker dependencies as needed, finds free ports, starts the TRIBE v2 worker and Next.js app, and connects them automatically.

TRIBE v2's language feature path can require access to Meta's gated `meta-llama/Llama-3.2-3B` model. Add a Hugging Face read token if needed:

```bash
HF_TOKEN=hf_your_token ./run.sh
```

For a persistent local setup, put `HF_TOKEN` in `.env.local`; that file is ignored by Git.

### Run the frontend only

```bash
npm install
npm run dev
```

Without `TRIBEV2_API_URL`, the UI remains usable in visual-preview mode.

### Run the worker in Docker

```bash
cd worker
docker build -t cerebra-tribev2 .
docker run --rm -p 8000:8000 \
  -e HF_TOKEN=your_huggingface_read_token \
  -v tribev2-cache:/data/cache cerebra-tribev2
```

Then set `TRIBEV2_API_URL=http://localhost:8000` in `.env.local` and restart the frontend.

## Scientific scope

TRIBE v2 predicts **population-average cortical responses** to naturalistic stimuli. Cerebra's four surface regions are manually defined display proxies. They are not direct measurements of emotion, reward, desire, intent, self-relevance, memory encoding, subcortical activity, an individual viewer's mental state, or health.

Cerebra is a research/visualization interface—not an fMRI scanner, diagnostic tool, or behavioral truth machine.

## Development checks

```bash
npm run lint
npm run build
```
