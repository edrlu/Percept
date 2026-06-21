"""End-to-end Percept Context demo against your Redis.

    cd percept-context
    cp .env.example .env   # set REDIS_URL
    python examples/quickstart.py
"""

from percept_context import load_settings, ContextGraph
from percept_context.seed import seed_graph

cg = ContextGraph(load_settings())

# Use a throwaway namespace so we don't clutter the shared graph.
GRAPH = "demo"

print("Seeding demo graph…")
print(seed_graph(cg, graph=GRAPH))

print("\nGraphRAG query: 'energy drink ad that stops the scroll'")
rag = cg.graph_rag("energy drink ad that stops the scroll", graph=GRAPH, k=3, hops=1)
print(rag["context"])

print("\nReinforcing a winning path (simulating a high TRIBE score)…")
entry = rag["entry_nodes"][0]["node_id"]
if rag["edges"]:
    path = [rag["edges"][0]["src"], rag["edges"][0]["dst"]]
    print(cg.record_outcome(path, reward=5.0, graph=GRAPH))

print("\nTop performers after reinforcement:")
for n in cg.top_performers(graph=GRAPH, limit=5):
    print(f"  {n['score']:>6.2f}  [{n['type']}] {n['label']}")

print("\nStats:", cg.stats(graph=GRAPH))
