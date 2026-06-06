from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

import config
from filters import (
    extract_posted_age_text,
    is_allowed_posted_age,
    is_closed_text,
    is_junior_or_intern,
    is_target_location,
    is_target_role,
    parse_job_age_to_days,
)
from models import Job, JobDetails, JobRejected, LinkedInPageSnapshot
from text_utils import contains_any_marker, join_texts, normalize_text

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False


log = logging.getLogger(__name__)

BODY_TEXT_TIMEOUT_MS = 5_000
NETWORK_IDLE_TIMEOUT_MS = 8_000
PAGE_LOAD_TIMEOUT_MS = 20_000
LAZY_CONTENT_WAIT_MS = 600
STATUS_BANNER_WAIT_MS = 2_000

DEFAULT_COMPANY = "N/A"
DEFAULT_LOCATION = "Brasil"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

LINKEDIN_JOB_RE = re.compile(r"linkedin\.com/jobs/view/", re.IGNORECASE)
LINKEDIN_CANONICAL_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/jobs/(view/\d+|view/[^/?#]+)",
    re.IGNORECASE,
)

APPLY_SELECTORS = [
    "button.jobs-apply-button",
    ".jobs-s-apply",
    ".jobs-apply-button",
    "a[href*='/jobs/apply/']",
    "a[href*='trk=public_jobs_apply-link']",
    "a[href*='externalApply']",
    "a[data-tracking-control-name*='apply']",
    "button[aria-label*='Candidatar']",
    "button[aria-label*='candidatar']",
    "button[aria-label*='Apply']",
    "button[aria-label*='apply']",
]

APPLY_TEXT_MARKERS = {
    "candidatar-se",
    "candidatar se",
    "candidate-se",
    "candidatura simplificada",
    "easy apply",
    "apply now",
    "apply for this job",
}

NON_APPLY_TEXT_MARKERS = {
    "salvar",
    "save",
    "compartilhar",
    "share",
    "entrar",
    "sign in",
    "login",
    "join",
    "criar alerta",
    "job alert",
    "alerta de vaga",
}

JOB_TITLE_SELECTORS = [
    "h1.top-card-layout__title",
    "h1.job-title",
    ".top-card-layout__title",
    ".job-details-jobs-unified-top-card__job-title",
    "h1",
]

ERROR_SELECTORS = [
    ".artdeco-inline-feedback--error",
    ".top-card-layout__error-message",
    ".message-bubble",
    ".closed-job-banner",
]

TOP_CARD_SELECTORS = [
    "section.top-card-layout",
    ".job-details-jobs-unified-top-card",
]

WORKPLACE_SELECTORS = [
    ".job-details-jobs-unified-top-card__workplace-type",
    ".topcard__flavor--metadata",
]

COMPANY_SELECTORS = [
    "a.topcard__org-name-link",
    ".job-details-jobs-unified-top-card__company-name",
    ".topcard__flavor--black-link",
]

LOCATION_SELECTORS = [
    ".topcard__flavor--bullet",
    ".job-details-jobs-unified-top-card__bullet",
]


def normalize_linkedin_url(url: str) -> str:
    raw_url = (url or "").strip()
    if not raw_url:
        return ""

    url_without_tracking = remove_url_tracking(raw_url)
    match = LINKEDIN_CANONICAL_URL_RE.search(url_without_tracking)
    if match:
        return f"https://www.linkedin.com/jobs/{match.group(1).rstrip('/')}"
    return url_without_tracking.rstrip("/")


def remove_url_tracking(url: str) -> str:
    parsed_url = urlsplit(url)
    if not parsed_url.scheme or not parsed_url.netloc:
        return url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return urlunsplit((parsed_url.scheme, parsed_url.netloc.lower(), parsed_url.path.rstrip("/"), "", ""))


def is_linkedin_job_url(url: str) -> bool:
    return bool(LINKEDIN_JOB_RE.search(url)) and "/jobs/view/" in normalize_linkedin_url(url).lower()


def job_signature(job: Job) -> str:
    title = normalize_text(job.get("title", "")).strip()
    company = normalize_text(job.get("company", "")).strip()
    location = normalize_text(job.get("location", DEFAULT_LOCATION)).strip() or "brasil"
    return f"sig::{title}|{company}|{location}"


def job_seen_keys(job: Job) -> set[str]:
    url_key = f"url::{normalize_linkedin_url(job.get('url', ''))}"
    return {url_key, job_signature(job)}


def enrich_with_playwright(jobs: list[Job]) -> list[Job]:
    if not PLAYWRIGHT_AVAILABLE:
        return handle_missing_playwright(jobs)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = create_linkedin_page(browser)
            return enrich_jobs_with_page(page, jobs)
        finally:
            browser.close()


def handle_missing_playwright(jobs: list[Job]) -> list[Job]:
    if config.REQUIRE_PLAYWRIGHT_VALIDATION:
        log.warning("Playwright nao instalado; pulando envio para evitar vagas nao validadas.")
        return []
    log.warning("Playwright nao instalado; usando dados do DuckDuckGo sem validacao.")
    return jobs


def create_linkedin_page(browser: Any) -> Any:
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
    open_job_page(page, job["url"])
    snapshot = capture_page_snapshot(page)
    if is_closed_job_page(snapshot):
        raise JobRejected("vaga encerrada ou indisponivel")

    details = extract_job_details(snapshot, job)
    validate_job_details(job, details)
    log.debug("[OK] %s @ %s", details.title, details.company)
    return merge_job_details(job, details)


def open_job_page(page: Any, url: str) -> None:
    page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
    if not is_individual_job_page(page.url):
        raise JobRejected(f"URL redirecionada para pagina nao individual ({page.url})")

    wait_for_network_idle(page)
    reveal_lazy_content(page)
    page.wait_for_timeout(STATUS_BANNER_WAIT_MS)


def is_individual_job_page(url: str) -> bool:
    return "/jobs/view/" in normalize_linkedin_url(url).lower()


def wait_for_network_idle(page: Any) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
    except Exception as exc:
        log.debug("Network idle nao confirmado: %s", exc)


def reveal_lazy_content(page: Any) -> None:
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(LAZY_CONTENT_WAIT_MS)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception as exc:
        log.debug("Conteudo lazy nao foi acionado: %s", exc)


def capture_page_snapshot(page: Any) -> LinkedInPageSnapshot:
    soup = BeautifulSoup(page.content(), "html.parser")
    page_title = read_page_title(page)
    return LinkedInPageSnapshot(
        url=page.url,
        title=page_title,
        soup=soup,
        full_text=collect_full_page_text(page, soup, page_title),
    )


def read_page_title(page: Any) -> str:
    try:
        return page.title()
    except Exception as exc:
        log.debug("Titulo da pagina indisponivel: %s", exc)
        return ""


def collect_full_page_text(page: Any, soup: BeautifulSoup, page_title: str) -> str:
    parts = [soup.get_text(" ", strip=True), read_body_text(page), page_title]
    return " ".join(part for part in parts if part)


def read_body_text(page: Any) -> str:
    try:
        return page.inner_text("body", timeout=BODY_TEXT_TIMEOUT_MS)
    except Exception as exc:
        log.debug("Texto do body indisponivel: %s", exc)
        return ""


def is_closed_job_page(snapshot: LinkedInPageSnapshot) -> bool:
    if "expired_jd_redirect" in normalize_text(snapshot.url):
        return True
    if is_closed_text(snapshot.full_text):
        return True
    if has_error_banner(snapshot.soup):
        return True
    if not has_job_title(snapshot.soup, snapshot.title):
        return True
    return config.REQUIRE_APPLY_EVIDENCE and not has_apply_evidence(snapshot.soup)


def has_error_banner(soup: BeautifulSoup) -> bool:
    return any(soup.select_one(selector) for selector in ERROR_SELECTORS)


def has_job_title(soup: BeautifulSoup, page_title: str) -> bool:
    if select_text(soup, JOB_TITLE_SELECTORS, min_length=8):
        return True

    normalized_title = normalize_text(page_title or "")
    if not normalized_title or "sign in" in normalized_title or "entrar" in normalized_title:
        return False
    return "linkedin" in normalized_title and ("job" in normalized_title or "vaga" in normalized_title)


def has_apply_evidence(soup: BeautifulSoup) -> bool:
    return any(has_apply_signal(element) for element in apply_candidate_elements(soup))


def apply_candidate_elements(soup: BeautifulSoup) -> Iterable[Any]:
    for selector in APPLY_SELECTORS:
        yield from soup.select(selector)
    yield from soup.select("button, a")


def has_apply_signal(element: Any) -> bool:
    href = normalize_text(element.get("href") or "")
    if is_apply_href(href):
        return True

    signal_text = element_signal_text(
        element,
        ["aria-label", "title", "data-tracking-control-name", "data-control-name"],
    )
    return not contains_any_marker(signal_text, NON_APPLY_TEXT_MARKERS) and contains_any_marker(
        signal_text,
        APPLY_TEXT_MARKERS,
    )


def is_apply_href(href: str) -> bool:
    return "/jobs/apply/" in href or "public_jobs_apply-link" in href or "externalapply" in href


def element_signal_text(element: Any, attributes: Iterable[str]) -> str:
    parts = [element.get_text(" ", strip=True)]
    parts.extend(element.get(attribute) or "" for attribute in attributes)
    return " ".join(str(part) for part in parts if part)


def select_text(soup: BeautifulSoup, selectors: Iterable[str], min_length: int = 1) -> str:
    for selector in selectors:
        element = soup.select_one(selector)
        text = element.get_text(" ", strip=True) if element else ""
        if len(text) >= min_length:
            return text
    return ""


def extract_job_details(snapshot: LinkedInPageSnapshot, fallback_job: Job) -> JobDetails:
    top_card_text = select_text(snapshot.soup, TOP_CARD_SELECTORS)
    workplace = select_text(snapshot.soup, WORKPLACE_SELECTORS)
    posted_age = extract_posted_age_text(top_card_text) or extract_posted_age_text(snapshot.full_text)

    return JobDetails(
        title=extract_job_title(snapshot, fallback_job),
        company=extract_company(snapshot),
        location=extract_location(snapshot),
        workplace=workplace,
        posted_age=posted_age,
        top_card_text=top_card_text,
    )


def extract_job_title(snapshot: LinkedInPageSnapshot, fallback_job: Job) -> str:
    return (
        select_text(snapshot.soup, JOB_TITLE_SELECTORS)
        or extract_title_from_page_title(snapshot.title)
        or fallback_job["title"]
    )


def extract_company(snapshot: LinkedInPageSnapshot) -> str:
    return (
        select_text(snapshot.soup, COMPANY_SELECTORS)
        or extract_company_from_page_title(snapshot.title)
        or DEFAULT_COMPANY
    )


def extract_location(snapshot: LinkedInPageSnapshot) -> str:
    return select_text(snapshot.soup, LOCATION_SELECTORS) or extract_location_from_page_title(snapshot.title)


def extract_title_from_page_title(page_title: str) -> str:
    if "|" not in page_title:
        return ""
    return page_title.split("|", 1)[0].strip()


def extract_company_from_page_title(page_title: str) -> str:
    if " at " not in page_title:
        return ""
    return page_title.split(" at ", 1)[1].split("|", 1)[0].strip()


def extract_location_from_page_title(page_title: str) -> str:
    title_parts = [part.strip() for part in page_title.split("|")]
    if len(title_parts) < 3:
        return ""
    return title_parts[-2]


def validate_job_details(job: Job, details: JobDetails) -> None:
    scope_text = build_scope_text(job, details)
    if not is_allowed_posted_age(details.posted_age):
        raise JobRejected(posted_age_rejection_reason(details.posted_age))
    if not is_target_location(details.location, scope_text):
        raise JobRejected(location_rejection_reason(details))
    if config.REQUIRE_TARGET_ROLE and not is_target_role(details.title, scope_text):
        raise JobRejected("area fora do escopo tech")
    if not is_junior_or_intern(details.title, scope_text):
        raise JobRejected("nivel nao e junior/estagio")


def build_scope_text(job: Job, details: JobDetails) -> str:
    return join_texts(
        [
            job.get("title", ""),
            job.get("snippet", ""),
            details.top_card_text,
            details.workplace,
        ]
    )


def posted_age_rejection_reason(posted_age: str) -> str:
    if not posted_age:
        return "idade nao identificada"

    days = parse_job_age_to_days(posted_age)
    return f"idade fora do filtro ({posted_age} = ~{days or 0} dias)"


def location_rejection_reason(details: JobDetails) -> str:
    location = details.location or "vazio"
    workplace = details.workplace or "sem modalidade"
    return f"local/remoto fora do filtro ({location} | {workplace})"


def merge_job_details(job: Job, details: JobDetails) -> Job:
    return {
        **job,
        "title": details.title,
        "company": details.company,
        "location": details.location,
        "workplace": details.workplace,
        "posted_age": details.posted_age,
    }
