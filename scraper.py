"""
Orquestra a busca de vagas tech remotas e o envio de alertas por e-mail.
"""

from __future__ import annotations

import io
import logging
import schedule
import sys
import time
from collections.abc import Iterable
from datetime import datetime

import config
from emailer import send_email
from job_search import ddg_search
from linkedin import enrich_with_playwright, job_seen_keys
from models import Job
from storage import load_seen, save_seen


SCHEDULER_SLEEP_SECONDS = 30


def configure_stdout() -> None:
    if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        ],
    )


configure_stdout()
configure_logging()
log = logging.getLogger(__name__)


def run_job() -> None:
    log.info("=" * 60)
    log.info("▶  Iniciando varredura  %s", datetime.now().isoformat(sep=" ", timespec="seconds"))

    seen_jobs = load_seen()
    jobs = enrich_with_playwright(ddg_search())
    new_jobs = filter_new(jobs, seen_jobs)

    send_email(new_jobs)
    remember_seen_jobs(seen_jobs, jobs)
    log.info("✔  Ciclo concluído.\n")


def filter_new(jobs: list[Job], seen: set[str]) -> list[Job]:
    new_jobs = [job for job in jobs if not (job_seen_keys(job) & seen)]
    log.info("[NEW]  %d vagas novas (de %d encontradas)", len(new_jobs), len(jobs))
    return new_jobs


def remember_seen_jobs(seen_jobs: set[str], jobs: Iterable[Job]) -> None:
    for job in jobs:
        seen_jobs.update(job_seen_keys(job))
    save_seen(seen_jobs)


def main() -> None:
    if should_run_once(sys.argv):
        run_once()
        return
    run_continuously()


def should_run_once(argv: Iterable[str]) -> bool:
    return "--once" in argv


def run_once() -> None:
    log.info("🚀  LinkedIn Job Scraper iniciado")
    run_job()


def run_continuously() -> None:
    run_once()
    schedule.every(config.RUN_INTERVAL_MINUTES).minutes.do(run_job)
    log.info(
        "⏰  Proxima execucao agendada a cada %s minuto(s)",
        config.RUN_INTERVAL_MINUTES,
    )
    wait_for_scheduled_jobs()


def wait_for_scheduled_jobs() -> None:
    while True:
        schedule.run_pending()
        time.sleep(SCHEDULER_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
