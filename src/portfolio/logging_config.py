"""Local operational logs only: never dump raw payloads, accounts or tokens."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(directory):
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(directory / "money3.log", maxBytes=500000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger = logging.getLogger("portfolio")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
        logger.info("Application started; source access is read-only; no telemetry.")
        return directory / "money3.log"
    except OSError:
        return None
