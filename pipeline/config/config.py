from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Pipeline knobs from `.env`. Relative paths resolve against the repo root."""
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    MAIL_BASE_URL: str = "http://127.0.0.1:8025"
    PORTAL_BASE_URL: str = "http://127.0.0.1:8080"
    PIPELINE_ADDRESS: str = "pipeline@securitypal.test"
    CLIENT_ADDRESS: str = "vendor-assessments@acme-client.test"
    PORTAL_ACCESS_ADDRESS: str = "access@portal.acme-client.test"
    PORTAL_USERNAME: str = "securitypal"
    PORTAL_HEADLESS: bool = True
    PORTAL_SLOW_MO_MS: int = 0
    GEMINI_API_KEY: str
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_MIN_INTERVAL_SECONDS: float = 0
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen3.5:4b"
    OLLAMA_TIMEOUT_SECONDS: float = 180
    EMBED_MODEL_NAME: str = "jinaai/jina-embeddings-v5-text-nano-retrieval"
    EMBED_DEVICE: str = "cpu"
    KB_PATH: Path = Path("data/knowledge_base/ClientABC _ ATB Financial_Knowledge Base.xlsx")
    QDRANT_PATH: Path = Path("cache/qdrant")
    TOP_K: int = 5
    RETRIEVER: str = "dense"
    DENSE_WEIGHT: float = 0.7
    POLL_INTERVAL_SECONDS: float = 3
    OUTPUT_DIR: Path = Path("outputs")
    LOG_LEVEL: str = "INFO"

    @model_validator(mode="after")
    def _abs_paths(self):
        """Make KB_PATH, QDRANT_PATH, and OUTPUT_DIR absolute."""
        for name in ("KB_PATH", "QDRANT_PATH", "OUTPUT_DIR"):
            p = getattr(self, name)
            if not p.is_absolute():
                setattr(self, name, REPO_ROOT / p)
        return self


def load_settings() -> Settings:
    """Load Settings from env / `.env`."""
    return Settings()


if __name__ == "__main__":
    s = load_settings()
    for k, v in s.model_dump().items():
        print(f"{k}={'****' if k == 'GEMINI_API_KEY' else v}")
