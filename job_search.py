from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from ddgs import DDGS

import config
from filters import has_known_blocked_age, is_closed_text, is_junior_or_intern, is_target_role
from linkedin import is_linkedin_job_url, normalize_linkedin_url
from models import Job


log = logging.getLogger(__name__)

DEFAULT_SEARCH_TITLE = "Vaga LinkedIn"


def ddg_search() -> list[Job]:
    results: list[Job] = []
    seen_urls: set[str] = set()

    with DDGS() as duckduckgo:
        for query in config.SEARCH_QUERIES:
            results.extend(search_query_jobs(duckduckgo, query, seen_urls))

    log.info("🔎  %d vagas únicas encontradas via DDG", len(results))
    return results


def search_query_jobs(duckduckgo: DDGS, query: str, seen_urls: set[str]) -> list[Job]:
    log.info("🔍  Buscando: %s", query)
    try:
        hits = list(duckduckgo.text(query, max_results=config.MAX_RESULTS_PER_QUERY) or [])
    except Exception as exc:
        log.warning("Erro na busca DDG ('%s'): %s", query, exc)
        return []

    jobs = accept_search_hits(hits, seen_urls)
    log.info("✅ Query '%s': %d/%d links aproveitados", query, len(jobs), len(hits))
    return jobs


def accept_search_hits(hits: Iterable[Mapping[str, Any]], seen_urls: set[str]) -> list[Job]:
    accepted_jobs: list[Job] = []
    for hit in hits:
        job = parse_search_hit(hit)
        if not is_relevant_search_job(job):
            continue

        canonical_job = canonicalize_search_job(job)
        if canonical_job["url"] in seen_urls:
            continue

        seen_urls.add(canonical_job["url"])
        accepted_jobs.append(canonical_job)
    return accepted_jobs


def parse_search_hit(hit: Mapping[str, Any]) -> Job:
    return {
        "url": str(hit.get("href") or "").strip(),
        "title": str(hit.get("title") or DEFAULT_SEARCH_TITLE).strip(),
        "snippet": str(hit.get("body") or "").strip(),
    }


def is_relevant_search_job(job: Job) -> bool:
    result_text = f"{job['title']} {job['snippet']}"
    if not job["url"] or not is_linkedin_job_url(job["url"]):
        return False
    if is_closed_text(result_text):
        return False
    if has_known_blocked_age(result_text):
        return False
    if not is_junior_or_intern(job["title"], job["snippet"]):
        return False
    return not config.REQUIRE_TARGET_ROLE or is_target_role(job["title"], job["snippet"])


def canonicalize_search_job(job: Job) -> Job:
    return {
        **job,
        "url": normalize_linkedin_url(job["url"]),
    }
