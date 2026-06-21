# TRIBE Scoring of Regenerated Takes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score each of the 3 regenerated takes individually against the original video's saved cortical baseline, show the scores + best pick in the "Choose a take" picker, and on selection replace both the video and the original engagement graph for that segment.

**Architecture:** A new worker endpoint z-scores each take's raw TRIBE predictions against the original's persisted per-vertex baseline (`μ/σ`), so the 3 takes are comparable to each other and to the original without re-scoring it. A new Next route gateways the run's take mp4s to that endpoint and caches the result. The client triggers scoring once all 3 takes finish generating, displays per-take headline (mean of 4 factors) + factor breakdown + best badge, and on "Use this take" splices the take's per-frame series into `analysis`.

**Tech Stack:** Next.js 16.2.9 (App Router, Node runtime), React 19, TypeScript 5, Python FastAPI worker (`facebook/tribev2`), numpy.

## Global Constraints

- **Modified Next.js:** `AGENTS.md`/`CLAUDE.md` — before writing route-handler code, read the relevant guide in `node_modules/next/dist/docs/` and heed deprecations. Do not assume training-data API shapes.
- **Route handler pattern (verbatim from existing routes):** `export const runtime = "nodejs";`, `export const maxDuration = <n>;`, `export async function POST(request: Request)`.
- **Worker reachability:** the Next process reaches the worker via `process.env.TRIBEV2_API_URL` (no new env var). 503 if unset.
- **No new dependencies.** Only stdlib + already-installed packages (numpy is already a worker dep; React/Next already present).
- **Score scale:** TRIBE families map to 0–100 where 50 = baseline. Per-take **headline = mean of the 4 family ("factor") scores**; **best take = highest headline**.
- **Verification tooling:** there is no JS test runner. Client/route verification = `npx tsc --noEmit` + `npm run lint` + `npm run build`. Worker scoring math has a model-free numpy unit test; full inference is verified by curl smoke test against a running worker (GPU).

## File Structure

- `worker/app.py` — **modify**: `build_response` gains an optional `ref` arg; add `reference_stats()`, persist `μ/σ` + `referenceId` on `/predict`; add `/score_takes` endpoint.
- `worker/test_scoring.py` — **create**: model-free numpy tests for the reference math + npz round-trip.
- `app/api/regenerate/score/route.ts` — **create**: gateway that posts the run's take mp4s + `referenceId` to the worker `/score_takes`, caches `data/<runId>/scores.json`.
- `app/api/regenerate/complete/route.ts` — **modify**: drop the filler scorer call (keep take mp4 archiving).
- `app/lib/regen.ts` — **modify**: remove `scoreArchivedTakeMp3`; update `RegenJob.score` doc comment.
- `app/page.tsx` — **modify**: types (`Analysis.referenceId`, `RegenVariant.factors/series`, `RegenJobState` run fields), capture `referenceId`, store `runId`, `scoreBatch()` trigger + gating, picker UI (headline/factors/best/average/scoring/reject-all), `chooseVariant` graph splice + `spliceTakeIntoAnalysis`.
- `app/globals.css` — **modify**: styles for the new picker elements.

---

### Task 1: Worker — reference baseline math + persistence

**Files:**
- Modify: `worker/app.py` (`build_response` ~line 324; `/predict` ~lines 532-547)
- Test: `worker/test_scoring.py` (create)

**Interfaces:**
- Produces: `reference_stats(predictions: np.ndarray) -> tuple[np.ndarray, np.ndarray]` (per-vertex `mu`, `sd`); `build_response(predictions, ref: tuple[np.ndarray, np.ndarray] | None = None) -> dict`; `/predict` JSON now includes `"referenceId": <sha256 hex>`; sidecar `prediction_cache/<sha256>.ref.npz` with arrays `mu`, `sd`.

- [ ] **Step 1: Write the failing test** — create `worker/test_scoring.py`

```python
"""Model-free tests for the fixed-reference scoring math (no GPU / no TRIBE model)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import reference_stats  # noqa: E402


def test_reference_stats_are_per_vertex_mean_and_sd():
    pred = np.array([[0.0, 10.0], [2.0, 10.0], [4.0, 10.0]])  # (T=3, V=2)
    mu, sd = reference_stats(pred)
    assert np.allclose(mu, [2.0, 10.0])
    assert np.allclose(sd[0], np.std([0.0, 2.0, 4.0]))
    assert sd[1] >= 1e-6  # zero-variance vertex is floored, never divides by 0


def test_self_reference_centers_at_zero_other_reference_shifts():
    take = np.array([[5.0], [7.0], [9.0]])           # mean 7
    own_mu, own_sd = reference_stats(take)
    z_self = ((take - own_mu) / own_sd).mean()
    assert abs(z_self) < 1e-9                          # vs itself -> centered (~50 after scaling)
    orig = np.array([[1.0], [3.0], [5.0]])             # original baseline, mean 3
    ref_mu, ref_sd = reference_stats(orig)
    z_vs_orig = ((take - ref_mu) / ref_sd).mean()
    assert z_vs_orig > 0.5                             # the take sits clearly above the original


def test_npz_roundtrip(tmp_path=Path("/tmp")):
    mu = np.arange(20484, dtype=np.float64)
    sd = np.ones(20484, dtype=np.float64)
    f = Path(tmp_path) / "_ref_roundtrip.npz"
    np.savez(f, mu=mu, sd=sd)
    data = np.load(f)
    assert np.array_equal(data["mu"], mu) and np.array_equal(data["sd"], sd)
    f.unlink()


if __name__ == "__main__":
    test_reference_stats_are_per_vertex_mean_and_sd()
    test_self_reference_centers_at_zero_other_reference_shifts()
    test_npz_roundtrip()
    print("OK: all scoring-math tests passed")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && .venv/bin/python worker/test_scoring.py`
Expected: FAIL — `ImportError: cannot import name 'reference_stats' from 'app'`.

- [ ] **Step 3: Add `reference_stats` and make `build_response` reference-aware**

In `worker/app.py`, immediately **above** `def build_response(` (line 324), insert:

```python
def reference_stats(predictions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-vertex temporal mean/SD over a clip — the within-video baseline.
    Persisted per scored video so later clips (regenerated takes) can be
    z-scored against THIS video's baseline instead of their own, making their
    engagement scores comparable across clips (the fixed reference the model
    authors deferred). SD is floored to avoid divide-by-zero on flat vertices."""
    pred = np.asarray(predictions, dtype=np.float64)
    mu = pred.mean(axis=0)
    sd = pred.std(axis=0)
    sd[sd < 1e-6] = 1e-6
    return mu, sd
```

Then change the `build_response` signature and the `mu`/`sd`/`z` block. Replace lines 324 and 336-343:

```python
def build_response(predictions: np.ndarray, ref: tuple[np.ndarray, np.ndarray] | None = None) -> dict:
```

and replace the per-vertex baseline block (originally):

```python
    pred = np.asarray(predictions, dtype=np.float64)
    n_frames = int(pred.shape[0])

    # Per-vertex temporal baseline (signed): (value - vertex mean) / vertex SD.
    mu = pred.mean(axis=0, keepdims=True)
    sd = pred.std(axis=0, keepdims=True)
    sd[sd < 1e-6] = 1e-6
    z = (pred - mu) / sd  # (T, V) signed; > 0 means above this vertex's baseline
```

with:

```python
    pred = np.asarray(predictions, dtype=np.float64)
    n_frames = int(pred.shape[0])

    # Per-vertex baseline (signed z): (value - mean) / SD. When `ref` is given we
    # z-score against ANOTHER video's baseline (the original), so this clip's
    # engagement is measured relative to the original rather than to itself.
    if ref is None:
        mu = pred.mean(axis=0, keepdims=True)
        sd = pred.std(axis=0, keepdims=True)
    else:
        mu = np.asarray(ref[0], dtype=np.float64).reshape(1, -1)
        sd = np.asarray(ref[1], dtype=np.float64).reshape(1, -1)
    sd = np.where(sd < 1e-6, 1e-6, sd)
    z = (pred - mu) / sd  # (T, V) signed; > 0 means above the (reference) baseline
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && .venv/bin/python worker/test_scoring.py`
Expected: `OK: all scoring-math tests passed`

- [ ] **Step 5: Persist `μ/σ` + `referenceId` on `/predict`**

In `worker/app.py`, in the `predict` compute path, replace the block (originally lines 532, 541-547):

```python
            result = build_response(predictions)
```
...
```python
        result["cached"] = False
        try:
            cache_file.write_text(json.dumps(result))
            print(f"[cache] saved {digest[:12]} -> {cache_file}")
        except Exception as exc:
            print(f"[cache] failed to save {cache_file.name}: {exc}")
        return result
```

with (note the added `reference_stats` save + `referenceId`):

```python
            result = build_response(predictions)
```
...
```python
        result["referenceId"] = digest
        result["cached"] = False
        try:
            mu, sd = reference_stats(predictions)
            np.savez(PREDICTIONS_DIR / f"{digest}.ref.npz", mu=mu, sd=sd)
        except Exception as exc:
            print(f"[cache] failed to save reference {digest[:12]}: {exc}")
        try:
            cache_file.write_text(json.dumps(result))
            print(f"[cache] saved {digest[:12]} -> {cache_file}")
        except Exception as exc:
            print(f"[cache] failed to save {cache_file.name}: {exc}")
        return result
```

- [ ] **Step 6: Sanity-check the import surface compiles**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && .venv/bin/python -c "import ast; ast.parse(open('worker/app.py').read()); print('app.py parses')"`
Expected: `app.py parses`

- [ ] **Step 7: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add worker/app.py worker/test_scoring.py
git commit -m "feat(worker): fixed-reference scoring math + persist per-video baseline"
```

---

### Task 2: Worker — `/score_takes` endpoint

**Files:**
- Modify: `worker/app.py` (add endpoint after `/predict`, ~line 547)

**Interfaces:**
- Consumes: `reference_stats`, `build_response(predictions, ref)`, `_maybe_downscale`, `model` (from Task 1 + existing).
- Produces: `POST /score_takes` — multipart `referenceId` (form) + `takes` (1+ files, filename `take_<n>.mp4`). Returns `{ "best": int, "average": float, "takes": [{ "takeIndex": int, "score": float, "factors": {"AUD":float,"LANG":float,"ATTN":float,"VIS":float}, "series": {"global":[...],"AUD":[...],"LANG":[...],"ATTN":[...],"VIS":[...]} }] }`. `takeIndex` is `<n>-1` parsed from the filename.

- [ ] **Step 1: Add `re` to imports**

In `worker/app.py`, add `import re` to the stdlib import block (after `import os`, line 9):

```python
import os
import re
```

- [ ] **Step 2: Add reference loader + single-take scorer + endpoint**

In `worker/app.py`, immediately **after** the `predict` function (after its final `return result`, ~line 547), insert:

```python
def _load_reference(reference_id: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Load the persisted per-vertex baseline for an already-scored video."""
    safe = "".join(c for c in reference_id if c in "0123456789abcdefABCDEF")
    ref_file = PREDICTIONS_DIR / f"{safe}.ref.npz"
    if not ref_file.exists():
        return None
    data = np.load(ref_file)
    return data["mu"], data["sd"]


def _score_against_reference(video_path: Path, ref: tuple[np.ndarray, np.ndarray]) -> dict:
    """Run TRIBE on one clip and score it against the given reference baseline."""
    infer_path = _maybe_downscale(video_path)
    events = model.get_events_dataframe(video_path=str(infer_path))
    predictions, _ = model.predict(events, verbose=False)
    return build_response(predictions, ref=ref)


@app.post("/score_takes")
async def score_takes(referenceId: str = Form(...), takes: list[UploadFile] = File(...)):
    """Score regenerated takes against an already-scored ORIGINAL video.

    The original is NOT re-encoded: we reuse the per-vertex baseline saved by
    /predict (keyed by `referenceId` = the original's sha256). Each take is
    z-scored against that baseline, so the takes are directly comparable to one
    another and to the original. Returns one headline (mean of the 4 families)
    and the per-frame series per take, plus the best take index."""
    if model is None:
        raise HTTPException(status_code=503, detail="Model is still loading.")
    ref = _load_reference(referenceId)
    if ref is None:
        raise HTTPException(status_code=409, detail="Unknown referenceId — score the original first.")
    if not takes:
        raise HTTPException(status_code=400, detail="No takes supplied.")

    out: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="score-takes-") as tmp:
        for upload_index, up in enumerate(takes):
            match = re.search(r"take_(\d+)", up.filename or "")
            take_index = int(match.group(1)) - 1 if match else upload_index
            suffix = Path(up.filename or "take.mp4").suffix.lower() or ".mp4"
            dest = Path(tmp) / f"take_{upload_index}{suffix}"
            with dest.open("wb") as fh:
                while chunk := await up.read(1024 * 1024):
                    fh.write(chunk)
            await up.close()
            try:
                resp = _score_against_reference(dest, ref)
            except Exception as exc:
                import traceback
                print("[score_takes] inference FAILED:\n" + traceback.format_exc(), flush=True)
                raise HTTPException(status_code=500, detail=f"Take scoring failed: {exc}") from exc
            out.append({
                "takeIndex": take_index,
                "score": resp["engagementScore"],
                "factors": {r["short"]: r["score"] for r in resp["regions"]},
                "series": {"global": resp["global"], **{r["short"]: r["values"] for r in resp["regions"]}},
            })

    best = max(range(len(out)), key=lambda i: out[i]["score"])
    average = round(sum(t["score"] for t in out) / len(out), 1)
    return {"best": out[best]["takeIndex"], "average": average, "takes": out}
```

- [ ] **Step 3: Verify the module parses**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && .venv/bin/python -c "import ast; ast.parse(open('worker/app.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Smoke-test against a running worker (requires the GPU worker up)**

Prereq: worker running and an original already scored so its `.ref.npz` exists. Get a `referenceId` from a prior `/predict` (its `referenceId` field), and have two short mp4s.

Run:
```bash
REF=<sha256-from-a-prior-predict>
curl -s -X POST "$TRIBEV2_API_URL/score_takes" \
  -F "referenceId=$REF" \
  -F "takes=@/path/take_1.mp4;type=video/mp4" \
  -F "takes=@/path/take_2.mp4;type=video/mp4" | python -m json.tool
```
Expected: JSON with `best`, `average`, and a `takes` array; each take has `takeIndex` (0,1), a `score`, `factors` with AUD/LANG/ATTN/VIS, and `series.global`. If the worker isn't available, mark this step done after the parse check (Step 3) and note it for the integration pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add worker/app.py
git commit -m "feat(worker): /score_takes endpoint scoring takes vs original baseline"
```

---

### Task 3: Next — `/api/regenerate/score` gateway route

**Files:**
- Create: `app/api/regenerate/score/route.ts`

**Interfaces:**
- Consumes: worker `/score_takes` (Task 2); `dataDir`, `fileExists` from `@/app/lib/regen`.
- Produces: `POST /api/regenerate/score` — JSON body `{ runId: string, referenceId: string }`. Reads `data/<runId>/take_1..3.mp4`, posts them to the worker, caches `data/<runId>/scores.json`, returns the worker payload `{ best, average, takes }`.

- [ ] **Step 1: Consult the modified-Next route-handler guide**

Run: `ls /Users/adrianm/berkeleyhack/Cerebra/node_modules/next/dist/docs/ && grep -rl "route" /Users/adrianm/berkeleyhack/Cerebra/node_modules/next/dist/docs/ | head`
Read the route-handler / `maxDuration` guidance to confirm the `runtime`/`maxDuration`/`POST(request: Request)` shape used below matches this Next version. (Existing routes — `app/api/predict/route.ts` — already use exactly this shape, so it is the source of truth.)

- [ ] **Step 2: Create the route**

Create `app/api/regenerate/score/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { dataDir, fileExists } from "@/app/lib/regen";

export const runtime = "nodejs";
// Scoring runs up to 3 TRIBE inferences; a cold GPU can take minutes. Mirror the
// predict gateway's long budget so the UI waits instead of timing out.
export const maxDuration = 14400;

const VARIANT_COUNT = 3;

/**
 * POST /api/regenerate/score — score a run's 3 takes against the original.
 * Body: { runId, referenceId }. Reads data/<runId>/take_<n>.mp4, forwards them
 * to the worker /score_takes (which reuses the original's saved baseline), and
 * caches the result so reopening the picker is instant.
 */
export async function POST(request: Request) {
  const workerUrl = process.env.TRIBEV2_API_URL;
  if (!workerUrl) {
    return NextResponse.json(
      { error: "No TRIBE v2 worker configured. Set TRIBEV2_API_URL to enable scoring." },
      { status: 503 },
    );
  }

  let body: { runId?: string; referenceId?: string };
  try { body = await request.json(); }
  catch { return NextResponse.json({ error: "Expected JSON { runId, referenceId }" }, { status: 400 }); }
  const { runId, referenceId } = body;
  if (!runId || !referenceId) {
    return NextResponse.json({ error: "Missing runId or referenceId" }, { status: 400 });
  }

  const ddir = dataDir(runId);
  const scoresFile = path.join(ddir, "scores.json");
  if (await fileExists(scoresFile)) {
    try { return NextResponse.json(JSON.parse(await readFile(scoresFile, "utf8"))); }
    catch { /* unreadable cache: recompute */ }
  }

  const form = new FormData();
  form.append("referenceId", referenceId);
  let count = 0;
  for (let n = 1; n <= VARIANT_COUNT; n++) {
    const p = path.join(ddir, `take_${n}.mp4`);
    if (!(await fileExists(p))) continue;
    const bytes = await readFile(p);
    form.append("takes", new Blob([bytes], { type: "video/mp4" }), `take_${n}.mp4`);
    count++;
  }
  if (count === 0) return NextResponse.json({ error: "No takes found for this run" }, { status: 404 });

  try {
    const upstream = await fetch(`${workerUrl.replace(/\/$/, "")}/score_takes`, {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(14_400_000),
    });
    const data = await upstream.json();
    if (!upstream.ok) return NextResponse.json(data, { status: upstream.status });
    try { await writeFile(scoresFile, JSON.stringify(data)); } catch { /* best-effort cache */ }
    return NextResponse.json(data);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Scoring worker unavailable";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
```

- [ ] **Step 3: Typecheck**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && npx tsc --noEmit`
Expected: no errors (exit 0).

- [ ] **Step 4: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add app/api/regenerate/score/route.ts
git commit -m "feat(api): /api/regenerate/score gateway to worker /score_takes"
```

---

### Task 4: Next — remove the filler scorer from `/complete` and `regen.ts`

**Files:**
- Modify: `app/api/regenerate/complete/route.ts` (imports line 4; scoring block lines 36-72)
- Modify: `app/lib/regen.ts` (remove `scoreArchivedTakeMp3` lines 249-256; `RegenJob.score` doc lines 38-40)

**Interfaces:**
- Produces: `/complete` no longer computes or returns `score`; take mp4 archiving is retained (the scorer reads `data/<runId>/take_<n>.mp4`). `RegenJob.score` stays on the type (set later by the client from `/api/regenerate/score`), with an updated comment.

- [ ] **Step 1: Drop the import of the stub + the now-unused mp3 helper in `/complete`**

In `app/api/regenerate/complete/route.ts` line 4, change:

```typescript
import { appendJobLog, dataDir, extractAudioMp3, jobDir, mergeReplace, readJob, readJobLogTail, scoreArchivedTakeMp3, sourceDir, writeJob } from "@/app/lib/regen";
```
to:
```typescript
import { appendJobLog, dataDir, jobDir, mergeReplace, readJob, readJobLogTail, sourceDir, writeJob } from "@/app/lib/regen";
```

- [ ] **Step 2: Replace the archive + scoring block**

Replace lines 36-72 (the `archivedMp3` block through the success `return`) with — archive only the take mp4 (no mp3, no scorer), merge, mark done without a score:

```typescript
  // Archive this take's generated clip alongside the run's floor clip. The take
  // mp4 is what /api/regenerate/score later sends to the TRIBE worker. Scoring is
  // no longer done here: it runs once per run after all takes finish generating.
  if (job.runId) {
    try {
      const ddir = dataDir(job.runId);
      await mkdir(ddir, { recursive: true });
      const take = `take_${(job.takeIndex ?? 0) + 1}`;
      await copyFile(clipPath, path.join(ddir, `${take}.mp4`));
      await appendJobLog(jobId, `archived generated take → data/${job.runId}/${take}.mp4`);
    } catch (err) {
      await appendJobLog(jobId, `WARN: could not archive take to data/${job.runId}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  await writeJob({ ...job, status: "merging", stage: "merge" });
  try {
    const t = Date.now();
    await mergeReplace(source, clipPath, job.startSec, job.endSec, final, { jobId });
    await writeJob({ ...job, status: "done", stage: "complete" });
    await appendJobLog(jobId, `/complete merge OK in ${((Date.now() - t) / 1000).toFixed(1)}s; final=${final}`);
    console.log(`[regen-api ${new Date().toISOString()}] /complete ${jobId} · merge OK in ${((Date.now() - t) / 1000).toFixed(1)}s → done`);
    return NextResponse.json({ ok: true, downloadUrl: `/api/regenerate/file?job=${jobId}&name=final.mp4` });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Merge failed";
    await writeJob({ ...job, status: "error", stage: "merge", error: message });
    await appendJobLog(jobId, `/complete MERGE FAILED: ${message}`);
    console.error(`[regen-api ${new Date().toISOString()}] /complete ${jobId} · MERGE FAILED: ${message}`);
    return NextResponse.json({ error: message, logTail: await readJobLogTail(jobId) }, { status: 500 });
  }
```

- [ ] **Step 3: Remove `scoreArchivedTakeMp3` from `regen.ts` and update the `score` doc**

In `app/lib/regen.ts`, delete the entire `scoreArchivedTakeMp3` block (lines 249-256, the doc comment + function). The `extractAudioMp3` helper above it stays (it's still exported even if currently unused; leaving it avoids churn — do NOT delete it).

Then update the `RegenJob.score` comment (lines 38-40) from:

```typescript
  // Temporary clip-quality label from the local filler scorer. This is kept on
  // the job so polling clients can display the result as soon as it is ready.
  score?: number;
```
to:
```typescript
  // TRIBE engagement headline (mean of the 4 families), referenced to the
  // original. Set by the client from /api/regenerate/score after the run's
  // takes finish generating — not by /complete.
  score?: number;
```

- [ ] **Step 4: Typecheck + lint**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && npx tsc --noEmit && npm run lint`
Expected: no errors. (Lint will flag `extractAudioMp3` only if it enforces no-unused-exports — it does not for exported members; if lint complains about an unused import you missed, remove it.)

- [ ] **Step 5: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add app/api/regenerate/complete/route.ts app/lib/regen.ts
git commit -m "refactor(regen): remove filler scorer; scoring moves to a run-level pass"
```

---

### Task 5: Client — types, `referenceId` capture, `runId` storage

**Files:**
- Modify: `app/page.tsx` (types lines 8-38; `regenerate` line 618)

**Interfaces:**
- Produces: `Analysis.referenceId?: string`; `type Factors`; `type TakeSeries`; `RegenVariant.factors?/series?`; `RegenJobState` gains `runId?/scoring?/scored?/best?/average?`. `analysis.referenceId` is populated from the `/api/predict` response; `regenJobs[key].runId` is set when a batch starts.

- [ ] **Step 1: Extend the `Analysis` type with `referenceId`**

In `app/page.tsx`, change the `Analysis` type (lines 9-17) to add the field:

```typescript
type Analysis = {
  duration: number;
  frames: number;
  regions: Region[];
  global: number[];
  peak: { time: number; label: string; value: number };
  source: "demo" | "model";
  cognitiveSeries?: Record<string, number[]>;
  referenceId?: string; // sha256 of the scored original; lets takes be scored against it
};
```

- [ ] **Step 2: Add `Factors`/`TakeSeries` and extend `RegenVariant` + `RegenJobState`**

Replace the `RegenVariant`/`RegenJobState` declarations (lines 37-38) with:

```typescript
type Factors = { AUD: number; LANG: number; ATTN: number; VIS: number };
type TakeSeries = { global: number[]; AUD: number[]; LANG: number[]; ATTN: number[]; VIS: number[] };
type RegenVariant = { jobId?: string; status: RegenStatus; clipUrl?: string; downloadUrl?: string; logUrl?: string; logTail?: string; error?: string; startedAt?: number; score?: number; factors?: Factors; series?: TakeSeries };
type RegenJobState = { variants: RegenVariant[]; runId?: string; scoring?: boolean; scored?: boolean; best?: number; average?: number };
```

- [ ] **Step 3: Store `runId` on the batch when regeneration starts**

In `app/page.tsx` `regenerate()`, change the seed line (618) from:

```typescript
    setRegenJobs((j) => ({ ...j, [key]: { variants: Array.from({ length: VARIANT_COUNT }, () => ({ status: "extracting" as RegenStatus })) } }));
```
to:
```typescript
    setRegenJobs((j) => ({ ...j, [key]: { variants: Array.from({ length: VARIANT_COUNT }, () => ({ status: "extracting" as RegenStatus })), runId } }));
```

(`referenceId` needs no code in `runAnalysis`: `withSchemeColors`/`withRealDuration` spread the remote object, so `remote.referenceId` flows onto `analysis` now that the type allows it. Demo fallbacks have no `referenceId`, which correctly disables scoring.)

- [ ] **Step 4: Typecheck**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add app/page.tsx
git commit -m "feat(ui): score/factor/series state + referenceId + runId plumbing"
```

---

### Task 6: Client — run-level scoring trigger + gating

**Files:**
- Modify: `app/page.tsx` (poll patch line 695; auto-open effect lines 712-721; add `scoreBatch`)

**Interfaces:**
- Consumes: `POST /api/regenerate/score` (Task 3); `regenJobs[key].runId`, `analysis.referenceId` (Task 5).
- Produces: `scoreBatch(key: string): Promise<void>`. After all takes in a batch reach `done`, scoring runs once; the picker opens showing a "scoring…" state, then fills in per-take `score`/`factors`/`series` + batch `best`/`average`.

- [ ] **Step 1: Stop the poll from writing `score` (it's no longer set by `/complete`)**

In the poll patch (lines 693-698), remove the `score: job.score,` line so a late poll can't clobber the client-set score. The patch becomes:

```typescript
            updateVariant(key, i, {
              status: job.status, error: job.error, logTail: job.logTail, logUrl: job.logUrl,
              clipUrl: job.status === "done" ? `/api/regenerate/file?job=${v.jobId}&name=clip.mp4` : v.clipUrl,
              downloadUrl: job.status === "done" ? `/api/regenerate/file?job=${v.jobId}&name=final.mp4` : v.downloadUrl,
            });
```

- [ ] **Step 2: Add `scoreBatch` (place it just above the poll `useEffect`, ~line 669)**

```typescript
  // Once a batch finishes generating, score all takes against the ORIGINAL in one
  // run-level pass, then reveal the picker with scores. No clip is shown without
  // its score. If the original was never scored by the live model (demo fallback,
  // no referenceId), open the picker unscored rather than blocking.
  async function scoreBatch(key: string) {
    const st = regenJobs[key];
    const runId = st?.runId;
    const referenceId = analysis.referenceId;
    if (!runId || !referenceId) { setVariantPicker(key); return; }
    setRegenJobs((prev) => (prev[key] ? { ...prev, [key]: { ...prev[key], scoring: true } } : prev));
    setVariantPicker(key);
    try {
      const res = await fetch("/api/regenerate/score", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ runId, referenceId }),
      });
      if (!res.ok) throw new Error("scoring request failed");
      const data: { best: number; average: number; takes: { takeIndex: number; score: number; factors: Factors; series: TakeSeries }[] } = await res.json();
      setRegenJobs((prev) => {
        const cur = prev[key];
        if (!cur) return prev;
        const variants = cur.variants.slice();
        for (const t of data.takes) {
          if (variants[t.takeIndex]) variants[t.takeIndex] = { ...variants[t.takeIndex], score: t.score, factors: t.factors, series: t.series };
        }
        return { ...prev, [key]: { ...cur, variants, scoring: false, scored: true, best: data.best, average: data.average } };
      });
    } catch {
      setRegenJobs((prev) => (prev[key] ? { ...prev, [key]: { ...prev[key], scoring: false } } : prev));
    }
  }
```

- [ ] **Step 3: Trigger `scoreBatch` from the batch-finished effect**

Replace the auto-open effect (lines 712-721) with:

```typescript
  // When a batch finishes (nothing in flight) with at least one good take, score
  // it once (which opens the picker). The autoOpenedRef guard fires this once.
  useEffect(() => {
    for (const [key, st] of Object.entries(regenJobs)) {
      const anyFlight = st.variants.some((v) => isInFlight(v.status));
      const doneCount = st.variants.filter((v) => v.status === "done").length;
      if (anyFlight || doneCount === 0 || autoOpenedRef.current.has(key)) continue;
      autoOpenedRef.current.add(key);
      void scoreBatch(key);
    }
  }, [regenJobs]); // eslint-disable-line react-hooks/exhaustive-deps
```

(`scoreBatch` reads `regenJobs`/`analysis` from the current render closure; the effect already re-runs on `regenJobs` changes. The disable comment matches the existing poll effect's intentional dep handling.)

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && npx tsc --noEmit && npm run build`
Expected: typecheck clean; build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add app/page.tsx
git commit -m "feat(ui): run-level scoring pass gates the take picker"
```

---

### Task 7: Client — picker UI (headline, factors, best, average, scoring, reject-all)

**Files:**
- Modify: `app/page.tsx` (picker modal lines 928-948; add `rejectAll`)
- Modify: `app/globals.css` (new picker styles)

**Interfaces:**
- Consumes: `regenJobs[key]` `.scoring/.scored/.best/.average` + per-variant `.score/.factors` (Task 6).
- Produces: `rejectAll(key: string): void`. Picker shows per-take headline + 4-factor breakdown + a "BEST" badge on `st.best`, a header average, a scoring state, and a "Reject all" button.

- [ ] **Step 1: Add `rejectAll` (place next to `chooseVariant`, ~line 668)**

```typescript
  // Discard all takes for a slot and keep the original untouched. The segment
  // stays so the user can regenerate again.
  function rejectAll(key: string) {
    setVariantPicker(null);
    autoOpenedRef.current.delete(key);
    setRegenJobs((prev) => { const next = { ...prev }; delete next[key]; return next; });
  }
```

- [ ] **Step 2: Replace the picker modal body**

Replace lines 928-948 (the entire `variantPicker && ...` IIFE block) with — header average + scoring note, per-take headline + factor chips + best badge, and a Reject-all button:

```typescript
    {variantPicker && regenJobs[variantPicker] && (() => {
      const key = variantPicker;
      const st = regenJobs[key];
      const [start, end] = key.split("-").map(Number);
      const slot = `${formatTime(start)} – ${formatTime(end)}`;
      const variants = st.variants;
      const stillGenerating = variants.some((v) => isInFlight(v.status));
      const FACTORS: (keyof Factors)[] = ["AUD", "LANG", "ATTN", "VIS"];
      return <div className="info-backdrop" onClick={() => setVariantPicker(null)}>
        <div className="variant-modal" onClick={(e) => e.stopPropagation()}>
          <div className="info-head">
            <h2>Choose a take · {slot}</h2>
            <div className="variant-head-right">
              {typeof st.average === "number" && <span className="variant-avg" title="Average headline (mean of the 4 factors) across the takes">avg {st.average} vs original</span>}
              <button className="icon-button" onClick={() => setVariantPicker(null)} aria-label="Close"><Icon name="close" size={18}/></button>
            </div>
          </div>
          <p className="variant-sub">
            {VARIANT_COUNT} AI takes of this slot, each scored vs the original (50 = original baseline).
            {stillGenerating ? " Still generating — takes appear as they finish." : st.scoring ? " Scoring against the original…" : " Pick one to splice in."}
          </p>
          <div className="variant-grid">
            {variants.map((v, i) => <div className={`variant-card ${v.status} ${st.best === i && st.scored ? "best" : ""}`} key={i}>
              <div className="variant-head">
                <span>Take {i + 1}{st.best === i && st.scored ? <b className="variant-best" title="Best overall across the 4 factors"> · BEST</b> : null}</span>
                <span className="variant-labels">
                  {st.scoring && v.status === "done" && typeof v.score !== "number" && <em>scoring…</em>}
                  {typeof v.score === "number" && <b className="variant-score" title="Headline: mean of the 4 factors, vs the original baseline of 50" aria-label={`Score ${v.score}, baseline 50`}>{v.score}</b>}
                  {v.status === "done" ? <em className="ok">ready</em> : v.status === "error" ? <em className="bad">failed</em> : <em>{REGEN_LABEL[v.status]}</em>}
                </span>
              </div>
              {v.status === "done" && v.clipUrl
                ? <video className="variant-video" src={v.clipUrl} muted loop playsInline autoPlay controls/>
                : <div className="variant-pending">{v.status === "error" ? <span className="variant-error" title={v.error}>{v.error || "Generation failed"}</span> : <><i className="regen-dot"/>{REGEN_LABEL[v.status]}</>}</div>}
              {v.factors && <div className="variant-factors">{FACTORS.map((f) => <span key={f} className="variant-factor"><i>{f}</i>{Math.round(v.factors![f])}</span>)}</div>}
              <button className="variant-use" disabled={v.status !== "done"} onClick={() => chooseVariant(key, i)}>Use this take</button>
            </div>)}
          </div>
          <div className="variant-foot">
            <button className="variant-reject" onClick={() => rejectAll(key)}>Reject all · keep original</button>
          </div>
        </div>
      </div>;
    })()}
```

- [ ] **Step 3: Add styles to `app/globals.css`**

Append to `app/globals.css`:

```css
/* Regen take picker — scores, factor breakdown, best badge, reject-all. */
.variant-head-right { display: flex; align-items: center; gap: 12px; }
.variant-avg { font-size: 12px; opacity: 0.7; white-space: nowrap; }
.variant-card.best { outline: 1px solid var(--accent, #6ad6c0); outline-offset: 2px; }
.variant-best { color: var(--accent, #6ad6c0); font-weight: 600; }
.variant-factors { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.variant-factor { display: inline-flex; align-items: baseline; gap: 4px; font-size: 11px; padding: 2px 6px; border-radius: 6px; background: var(--surface-2, rgba(255,255,255,0.06)); }
.variant-factor i { font-style: normal; opacity: 0.55; font-size: 10px; letter-spacing: 0.04em; }
.variant-foot { display: flex; justify-content: flex-end; margin-top: 14px; }
.variant-reject { background: transparent; border: 1px solid var(--border, rgba(255,255,255,0.15)); color: inherit; opacity: 0.8; padding: 7px 12px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.variant-reject:hover { opacity: 1; }
```

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && npx tsc --noEmit && npm run build`
Expected: clean.

- [ ] **Step 5: Runtime check the picker (preview)**

Start the dev server (preview_start), upload a video, draw a 5s splice, regenerate. With the worker offline (no `referenceId`), confirm the picker opens with the 3 takes and **no** score badges (graceful unscored path), and that "Reject all" closes the picker and restores the "Regenerate" button. With the worker online, confirm score badges, the 4 factor chips, a BEST badge on one card, and the header average. (If no GPU worker is available in this environment, verify the unscored path and note the scored path for the integration pass.)

- [ ] **Step 6: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add app/page.tsx app/globals.css
git commit -m "feat(ui): take picker shows scores, factor breakdown, best, reject-all"
```

---

### Task 8: Client — selection splices the take into the original graph

**Files:**
- Modify: `app/page.tsx` (`chooseVariant` lines 655-667; add helpers)

**Interfaces:**
- Consumes: `RegenVariant.series` (Task 6); `Analysis` (`global`, `regions[].values`, `cognitiveSeries`).
- Produces: `spliceTakeIntoAnalysis(series: TakeSeries, start: number, end: number): void` and `resampleTo(series: number[], n: number): number[]`. On "Use this take", `analysis` for `[start,end]` is replaced by the take's series and `regions[].score` recomputed; the overall `engagementScore()` `useMemo` re-derives automatically.

- [ ] **Step 1: Add the short→family-key map and helpers (near `engagementScore`, ~line 122, module scope)**

```typescript
const FAMILY_KEY_BY_SHORT: Record<string, string> = {
  AUD: "auditory_engagement",
  LANG: "language_message",
  ATTN: "attention_salience",
  VIS: "visual_motion",
};

// Linearly resample a per-frame series to exactly n points (the take is ~5
// samples at 1 Hz; the graph segment may have a different frame count).
function resampleTo(series: number[], n: number): number[] {
  if (n <= 0) return [];
  if (series.length === 0) return new Array(n).fill(0);
  if (series.length === 1) return new Array(n).fill(series[0]);
  return Array.from({ length: n }, (_, k) => {
    const pos = n === 1 ? 0 : (k / (n - 1)) * (series.length - 1);
    const lo = Math.floor(pos), hi = Math.ceil(pos), frac = pos - lo;
    return series[lo] * (1 - frac) + series[hi] * frac;
  });
}
```

- [ ] **Step 2: Add `spliceTakeIntoAnalysis` (inside the component, near `chooseVariant`, ~line 654)**

```typescript
  // Replace [start,end] of the engagement graph with the chosen take's per-frame
  // series (already z-scored vs the original, so it drops in on the same scale).
  // Updates global, each region's values + mean score, and cognitiveSeries; the
  // overall engagementScore() useMemo recomputes from the new analysis.
  function spliceTakeIntoAnalysis(series: TakeSeries, start: number, end: number) {
    setAnalysis((a) => {
      const len = a.global.length;
      if (!len || !a.duration) return a;
      const i0 = Math.max(0, Math.round((start / a.duration) * (len - 1)));
      const i1 = Math.min(len - 1, Math.round((end / a.duration) * (len - 1)));
      const n = i1 - i0 + 1;
      if (n <= 0) return a;
      const spliceInto = (orig: number[], take: number[]) => {
        const next = orig.slice();
        const rs = resampleTo(take, n);
        for (let k = 0; k < n; k++) next[i0 + k] = rs[k];
        return next;
      };
      const global = spliceInto(a.global, series.global);
      const cognitiveSeries: Record<string, number[]> = { ...(a.cognitiveSeries ?? {}) };
      const regions = a.regions.map((r) => {
        const takeVals = series[r.short as keyof TakeSeries];
        if (!Array.isArray(takeVals)) return r;
        const values = spliceInto(r.values, takeVals);
        const fk = FAMILY_KEY_BY_SHORT[r.short];
        if (fk) cognitiveSeries[fk] = values;
        const score = Math.round((values.reduce((s, x) => s + x, 0) / values.length) * 10) / 10;
        return { ...r, values, score };
      });
      return { ...a, global, regions, cognitiveSeries };
    });
  }
```

- [ ] **Step 3: Splice in `chooseVariant` before the video swap**

Replace `chooseVariant` (lines 655-667) with — capture `series` first (the batch is deleted by the video swap), splice, then swap:

```typescript
  async function chooseVariant(key: string, i: number) {
    const v = regenJobs[key]?.variants[i];
    if (!v?.downloadUrl) return;
    const series = v.series;
    const [start, end] = key.split("-").map(Number);
    const slot = `${formatTime(start)}–${formatTime(end)}`;
    setVariantPicker(null);
    try {
      if (series) spliceTakeIntoAnalysis(series, start, end);
      await replacePreviewWithRegeneratedVideo(v.downloadUrl, { start, end }, key);
      logUpsert(`regen_${key}_t${i}`, { title: `Regenerate ${slot} · take ${i + 1}`, detail: "Applied in place · graph updated · ready to play", status: "done", href: v.downloadUrl });
    } catch (error) {
      logUpsert(`regen_${key}_t${i}`, { title: `Regenerate ${slot} · take ${i + 1}`, detail: error instanceof Error ? error.message : "Couldn't load the regenerated video", status: "error" });
    }
  }
```

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/adrianm/berkeleyhack/Cerebra && npx tsc --noEmit && npm run build`
Expected: clean.

- [ ] **Step 5: Runtime check the graph update (preview)**

With the worker online: regenerate a 5s slot, score, note a take's headline, click "Use this take", and confirm the engagement graph's curve for that segment changes and the overall score (the `engagementScore` display) shifts. With the worker offline, click "Use this take" on an unscored take and confirm the video still swaps in and no error is thrown (`series` undefined → splice skipped). 

- [ ] **Step 6: Commit**

```bash
cd /Users/adrianm/berkeleyhack/Cerebra
git add app/page.tsx
git commit -m "feat(ui): selecting a take splices its scores into the original graph"
```

---

## Self-Review

**Spec coverage:**
- "Score only the 3 takes, reuse original" → Task 1 (persist μ/σ) + Task 2 (`/score_takes` loads ref, never re-encodes original) + Task 3 (sends only takes). ✓
- "Score individually against the same original" → Task 2 z-scores each take vs the one `referenceId` baseline. ✓
- "4 factors; headline = average of 4; best = highest" → Task 2 (`factors`, `score`=engagementScore=mean of 4, `best`=max). Task 7 displays. ✓
- "Fastest on 1 A100" → only 3 take encodes; original reused; per-run cache (`scores.json`). ✓
- "Before displayed" gating → Task 6 scores after generation, opens picker with scores; unscored only on the no-referenceId fallback. ✓
- "Selection replaces video + graph segment + scores" → Task 8. ✓
- "Reject all" → Task 7. ✓
- "5s target / receptive-field" → fixed-reference (no concat) is inherently seam-free; documented in the spec. ✓

**Placeholder scan:** No TBD/TODO; all code blocks are complete; commands have expected output. ✓

**Type consistency:** `Factors` keys `AUD/LANG/ATTN/VIS` match worker `regions[].short` and `TakeSeries` keys and `FAMILY_KEY_BY_SHORT`. `score`/`factors`/`series` on `RegenVariant` match the `scoreBatch` patch and `/score_takes` response. `best` is a `takeIndex` (worker returns `out[best]["takeIndex"]`; client compares `st.best === i`). ✓

**Known limitation (documented in spec, not a gap):** if the original's `.ref.npz` was never written (analysed before this change, or cache cleared), `/score_takes` returns 409 and the client opens the picker unscored. Backfill = re-run analysis on the original.
