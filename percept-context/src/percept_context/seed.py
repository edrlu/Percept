"""A small curated video-ad knowledge graph so a fresh install has structure.

This is intentionally compact — enough to demonstrate GraphRAG traversal and
the reward loop. Real deployments grow the graph from usage + research.
"""

from __future__ import annotations

# (key, type, label, content)
_NODES = [
    ("hook_first_second", "principle", "Hook in the first second",
     "Stop the scroll within ~1s: open on motion, a face, or an unresolved question before any logo."),
    ("show_dont_tell", "principle", "Show, don't tell",
     "Demonstrate the product in use; let visuals carry the claim instead of voiceover assertions."),
    ("pattern_interrupt", "technique", "Pattern interrupt",
     "An unexpected cut, sound, or visual break that resets attention mid-scroll."),
    ("ugc_authenticity", "technique", "UGC authenticity",
     "Handheld, imperfect, first-person framing reads as real and outperforms polished studio looks for short-form."),
    ("problem_solution", "technique", "Problem-solution arc",
     "Name a sharp pain in the first beat, then resolve it with the product as the turn."),
    ("social_proof", "technique", "Social proof",
     "On-screen counts, reviews, or 'everyone is using this' framing to borrow credibility."),
    ("fast_cuts", "technique", "Fast cuts / high pacing",
     "Sub-2s shots sustain attention and raise completion rate on vertical video."),
    ("beverage", "industry", "Beverage",
     "Refreshment, condensation, pour shots, ice, lifestyle and taste cues."),
    ("beauty", "industry", "Beauty",
     "Before/after, texture close-ups, skin and glow; trust and results matter."),
    ("saas", "industry", "SaaS / App",
     "Screen-recorded value moment, the 'aha', speed and time saved."),
    ("fitness", "industry", "Fitness",
     "Transformation, effort, energy; aspirational outcomes and momentum."),
    ("food", "industry", "Food",
     "Sizzle, steam, the bite; sensory cues drive craving and appetite appeal."),
]

# (src_key, EDGE_TYPE, dst_key, weight)
_EDGES = [
    ("hook_first_second", "ENABLES", "pattern_interrupt", 2.0),
    ("pattern_interrupt", "WORKS_IN", "beverage", 1.5),
    ("fast_cuts", "WORKS_IN", "beverage", 1.6),
    ("ugc_authenticity", "WORKS_IN", "beauty", 2.2),
    ("show_dont_tell", "WORKS_IN", "beauty", 1.8),
    ("problem_solution", "WORKS_IN", "saas", 2.4),
    ("show_dont_tell", "WORKS_IN", "saas", 1.7),
    ("problem_solution", "WORKS_IN", "fitness", 2.0),
    ("social_proof", "WORKS_IN", "fitness", 1.4),
    ("fast_cuts", "WORKS_IN", "food", 1.9),
    ("show_dont_tell", "WORKS_IN", "food", 2.1),
    ("hook_first_second", "CO_OCCURS_WITH", "fast_cuts", 1.3),
    ("ugc_authenticity", "CO_OCCURS_WITH", "social_proof", 1.2),
]


def seed_graph(cg, graph: str | None = None) -> dict:
    """Idempotent-ish seed. Returns counts. Re-running adds duplicate nodes,
    so call once per fresh graph namespace (or flush first)."""
    ids = {}
    for key, ntype, label, content in _NODES:
        ids[key] = cg.add_node(ntype, label, content, props={"seed_key": key}, graph=graph)
    n_edges = 0
    for src, etype, dst, weight in _EDGES:
        if src in ids and dst in ids:
            cg.link(ids[src], ids[dst], etype, weight=weight, graph=graph)
            n_edges += 1
    return {"graph": cg._g(graph), "nodes_added": len(ids), "edges_added": n_edges}
