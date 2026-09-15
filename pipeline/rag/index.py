"""Retriever: dense (Qdrant), BM25, or weighted-RRF hybrid over KB chunks."""
import logging
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

import bm25s
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from pipeline.rag.embedder import Embedder
from pipeline.rag.kb import Chunk, load_kb

log = logging.getLogger(__name__)
RRF_K = 60
HYBRID_POOL = 20


@dataclass
class Hit:
    """A retrieved chunk plus its score (cosine or RRF)."""
    chunk: Chunk
    score: float


def rrf(rankings: list[list[str]], k: int = RRF_K, weights: list[float] | None = None) -> list[tuple[str, float]]:
    """Reciprocal rank fusion. k=60. Docs only in one list still get a score."""
    if weights is None:
        weights = [1.0] * len(rankings)
    scores: dict[str, float] = {}
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


class Retriever:
    """Dense (Qdrant), BM25, or hybrid. Local Qdrant locks the folder; close() on exit."""

    def __init__(self, settings, kind: str, chunks: list[Chunk], embedder: Embedder | None):
        """kind is dense, bm25, or hybrid. embedder required unless bm25-only."""
        self.settings = settings
        self.kind = kind
        self.chunks = chunks
        self.embedder = embedder
        self._by_id = {c.id: c for c in chunks}
        self._qdrant = None
        self._bm25 = None
        if kind in ("dense", "hybrid"):
            self._ensure_dense()
        if kind in ("bm25", "hybrid"):
            self._ensure_bm25()

    def _ensure_dense(self):
        """Open or rebuild the local `kb` collection if count or vector dim drifted."""
        path = self.settings.QDRANT_PATH
        path.mkdir(parents=True, exist_ok=True)
        self._qdrant = QdrantClient(path=str(path))
        n = len(self.chunks)
        exists = self._qdrant.collection_exists("kb")
        count = self._qdrant.count("kb").count if exists else 0
        stored_dim = None
        if exists:
            stored_dim = self._qdrant.get_collection("kb").config.params.vectors.size
        if exists and count == n and stored_dim == self.embedder.dim:
            log.info("qdrant kb reused (%s points, dim %s)", count, stored_dim)
            return
        if exists:
            self._qdrant.delete_collection("kb")
        dim = self.embedder.dim
        self._qdrant.create_collection(
            collection_name="kb",
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        vecs = self.embedder.encode_documents([c.text for c in self.chunks])
        points = [
            PointStruct(
                id=str(uuid5(NAMESPACE_URL, c.id)),
                vector=vecs[i].tolist(),
                payload={
                    "id": c.id,
                    "text": c.text,
                    "original_id": c.original_id,
                    "section": c.section,
                    "control": c.control,
                },
            )
            for i, c in enumerate(self.chunks)
        ]
        self._qdrant.upsert(collection_name="kb", points=points)
        log.info("qdrant kb indexed %s points", n)

    def _ensure_bm25(self):
        """In-memory BM25 over chunk texts. Rebuilt each process; 117 docs is cheap."""
        tokens = bm25s.tokenize([c.text for c in self.chunks], stopwords="en", show_progress=False)
        self._bm25 = bm25s.BM25()
        self._bm25.index(tokens)

    def retrieve(self, question: str, k: int | None = None) -> list[Hit]:
        """Top-k hits for `question` using this retriever kind."""
        k = self.settings.TOP_K if k is None else k
        if self.kind == "dense":
            return self._dense(question, k)
        if self.kind == "bm25":
            return self._bm25_query(question, k)
        return self._hybrid(question, k)

    def _dense(self, question: str, k: int) -> list[Hit]:
        """Qdrant cosine search with the query embedding."""
        vec = self.embedder.encode_queries([question])[0].tolist()
        res = self._qdrant.query_points(collection_name="kb", query=vec, limit=k)
        hits = []
        for p in res.points:
            cid = p.payload["id"]
            hits.append(Hit(self._by_id[cid], float(p.score)))
        return hits

    def _bm25_query(self, question: str, k: int) -> list[Hit]:
        """BM25 hits with score > 0 (library pads zeros when fewer than k match)."""
        qtok = bm25s.tokenize([question], stopwords="en", show_progress=False)
        if getattr(qtok, "ids", None) is not None and len(qtok.ids[0]) == 0:
            return []
        idxs, scores = self._bm25.retrieve(
            qtok, k=min(k, len(self.chunks)), show_progress=False
        )
        hits = []
        for i, s in zip(np.asarray(idxs).reshape(-1), np.asarray(scores).reshape(-1)):
            if float(s) > 0:
                hits.append(Hit(self.chunks[int(i)], float(s)))
        return hits

    def _hybrid(self, question: str, k: int) -> list[Hit]:
        """RRF of top-20 dense and BM25 lists, weighted by DENSE_WEIGHT."""
        dense = self._dense(question, HYBRID_POOL)
        bm25 = self._bm25_query(question, HYBRID_POOL)
        dw = self.settings.DENSE_WEIGHT
        fused = rrf([[h.chunk.id for h in dense], [h.chunk.id for h in bm25]], weights=[dw, 1 - dw])
        by_id = {h.chunk.id: h.chunk for h in dense + bm25}
        return [Hit(by_id[i], s) for i, s in fused[:k]]

    def close(self):
        """Release the Qdrant folder lock so another process can open it."""
        if self._qdrant is not None:
            self._qdrant.close()
            self._qdrant = None


def build_retriever(settings, kind=None, embedder=None, chunks=None) -> Retriever:
    """Load KB + embedder unless passed in. `kind` defaults to settings.RETRIEVER."""
    kind = kind or settings.RETRIEVER
    if kind not in ("dense", "bm25", "hybrid"):
        raise ValueError(f"unknown retriever {kind}")
    chunks = chunks or load_kb(settings.KB_PATH)
    if embedder is None and kind != "bm25":
        embedder = Embedder(settings)
    return Retriever(settings, kind, chunks, embedder)


if __name__ == "__main__":
    import argparse, dataclasses, json
    from pathlib import Path
    from pipeline.config.config import load_settings
    from pipeline.config.logging_setup import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Retrieve top-k chunks for a question")
    parser.add_argument("question", help="The question to retrieve for")
    args = parser.parse_args()

    s = load_settings()
    r = build_retriever(s)
    try:
        hits = r.retrieve(args.question, k=s.TOP_K)
    finally:
        r.close()

    results = {
        "question" : args.question,
        "hits": [
        {"rank": i + 1, "score": round(h.score, 4), **dataclasses.asdict(h.chunk)}
        for i, h in enumerate(hits)]}
    out = Path("outputs/retrieval_test.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"{s.RETRIEVER} top-{s.TOP_K}: {len(hits)} hits -> {out}")
