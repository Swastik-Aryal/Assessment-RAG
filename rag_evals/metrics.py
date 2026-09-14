"""Recall@k and MRR@k over a list of evaluation results."""


def recall_at_k(retrieved_ids: list[str], relevant_id: str, k: int) -> float:
    """1.0 if relevant_id appears in retrieved_ids[:k], else 0.0."""
    return 1.0 if relevant_id in retrieved_ids[:k] else 0.0


def reciprocal_rank(retrieved_ids: list[str], relevant_id: str, k: int) -> float:
    """1/(rank) if relevant_id is in retrieved_ids[:k], else 0.0."""
    for i, rid in enumerate(retrieved_ids[:k]):
        if rid == relevant_id:
            return 1.0 / (i + 1)
    return 0.0


def aggregate(
    results: list[dict],
) -> dict[str, float]:
    """Compute Recall@1, Recall@5, Recall@10, MRR@5 from a list of per-query dicts.

    Each dict has keys: retrieved_ids (list[str]), relevant_id (str).
    """
    n = len(results)
    if n == 0:
        return {"recall@1": 0.0, "recall@5": 0.0, "recall@10": 0.0, "mrr@5": 0.0}
    r1 = sum(recall_at_k(r["retrieved_ids"], r["relevant_id"], 1) for r in results) / n
    r5 = sum(recall_at_k(r["retrieved_ids"], r["relevant_id"], 5) for r in results) / n
    r10 = sum(recall_at_k(r["retrieved_ids"], r["relevant_id"], 10) for r in results) / n
    mrr5 = sum(reciprocal_rank(r["retrieved_ids"], r["relevant_id"], 5) for r in results) / n
    return {"recall@1": r1, "recall@5": r5, "recall@10": r10, "mrr@5": mrr5}
