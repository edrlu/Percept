"""The Percept Context context graph: a reward-weighted property graph on Redis.

Storage model
-------------
Nodes  : Redis hashes under ``{prefix}:{graph}:{uuid}`` indexed by RedisVL
         (vector + tag/text/numeric fields). Node props live in a sibling hash.
Edges  : adjacency in sorted sets, score = edge weight (reward).
           out: percept:adj:{graph}:out:{src}:{type}  member=dst score=weight
           in : percept:adj:{graph}:in:{dst}:{type}   member=src score=weight
         edge types per node tracked in a set for "all neighbors" traversal.

Retrieval (GraphRAG)
--------------------
1. Vector search → entry nodes (semantic).
2. Beam traversal of highest-weight edges → connected subgraph (structural).
3. Render the subgraph into grounded context text.

Learning
--------
``record_outcome(path, reward)`` reinforces edges along a path (ZINCRBY) and
bumps node scores, so future traversals favor what previously performed.
"""

from __future__ import annotations

import json
from uuid import uuid4

from redisvl.query import CountQuery, FilterQuery, VectorQuery
from redisvl.query.filter import Tag

from .config import Settings
from .embeddings import get_vectorizer
from .store import build_index, make_clients, to_vector_bytes


class ContextGraph:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ready = False

    # ------------------------------------------------------------------ init
    def _ensure(self):
        if self._ready:
            return
        self.vectorizer = get_vectorizer(
            self.settings.vectorizer, self.settings.embedding_model
        )
        self.dims = int(self.vectorizer.dims)
        self.raw, self.kv = make_clients(self.settings)
        self.index = build_index(self.settings, self.dims, self.raw)
        self._ready = True

    def _g(self, graph: str | None) -> str:
        return graph or self.settings.default_graph

    # ----------------------------------------------------------- key helpers
    def _adj(self, graph, direction, node, etype):
        return f"percept:adj:{graph}:{direction}:{node}:{etype}"

    def _types(self, graph, direction, node):
        return f"percept:adjtypes:{graph}:{direction}:{node}"

    def _edge(self, graph, src, etype, dst):
        return f"percept:edge:{graph}:{src}:{etype}:{dst}"

    def _props(self, node_id):
        return f"percept:nodeprops:{node_id}"

    def _node_key(self, node_id):
        return f"{self.settings.node_prefix}:{node_id}"

    def _strip_prefix(self, value: str) -> str:
        pre = self.settings.node_prefix + ":"
        return value[len(pre):] if value and value.startswith(pre) else value

    # --------------------------------------------------------------- writes
    def add_node(
        self, type, label, content="", props=None, graph=None, score=0.0
    ) -> str:
        self._ensure()
        graph = self._g(graph)
        node_id = f"{graph}:{uuid4().hex}"
        text = content or label or ""
        emb = self.vectorizer.embed(text)
        record = {
            "id": node_id,
            "graph": graph,
            "type": type,
            "label": label or "",
            "content": content or "",
            "score": float(score),
            "embedding": to_vector_bytes(emb),
        }
        self.index.load([record], id_field="id")
        if props:
            self.kv.hset(
                self._props(node_id),
                mapping={k: json.dumps(v) for k, v in props.items()},
            )
        return node_id

    def link(self, src, dst, type, weight=1.0, props=None, graph=None) -> dict:
        self._ensure()
        graph = self._g(graph)
        weight = float(weight)
        self.kv.zadd(self._adj(graph, "out", src, type), {dst: weight})
        self.kv.zadd(self._adj(graph, "in", dst, type), {src: weight})
        self.kv.sadd(self._types(graph, "out", src), type)
        self.kv.sadd(self._types(graph, "in", dst), type)
        if props:
            self.kv.hset(
                self._edge(graph, src, type, dst),
                mapping={k: json.dumps(v) for k, v in props.items()},
            )
        return {"src": src, "dst": dst, "type": type, "weight": weight, "graph": graph}

    # --------------------------------------------------------------- reads
    def get_node(self, node_id, graph=None) -> dict | None:
        self._ensure()
        node_id = self._strip_prefix(node_id)
        key = self._node_key(node_id)
        vals = self.kv.hmget(key, ["graph", "type", "label", "content", "score"])
        if not any(v is not None for v in vals):
            return None
        node = {
            "node_id": node_id,
            "graph": vals[0],
            "type": vals[1],
            "label": vals[2],
            "content": vals[3],
            "score": float(vals[4] or 0),
        }
        props = self.kv.hgetall(self._props(node_id))
        if props:
            node["props"] = {k: _loads(v) for k, v in props.items()}
        return node

    def neighbors(self, node_id, edge_type=None, graph=None, limit=10, direction="out"):
        self._ensure()
        graph = self._g(graph)
        node_id = self._strip_prefix(node_id)
        types = [edge_type] if edge_type else sorted(
            self.kv.smembers(self._types(graph, direction, node_id))
        )
        out = []
        for t in types:
            for member, weight in self.kv.zrevrange(
                self._adj(graph, direction, node_id, t), 0, limit - 1, withscores=True
            ):
                out.append({"node_id": member, "edge_type": t, "weight": float(weight)})
        out.sort(key=lambda x: x["weight"], reverse=True)
        return out[:limit]

    def search(self, query, graph=None, types=None, k=5):
        self._ensure()
        graph = self._g(graph)
        emb = self.vectorizer.embed(query)
        filt = Tag("graph") == graph
        if types:
            filt = filt & (Tag("type") == list(types))
        vq = VectorQuery(
            vector=emb,
            vector_field_name="embedding",
            return_fields=["id", "graph", "type", "label", "content", "score"],
            num_results=k,
            filter_expression=filt,
        )
        return [self._fmt(r) for r in self.index.query(vq)]

    # ------------------------------------------------------------- graphrag
    def graph_rag(self, query, graph=None, types=None, k=5, hops=1, beam=5):
        self._ensure()
        graph = self._g(graph)
        entries = self.search(query, graph=graph, types=types, k=k)
        visited = {e["node_id"]: e for e in entries}
        seen_edges = set()
        edges = []
        frontier = [e["node_id"] for e in entries]
        for _ in range(max(0, hops)):
            nxt = []
            for nid in frontier:
                for nb in self.neighbors(nid, graph=graph, limit=beam):
                    ekey = (nid, nb["edge_type"], nb["node_id"])
                    if ekey not in seen_edges:
                        seen_edges.add(ekey)
                        edges.append(
                            {
                                "src": nid,
                                "dst": nb["node_id"],
                                "type": nb["edge_type"],
                                "weight": nb["weight"],
                            }
                        )
                    if nb["node_id"] not in visited:
                        node = self.get_node(nb["node_id"], graph=graph)
                        if node:
                            visited[nb["node_id"]] = node
                            nxt.append(nb["node_id"])
            frontier = nxt
        return {
            "query": query,
            "graph": graph,
            "entry_nodes": entries,
            "nodes": list(visited.values()),
            "edges": edges,
            "context": self._render_context(entries, visited, edges),
        }

    # -------------------------------------------------------------- learning
    def record_outcome(self, path, reward, graph=None, edge_type="REWARDED"):
        self._ensure()
        graph = self._g(graph)
        reward = float(reward)
        path = [self._strip_prefix(p) for p in path]
        bumped = []
        for a, b in zip(path, path[1:]):
            linked = False
            for t in sorted(self.kv.smembers(self._types(graph, "out", a))):
                if self.kv.zscore(self._adj(graph, "out", a, t), b) is not None:
                    self.kv.zincrby(self._adj(graph, "out", a, t), reward, b)
                    self.kv.zincrby(self._adj(graph, "in", b, t), reward, a)
                    bumped.append({"src": a, "dst": b, "type": t})
                    linked = True
            if not linked:
                self.link(a, b, edge_type, weight=reward, graph=graph)
                bumped.append({"src": a, "dst": b, "type": edge_type})
        for nid in path:
            try:
                self.raw.hincrbyfloat(self._node_key(nid), "score", reward)
            except Exception:
                pass
        return {"reward": reward, "edges_updated": bumped, "nodes_reinforced": path}

    def top_performers(self, graph=None, types=None, limit=10):
        self._ensure()
        graph = self._g(graph)
        filt = Tag("graph") == graph
        if types:
            filt = filt & (Tag("type") == list(types))
        fq = FilterQuery(
            filter_expression=filt,
            return_fields=["id", "graph", "type", "label", "score"],
            num_results=limit,
        )
        try:
            fq.sort_by("score", asc=False)
        except Exception:
            pass
        return [self._fmt(r) for r in self.index.query(fq)]

    def stats(self, graph=None):
        self._ensure()
        graph = self._g(graph)
        out = {"graph": graph, "index": self.settings.node_index}
        try:
            out["ping"] = bool(self.kv.ping())
        except Exception as exc:
            out["ping"] = False
            out["error"] = str(exc)
        try:
            out["nodes_in_graph"] = int(
                self.index.query(CountQuery(Tag("graph") == graph))
            )
        except Exception as exc:
            out["nodes_in_graph"] = None
            out.setdefault("error", str(exc))
        try:
            out["endpoint"] = self.settings.redis_url.split("@")[-1]
        except Exception:
            pass
        return out

    # -------------------------------------------------------------- helpers
    def _fmt(self, r: dict) -> dict:
        node_id = self._strip_prefix(r.get("id", ""))
        out = {
            "node_id": node_id,
            "graph": r.get("graph"),
            "type": r.get("type"),
            "label": r.get("label"),
            "content": r.get("content"),
            "score": float(r.get("score") or 0),
        }
        if "vector_distance" in r:
            out["similarity"] = round(1 - float(r["vector_distance"] or 0), 4)
        return out

    def _render_context(self, entries, visited, edges) -> str:
        lines = ["# Retrieved context graph", ""]
        lines.append("## Entry nodes (semantic match)")
        for e in entries:
            sim = e.get("similarity")
            tag = f" (sim {sim})" if sim is not None else ""
            lines.append(f"- [{e['type']}] {e['label']}{tag}: {e['content']}")
        if edges:
            lines.append("")
            lines.append("## Connected, proven relationships (reward-weighted)")
            label = {n["node_id"]: n.get("label", n["node_id"]) for n in visited.values()}
            for ed in sorted(edges, key=lambda x: x["weight"], reverse=True):
                s = label.get(ed["src"], ed["src"])
                d = label.get(ed["dst"], ed["dst"])
                lines.append(f"- {s} —[{ed['type']} w={ed['weight']:.2f}]→ {d}")
        return "\n".join(lines)


def _loads(v):
    try:
        return json.loads(v)
    except Exception:
        return v
