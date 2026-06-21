# Cerebra plugin (Claude Code)

This repo is also a **Claude Code plugin**: it bundles Meta's TRIBE v2 cortical-engagement
model as an MCP server and ships the `cerebra-cut` skill, which predicts a video's engagement
curve and auto-cuts it to the peak moments.

## What's in the bundle

| Component | Path | Purpose |
|---|---|---|
| Plugin manifest | `.claude-plugin/plugin.json` | identity, version, keywords |
| Marketplace | `.claude-plugin/marketplace.json` | makes the repo installable |
| MCP server | `.mcp.json` → `worker/mcp_server.py` | TRIBE v2 as `predict_engagement` / `engagement_health` (stdio) |
| Skill | `skills/cerebra-cut/` | the engagement → auto-cut workflow |

`worker/engagement.py` is the plugin's standalone, FastAPI-free TRIBE v2 analysis core: it
loads the model, summarises the four cortical-engagement proxies over time, and ranks
trim-ready peak ranges — so the MCP server runs without any of the web app's HTTP stack.

## Prerequisites

1. **The `pika` plugin** must be installed too — `cerebra-cut` uses its `mcp__plugin_pika_pika__*`
   tools for uploading, trimming, concatenating, and captioning. Install it from the Pika
   marketplace (`Pika-Labs/Pika-Plugins`).
2. **A Python env with the TRIBE deps**, since the MCP server runs the model locally. The
   `.mcp.json` launches `${CLAUDE_PLUGIN_ROOT}/.venv/bin/python`, so create that venv once:

   ```bash
   python3.12 -m venv .venv
   .venv/bin/pip install -r worker/requirements.txt
   ```

   (The existing `./run.sh` already creates `.venv` and installs these deps.) TRIBE v2's
   language path can require the gated `meta-llama/Llama-3.2-3B`; set `HF_TOKEN` in your
   environment if the model isn't already cached.

## Install

```
/plugin marketplace add edrlu/Cerebra
/plugin install cerebra
```

Then ask: *"cut /path/to/clip.mp4 to its most engaging moment"* — the skill calls
`predict_engagement`, picks the peak ranges, and renders the cut via Pika.

## Smoke-test the MCP server directly

```bash
.venv/bin/python worker/mcp_server.py   # speaks MCP over stdio; Ctrl-C to exit
```

The first `predict_engagement` call loads the model (slow); subsequent calls reuse it.
