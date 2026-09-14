"""Evaluate retriever: Recall@1, Recall@5, Recall@10, MRR@5.

Requires a --data JSON file (from generate_data.py) with entries:
  [{"query": "...", "relevant_id": "..."}]

Usage:
    python -m rag_evals --data rag_evals/data/original.json
    python -m rag_evals --data rag_evals/data/synthetic.json --k 10
"""
import argparse
import json
import logging
from pathlib import Path

from pipeline.config.config import load_settings
from pipeline.config.logging_setup import setup_logging
from pipeline.rag.index import build_retriever
from pipeline.rag.kb import load_kb

from .metrics import aggregate

log = logging.getLogger(__name__)


def run_eval(settings, data: list[dict], k: int = 10, out_dir: Path | None = None):
    """Run retrieval eval from pre-built query list and print/save metrics."""
    chunks = load_kb(settings.KB_PATH)
    retriever = build_retriever(settings, chunks=chunks)
    log.info("evaluating %s queries with retriever=%s, k=%s", len(data), retriever.kind, k)

    results = []
    for entry in data:
        query_text, gold_id = entry["query"], entry["relevant_id"]
        hits = retriever.retrieve(query_text, k=k)
        retrieved = [{"id": h.chunk.original_id, "score": round(h.score, 6)} for h in hits]
        results.append({
            "query": query_text,
            "relevant_id": gold_id,
            "retrieved_ids": [r["id"] for r in retrieved],
            "retrieved": retrieved,
        })

    metrics = aggregate(results)
    retriever.close()

    output = {
        "retriever": retriever.kind,
        "num_queries": len(data),
        "k": k,
        "metrics": metrics,
        "per_query": [
            {
                "query": r["query"],
                "expected_chunk_id": r["relevant_id"],
                "rank": next(
                    (i + 1 for i, rid in enumerate(r["retrieved_ids"]) if rid == r["relevant_id"]),
                    None,
                ),
                "retrieved_chunks": r["retrieved"],
            }
            for r in results
        ],
    }

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "eval_results.json"
        path.write_text(json.dumps(output, indent=2))
        log.info("results written to %s", path)

    print(f"\nRetriever: {retriever.kind}  |  Queries: {len(data)}  |  k={k}")
    for name, val in metrics.items():
        print(f"  {name}: {val:.4f}")

    return output


def main():
    parser = argparse.ArgumentParser(description="RAG retriever evaluation")
    parser.add_argument("--data", type=str, required=True, help="path to JSON data file from generate_data.py")
    parser.add_argument("--k", type=int, default=10, help="max k for retrieval (default 10)")
    parser.add_argument("--out", type=str, default=None, help="directory to write eval_results.json")
    args = parser.parse_args()

    setup_logging()
    settings = load_settings()
    data = json.loads(Path(args.data).read_text())
    out_dir = Path(args.out) if args.out else Path(__file__).parent / "evals"
    run_eval(settings, data=data, k=args.k, out_dir=out_dir)


if __name__ == "__main__":
    main()
