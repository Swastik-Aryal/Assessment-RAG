"""Jina v5 nano embedding model: separate query/document prompts, normalized 768-d vectors."""
import logging

from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)


class Embedder:
    """Jina v5 nano: document vs query prompts, L2-normalized 768-d vectors."""

    def __init__(self, settings):
        """Load EMBED_MODEL_NAME onto EMBED_DEVICE (first call may download ~0.5 GB)."""
        log.info("loading embedder %s on %s", settings.EMBED_MODEL_NAME, settings.EMBED_DEVICE)
        self.model = SentenceTransformer(
            settings.EMBED_MODEL_NAME, trust_remote_code=True, device=settings.EMBED_DEVICE
        )
        self.dim = int(self.model.get_embedding_dimension())

    def encode_documents(self, texts: list[str]):
        """Embed KB chunks with the document prompt."""
        return self.model.encode(
            texts, prompt_name="document", normalize_embeddings=True, show_progress_bar=False
        )

    def encode_queries(self, texts: list[str]):
        """Embed questions with the query prompt (not the same vector as document)."""
        return self.model.encode(
            texts, prompt_name="query", normalize_embeddings=True, show_progress_bar=False
        )


if __name__ == "__main__":
    import numpy as np

    from pipeline.config.config import load_settings
    from pipeline.config.logging_setup import setup_logging

    setup_logging()
    e = Embedder(load_settings())
    q = e.encode_queries(["multi-factor authentication"])[0]
    d = e.encode_documents(["multi-factor authentication"])[0]
    print("dim", e.dim, "q_norm", float(np.linalg.norm(q)), "d_norm", float(np.linalg.norm(d)))
    assert e.dim == 768, e.dim
    assert abs(np.linalg.norm(q) - 1) < 0.01
    assert abs(np.linalg.norm(d) - 1) < 0.01
    assert not np.allclose(q, d)
    print("embedder ok")
