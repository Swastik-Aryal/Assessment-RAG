import logging
from pathlib import Path

FMT = "%(asctime)s | %(levelname)s | %(message)s"
DATEFMT = "%H:%M:%S"
_NOISY = (
    "httpx",
    "httpcore",
    "google_genai",
    "google.genai",
    "jax",
    "jax._src.xla_bridge",
    "tensorflow",
    "tf_keras",
    "datasets",
    "urllib3",
    "filelock",
    "transformers",
    "sentence_transformers",
    "PIL",
    "PIL.PngImagePlugin",
)


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Console and runs.log share `level` so they stay identical. DEBUG = verbose."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    fmt = logging.Formatter(FMT, datefmt=DATEFMT)
    want = level.upper()
    console = logging.StreamHandler()
    console.setLevel(want)
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(want)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)


def add_run_log(log_file: Path, level: str = "DEBUG"):
    """Generation folder log. DEBUG so per-question lines are always there."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter(FMT, datefmt=DATEFMT))
    fh.setLevel(level.upper())
    logging.getLogger().addHandler(fh)
    return fh


def remove_run_log(fh) -> None:
    logging.getLogger().removeHandler(fh)
    fh.close()
