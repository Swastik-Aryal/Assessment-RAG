import logging
from pathlib import Path

FMT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Console logging, plus an optional run-folder file handler."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    fmt = logging.Formatter(FMT)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
