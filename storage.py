from __future__ import annotations

import json
import logging
from pathlib import Path

import config


log = logging.getLogger(__name__)


def load_seen() -> set[str]:
    seen_jobs_file = Path(config.SEEN_JOBS_FILE)
    if not seen_jobs_file.exists():
        return set()

    try:
        return set(json.loads(seen_jobs_file.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        log.warning("Arquivo de vagas vistas invalido (%s): %s", seen_jobs_file, exc)
    except OSError as exc:
        log.warning("Nao foi possivel ler %s: %s", seen_jobs_file, exc)
    return set()


def save_seen(seen: set[str]) -> None:
    Path(config.SEEN_JOBS_FILE).write_text(
        json.dumps(sorted(seen), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
