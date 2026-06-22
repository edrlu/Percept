#!/usr/bin/env python3
"""cerebra_eval — score a video against the TRIBE v2 neuro-engagement endpoint.

A thin client for humans and CI. The Pika skills call /score directly; this is
for quick local testing and for printing a readable report.

    export NEURO_API_URL=https://<tunnel>.trycloudflare.com
    export NEURO_API_KEY=<key>            # if the server enforces one
    python cerebra_eval.py path/to/clip.mp4
    python cerebra_eval.py a.mp4 b.mp4     # A/B: scores both and names a winner
    python cerebra_eval.py clip.mp4 --json # raw JSON only

No third-party deps — stdlib urllib multipart.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.request
import uuid
from pathlib import Path


def post_video(base_url: str, api_key: str, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="video"; filename="{path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    body = pre + path.read_bytes() + post
    req = urllib.request.Request(base_url.rstrip("/") + "/score", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if api_key:
        req.add_header("x-api-key", api_key)
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def print_report(name: str, rep: dict) -> None:
    peak, weak = rep.get("peak", {}), rep.get("weakWindow", {})
    chans = " · ".join(f"{r['short']} {r['score']:.0f}" for r in rep.get("regions", []))
    print(f"\n{name}")
    score = rep.get("adScore", rep.get("engagementScore"))
    print(f"  AD SCORE: {score}/100   ({rep.get('duration')}s)")
    print(f"  Peak  {peak.get('time')}s ({peak.get('label')}) — {peak.get('value')}")
    print(f"  Weak  {weak.get('startTime')}–{weak.get('endTime')}s — {weak.get('meanValue')}   <- fix this")
    print(f"  Channels: {chans}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Score video(s) for neuro-engagement.")
    ap.add_argument("videos", nargs="+", type=Path)
    ap.add_argument("--json", action="store_true", help="print raw JSON only")
    args = ap.parse_args()

    base = os.environ.get("NEURO_API_URL")
    if not base:
        print("set NEURO_API_URL (and NEURO_API_KEY) — see the colab/ notebook output", file=sys.stderr)
        return 2
    key = os.environ.get("NEURO_API_KEY", "")

    reports = []
    for v in args.videos:
        if not v.exists():
            print(f"missing file: {v}", file=sys.stderr)
            return 2
        rep = post_video(base, key, v)
        reports.append((v.name, rep))

    if args.json:
        print(json.dumps([r for _, r in reports] if len(reports) > 1 else reports[0][1], indent=2))
        return 0

    for name, rep in reports:
        print_report(name, rep)

    if len(reports) > 1:
        winner = max(
            reports,
            key=lambda nr: nr[1].get("adScore", nr[1].get("engagementScore", 0)),
        )
        scores = sorted(
            (r.get("adScore", r.get("engagementScore", 0)) for _, r in reports),
            reverse=True,
        )
        gap = scores[0] - scores[1]
        verdict = "clear winner" if gap >= 3 else "too close to call"
        winning_score = winner[1].get(
            "adScore", winner[1].get("engagementScore")
        )
        print(f"\nWINNER: {winner[0]}  ({winning_score}/100, +{gap:.1f}) — {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
