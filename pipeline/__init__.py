"""Cerebra Stage 1 — research-backed RAG prompt optimizer.

The first stage of the Cerebra loop: user prompt (voice/text) → Redis vector
retrieval + live research → an optimized, evidence-backed payload for the video
model (Seedance 2.0 SYSTEM prompt + context + generation skill). Stage 2 and the
TRIBE v2 neural-response scoring loop consume this output.
"""

from .optimizer import optimize  # noqa: F401
from .schema import OptimizeRequest, OptimizeResponse  # noqa: F401

__all__ = ["optimize", "OptimizeRequest", "OptimizeResponse"]
