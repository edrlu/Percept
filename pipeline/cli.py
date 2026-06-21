"""Quick local test runner for Stage 1 (no server needed).

    python -m pipeline.cli "a 15s ad for a cold-brew coffee can, gen-z, energetic"
    python -m pipeline.cli "promote my AI note-taking app" --industry saas --research

Prints the assembled video-model payload plus the retrieval/research provenance.
"""

from __future__ import annotations

import argparse

from . import optimizer
from .schema import OptimizeRequest


def main() -> None:
    ap = argparse.ArgumentParser(description="Cerebra Stage 1 optimizer")
    ap.add_argument("brief", help="The creative brief (voice→text or typed).")
    ap.add_argument("--product", default=None)
    ap.add_argument("--industry", default=None)
    ap.add_argument("--aspect-ratio", default=None)
    ap.add_argument("--duration", type=int, default=None)
    ap.add_argument("--research", action="store_true", help="Run live web research.")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    resp = optimizer.optimize(
        OptimizeRequest(
            brief=args.brief,
            product=args.product,
            industry=args.industry,
            aspect_ratio=args.aspect_ratio,
            duration_seconds=args.duration,
            live_research=args.research,
            use_cache=not args.no_cache,
        )
    )

    print("=" * 72)
    print(f"cached={resp.cached}  llm_backed={resp.llm_backed}")
    print("-" * 72)
    print("RETRIEVED:")
    for d in resp.retrieved:
        print(f"  ({d.score}) [{d.category}/{d.industry}] {d.title}")
    if resp.research:
        print("RESEARCH:")
        for f in resp.research:
            print(f"  - {f.title}: {f.technique}")
    print("=" * 72)
    print("VIDEO-MODEL PAYLOAD:\n")
    print(resp.video_model_payload)


if __name__ == "__main__":
    main()
