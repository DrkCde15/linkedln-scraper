"""
Orquestra a validacao das vagas com Playwright (navegacao e checagem
de disponibilidade) e a identidade das vagas para deduplicacao.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import config
import sites
from models import Job, JobRejected
from text_utils import normalize_text

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False


log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def job_signature(job: Job) -> str:
    title = normalize_text(job.get("title", "")).strip()
    company = normalize_text(job.get("company", "")).strip()
    location = normalize_text(job.get("location", sites.DEFAULT_LOCATION)).strip() or "brasil"
    return f"sig::{title}|{company}|{location}"


def job_seen_keys(job: Job) -> set[str]:
    url_key = f"url::{sites.normalize_job_url(job.get('url', ''))}"
    return {url_key, job_signature(job)}


def enrich_with_playwright(jobs: list[Job]) -> list[Job]:
    if not PLAYWRIGHT_AVAILABLE:
        return handle_missing_playwright(jobs)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = create_browser_page(browser)
            return enrich_jobs_with_page(page, jobs)
        finally:
            browser.close()


def handle_missing_playwright(jobs: list[Job]) -> list[Job]:
    if config.REQUIRE_PLAYWRIGHT_VALIDATION:
        log.warning("Playwright nao instalado; pulando envio para evitar vagas nao validadas.")
        return []
    log.warning("Playwright nao instalado; usando dados do DuckDuckGo sem validacao.")
    return jobs


def create_browser_page(browser: Any) -> Any:
    context = browser.new_context(user_agent=USER_AGENT, locale="pt-BR")
    return context.new_page()


def enrich_jobs_with_page(page: Any, jobs: Iterable[Job]) -> list[Job]:
    enriched_jobs: list[Job] = []
    for job in jobs:
        try:
            enriched_jobs.append(enrich_job(page, job))
        except JobRejected as exc:
            log.info("[SKIP] %s: %s", exc, job["url"])
        except PlaywrightTimeoutError:
            log.warning("[SKIP] Timeout ao validar vaga: %s", job["url"])
        except Exception as exc:
            log.warning("[SKIP] Erro ao validar vaga %s: %s", job["url"], exc)
    return enriched_jobs


def enrich_job(page: Any, job: Job) -> Job:
    if sites.is_linkedin_job_url(job["url"]):
        return sites.enrich_linkedin_job(page, job)
    return sites.enrich_generic_job(page, job)
