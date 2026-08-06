from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

import config
from filters import (
    extract_posted_age_text,
    is_allowed_posted_age,
    is_closed_text,
    is_desired_seniority,
    is_target_location,
    is_target_role,
    parse_job_age_to_days,
)
from models import Job, JobDetails, JobRejected, LinkedInPageSnapshot
from text_utils import contains_any_marker, join_texts, normalize_text, normalize_whitespace


log = logging.getLogger(__name__)

BODY_TEXT_TIMEOUT_MS = 5_000
NETWORK_IDLE_TIMEOUT_MS = 8_000
PAGE_LOAD_TIMEOUT_MS = 20_000
LAZY_CONTENT_WAIT_MS = 600
STATUS_BANNER_WAIT_MS = 2_000

DEFAULT_COMPANY = "N/A"
DEFAULT_LOCATION = "Brasil"
DESCRIPTION_MAX_CHARS = 2_000

LINKEDIN_JOB_RE = re.compile(r"linkedin\.com/jobs/view/", re.IGNORECASE)
LINKEDIN_CANONICAL_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/jobs/(view/\d+|view/[^/?#]+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------
#  URLs
# ---------------------------------------------------------------


def job_site_name(url: str) -> str:
    host = (urlsplit(url or "").netloc or "").lower()
    for site_name, clause in config.SITES.items():
        domain = clause.removeprefix("site:").split("/", 1)[0].lower()
        if domain in host:
            return site_name
    return ""


def is_supported_job_url(url: str) -> bool:
    return bool(job_site_name(url))


def normalize_job_url(url: str) -> str:
    raw_url = (url or "").strip()
    if not raw_url:
        return ""

    url_without_tracking = remove_url_tracking(raw_url)
    if job_site_name(url_without_tracking) == "linkedin":
        return normalize_linkedin_job_url(url_without_tracking)
    return url_without_tracking


def remove_url_tracking(url: str) -> str:
    parsed_url = urlsplit(url)
    if not parsed_url.scheme or not parsed_url.netloc:
        return url.split("#", 1)[0].split("?", 1)[0].rstrip("/")

    host = parsed_url.netloc.lower()
    if "indeed.com" in host:
        keep_params = {
            key: values for key, values in parse_qs(parsed_url.query).items() if key == "jk"
        }
    else:
        keep_params = {}
    query = urlencode(keep_params, doseq=True) if keep_params else ""
    return urlunsplit((parsed_url.scheme, host, parsed_url.path.rstrip("/"), query, ""))


def normalize_linkedin_job_url(url: str) -> str:
    match = LINKEDIN_CANONICAL_URL_RE.search(url)
    if match:
        return f"https://www.linkedin.com/jobs/{match.group(1).rstrip('/')}"
    return url


def is_linkedin_job_url(url: str) -> bool:
    return bool(LINKEDIN_JOB_RE.search(url)) and "/jobs/view/" in normalize_linkedin_job_url(
        url
    ).lower()


def is_individual_linkedin_page(url: str) -> bool:
    return "/jobs/view/" in normalize_linkedin_job_url(url).lower()


# ---------------------------------------------------------------
#  Helpers de pagina (compartilhados entre os sites)
# ---------------------------------------------------------------


def open_page(page: Any, url: str) -> None:
    page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
    wait_for_network_idle(page)
    reveal_lazy_content(page)


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


def capture_page_snapshot(page: Any, original_url: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(page.content(), "html.parser")
    page_title = read_page_title(page)
    body_text = read_body_text(page)
    full_text = " ".join(
        part for part in [soup.get_text(" ", strip=True), body_text, page_title] if part
    )
    return {
        "url": page.url,
        "original_url": original_url,
        "title": page_title,
        "soup": soup,
        "full_text": full_text,
    }


def read_page_title(page: Any) -> str:
    try:
        return page.title()
    except Exception as exc:
        log.debug("Titulo da pagina indisponivel: %s", exc)
        return ""


def read_body_text(page: Any) -> str:
    try:
        return page.inner_text("body", timeout=BODY_TEXT_TIMEOUT_MS)
    except Exception as exc:
        log.debug("Texto do body indisponivel: %s", exc)
        return ""


def select_text(soup: BeautifulSoup, selectors: Iterable[str], min_length: int = 1) -> str:
    for selector in selectors:
        element = soup.select_one(selector)
        text = element.get_text(" ", strip=True) if element else ""
        if len(text) >= min_length:
            return text
    return ""


def pick_first(*values: str) -> str:
    for value in values:
        cleaned = normalize_whitespace(value or "")
        if cleaned:
            return cleaned
    return ""


def title_from_page_title(page_title: str) -> str:
    if "|" in page_title:
        return page_title.split("|", 1)[0].strip()
    if "-" in page_title:
        return page_title.split("-", 1)[0].strip()
    return ""


def posted_age_rejection_reason(posted_age: str) -> str:
    if not posted_age:
        return "idade nao identificada"
    days = parse_job_age_to_days(posted_age)
    return f"idade fora do filtro ({posted_age} = ~{days or 0} dias)"


def location_rejection_reason(location: str, workplace: str) -> str:
    return f"local/remoto fora do filtro ({location or 'vazio'} | {workplace or 'sem modalidade'})"


def validate_job_fields(
    job: Job,
    title: str,
    scope_text: str,
    location: str,
    workplace: str,
    posted_age: str,
) -> None:
    if not is_allowed_posted_age(posted_age):
        raise JobRejected(posted_age_rejection_reason(posted_age))
    if not is_target_location(location, scope_text):
        raise JobRejected(location_rejection_reason(location, workplace))
    if config.REQUIRE_TARGET_ROLE and not is_target_role(title, scope_text):
        raise JobRejected("area fora do escopo tech")
    if not is_desired_seniority(title, scope_text):
        raise JobRejected("nivel nao e estagio/junior/pleno")


# ---------------------------------------------------------------
#  LinkedIn
# ---------------------------------------------------------------

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

LINKEDIN_APPLY_TEXT_MARKERS = {
    "candidatar-se",
    "candidatar se",
    "candidate-se",
    "candidatura simplificada",
    "easy apply",
    "apply now",
    "apply for this job",
}

LINKEDIN_NON_APPLY_TEXT_MARKERS = {
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


def enrich_linkedin_job(page: Any, job: Job) -> Job:
    open_linkedin_page(page, job["url"])
    snapshot = capture_page_snapshot(page)
    if is_closed_linkedin_job_page(snapshot):
        raise JobRejected("vaga encerrada ou indisponivel")

    details = extract_linkedin_job_details(snapshot, job)
    scope_text = build_linkedin_scope_text(job, details)
    validate_job_fields(
        job,
        details.title,
        scope_text,
        details.location,
        details.workplace,
        details.posted_age,
    )
    log.debug("[OK] %s @ %s", details.title, details.company)
    return merge_linkedin_job_details(job, details)


def open_linkedin_page(page: Any, url: str) -> None:
    page.goto(url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
    if not is_individual_linkedin_page(page.url):
        raise JobRejected(f"URL redirecionada para pagina nao individual ({page.url})")

    wait_for_network_idle(page)
    reveal_lazy_content(page)
    page.wait_for_timeout(STATUS_BANNER_WAIT_MS)


def is_closed_linkedin_job_page(snapshot: dict[str, Any]) -> bool:
    if "expired_jd_redirect" in normalize_text(snapshot["url"]):
        return True
    if is_closed_text(snapshot["full_text"]):
        return True
    if has_error_banner(snapshot["soup"]):
        return True
    if not has_linkedin_job_title(snapshot["soup"], snapshot["title"]):
        return True
    return config.REQUIRE_APPLY_EVIDENCE and not has_linkedin_apply_evidence(snapshot["soup"])


def has_error_banner(soup: BeautifulSoup) -> bool:
    return any(soup.select_one(selector) for selector in ERROR_SELECTORS)


def has_linkedin_job_title(soup: BeautifulSoup, page_title: str) -> bool:
    if select_text(soup, JOB_TITLE_SELECTORS, min_length=8):
        return True

    normalized_title = normalize_text(page_title or "")
    if not normalized_title or "sign in" in normalized_title or "entrar" in normalized_title:
        return False
    return "linkedin" in normalized_title and (
        "job" in normalized_title or "vaga" in normalized_title
    )


def has_linkedin_apply_evidence(soup: BeautifulSoup) -> bool:
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
    return not contains_any_marker(signal_text, LINKEDIN_NON_APPLY_TEXT_MARKERS) and (
        contains_any_marker(signal_text, LINKEDIN_APPLY_TEXT_MARKERS)
    )


def is_apply_href(href: str) -> bool:
    return "/jobs/apply/" in href or "public_jobs_apply-link" in href or "externalapply" in href


def element_signal_text(element: Any, attributes: Iterable[str]) -> str:
    parts = [element.get_text(" ", strip=True)]
    parts.extend(element.get(attribute) or "" for attribute in attributes)
    return " ".join(str(part) for part in parts if part)


def extract_linkedin_job_details(snapshot: dict[str, Any], fallback_job: Job) -> JobDetails:
    soup = snapshot["soup"]
    top_card_text = select_text(soup, TOP_CARD_SELECTORS)
    workplace = select_text(soup, WORKPLACE_SELECTORS)
    posted_age = extract_posted_age_text(top_card_text) or extract_posted_age_text(
        snapshot["full_text"]
    )

    return JobDetails(
        title=extract_linkedin_job_title(snapshot, fallback_job),
        company=extract_linkedin_company(snapshot),
        location=extract_linkedin_location(snapshot),
        workplace=workplace,
        posted_age=posted_age,
        top_card_text=top_card_text,
    )


def extract_linkedin_job_title(snapshot: dict[str, Any], fallback_job: Job) -> str:
    return (
        select_text(snapshot["soup"], JOB_TITLE_SELECTORS)
        or title_from_page_title(snapshot["title"])
        or fallback_job["title"]
    )


def extract_linkedin_company(snapshot: dict[str, Any]) -> str:
    return (
        select_text(snapshot["soup"], COMPANY_SELECTORS)
        or extract_company_from_page_title(snapshot["title"])
        or DEFAULT_COMPANY
    )


def extract_linkedin_location(snapshot: dict[str, Any]) -> str:
    return select_text(snapshot["soup"], LOCATION_SELECTORS) or extract_location_from_page_title(
        snapshot["title"]
    )


def extract_company_from_page_title(page_title: str) -> str:
    if " at " not in page_title:
        return ""
    return page_title.split(" at ", 1)[1].split("|", 1)[0].strip()


def extract_location_from_page_title(page_title: str) -> str:
    title_parts = [part.strip() for part in page_title.split("|")]
    if len(title_parts) < 3:
        return ""
    return title_parts[-2]


def build_linkedin_scope_text(job: Job, details: JobDetails) -> str:
    return join_texts(
        [
            job.get("title", ""),
            job.get("snippet", ""),
            details.top_card_text,
            details.workplace,
        ]
    )


def merge_linkedin_job_details(job: Job, details: JobDetails) -> Job:
    return {
        **job,
        "title": details.title,
        "company": details.company,
        "location": details.location,
        "workplace": details.workplace,
        "posted_age": details.posted_age,
        "source": job_site_name(job["url"]),
    }


# ---------------------------------------------------------------
#  Demais sites (parsing generico com JSON-LD + fallback DOM)
# ---------------------------------------------------------------

GENERIC_APPLY_TEXT_MARKERS = {
    "candidatar-se",
    "candidatar se",
    "candidate-se",
    "candidate se",
    "candidatura",
    "quero me candidatar",
    "candidatar",
    "inscrever-se",
    "inscrever se",
    "inscreva-se",
    "inscreva se",
    "inscricao",
    "apply now",
    "apply for this job",
    "easy apply",
    "aplicar",
    "enviar curriculo",
    "enviar currículo",
    "participar da vaga",
}

GENERIC_NON_APPLY_TEXT_MARKERS = {
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
    "seguir",
    "follow",
}

COMPANY_CLASS_HINTS = (
    "company",
    "employer",
    "org-name",
    "enterprise",
    "empresa",
    "contratante",
)

LOCATION_CLASS_HINTS = (
    "location",
    "local",
    "place",
    "cidade",
)

WORKPLACE_CLASS_HINTS = (
    "workplace",
    "work-type",
    "modalidade",
    "work-model",
)

LOCATION_LABEL_RE = re.compile(
    r"\b(?:localizacao|localização|endereco|endereço|cidade)\s*:?\s*([^|·•;\n]{2,80})",
    re.IGNORECASE,
)


def enrich_generic_job(page: Any, job: Job) -> Job:
    open_page(page, job["url"])
    snapshot = capture_page_snapshot(page, job["url"])
    if is_closed_generic_job_page(snapshot):
        raise JobRejected("vaga encerrada ou indisponivel")

    details = extract_generic_job_details(snapshot, job)
    scope_text = build_generic_scope_text(job, details)
    validate_job_fields(
        job,
        details["title"],
        scope_text,
        details["location"],
        details["workplace"],
        details["posted_age"],
    )
    log.debug("[OK] %s @ %s", details["title"], details["company"])
    return merge_generic_job_details(job, details)


def is_closed_generic_job_page(snapshot: dict[str, Any]) -> bool:
    if host_changed(snapshot["original_url"], snapshot["url"]):
        log.debug("URL redirecionada para outro dominio: %s", snapshot["url"])
        return True
    if is_closed_text(snapshot["full_text"]):
        return True
    if not has_generic_job_title(snapshot["soup"]):
        return True
    if config.REQUIRE_APPLY_EVIDENCE and not has_generic_apply_evidence(snapshot["soup"]):
        return True
    return False


def host_changed(original_url: str, current_url: str) -> bool:
    original_host = (urlsplit(original_url or "").netloc or "").lower()
    current_host = (urlsplit(current_url or "").netloc or "").lower()
    return bool(original_host) and current_host != original_host


def has_generic_job_title(soup: BeautifulSoup) -> bool:
    return bool(extract_generic_title(soup))


def has_generic_apply_evidence(soup: BeautifulSoup) -> bool:
    for element in soup.select("button, a, [role='button']"):
        if not element.get_text(" ", strip=True):
            continue
        signal_text = element.get_text(" ", strip=True)
        if element.get("aria-label"):
            signal_text += " " + str(element.get("aria-label"))
        if contains_any_marker(signal_text, GENERIC_NON_APPLY_TEXT_MARKERS):
            continue
        if contains_any_marker(signal_text, GENERIC_APPLY_TEXT_MARKERS):
            return True
    return False


def extract_generic_job_details(snapshot: dict[str, Any], fallback_job: Job) -> dict[str, str]:
    soup = snapshot["soup"]
    jsonld = first_jsonld_job(soup)

    title = pick_first(
        jsonld_title(jsonld),
        extract_generic_title(soup),
        title_from_page_title(snapshot["title"]),
        fallback_job["title"],
    )
    company = pick_first(
        jsonld_company(jsonld),
        extract_generic_company(soup),
        title_from_page_title(snapshot["title"]),
        DEFAULT_COMPANY,
    )
    location = pick_first(
        jsonld_location(jsonld),
        extract_generic_location(soup, snapshot["full_text"]),
        DEFAULT_LOCATION,
    )
    workplace = pick_first(
        jsonld_workplace(jsonld),
        extract_generic_workplace(soup),
        "",
    )
    posted_age = pick_first(
        jsonld_posted_age(jsonld),
        extract_posted_age_text(snapshot["full_text"]),
        "",
    )
    description = jsonld_description(jsonld) or ""

    return {
        "title": title,
        "company": company,
        "location": location,
        "workplace": workplace,
        "posted_age": posted_age,
        "description": description,
    }


def first_jsonld_job(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in walk_jsonld(data):
            job_type = item.get("@type")
            if job_type == "JobPosting" or (
                isinstance(job_type, list) and "JobPosting" in job_type
            ):
                return item
    return None


def walk_jsonld(data: Any):
    if isinstance(data, list):
        for entry in data:
            yield from walk_jsonld(entry)
    elif isinstance(data, dict):
        yield data
        for value in data.values():
            yield from walk_jsonld(value)


def jsonld_title(jsonld: dict[str, Any] | None) -> str:
    if not jsonld:
        return ""
    title = jsonld.get("title")
    return title if isinstance(title, str) else ""


def jsonld_company(jsonld: dict[str, Any] | None) -> str:
    if not jsonld:
        return ""
    org = jsonld.get("hiringOrganization")
    if isinstance(org, str):
        return org
    if isinstance(org, dict):
        name = org.get("name")
        if isinstance(name, str):
            return name
    return ""


def jsonld_location(jsonld: dict[str, Any] | None) -> str:
    if not jsonld:
        return ""
    location = jsonld.get("jobLocation")
    if isinstance(location, dict):
        return compose_jsonld_address(location)
    if isinstance(location, list):
        parts = [compose_jsonld_address(item) for item in location if isinstance(item, dict)]
        return ", ".join(part for part in parts if part)
    return ""


def compose_jsonld_address(location: dict[str, Any]) -> str:
    address = location.get("address")
    if isinstance(address, str):
        return address
    if isinstance(address, dict):
        parts = []
        for key in ("addressLocality", "addressRegion", "postalCode", "addressCountry"):
            value = address.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return ", ".join(parts)
    return ""


def jsonld_workplace(jsonld: dict[str, Any] | None) -> str:
    if not jsonld:
        return ""
    location_type = jsonld.get("jobLocationType")
    if isinstance(location_type, list):
        location_type = ", ".join(location_type)
    if isinstance(location_type, str) and location_type.strip():
        return map_workplace_type(location_type)
    return ""


def map_workplace_type(location_type: str) -> str:
    normalized = normalize_text(location_type)
    if "telecommute" in normalized or "remote" in normalized:
        return "Remoto"
    if "hybrid" in normalized:
        return "Hibrido"
    if "on" in normalized and "site" in normalized:
        return "Presencial"
    return location_type


def jsonld_posted_age(jsonld: dict[str, Any] | None) -> str:
    if not jsonld:
        return ""
    date_posted = jsonld.get("datePosted")
    if not isinstance(date_posted, str):
        return ""
    date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", date_posted)
    if not date_match:
        return ""
    year, month, day = date_match.groups()
    return f"publicada em {day}/{month}/{year}"


def jsonld_description(jsonld: dict[str, Any] | None) -> str:
    if not jsonld:
        return ""
    description = jsonld.get("description")
    if not isinstance(description, str):
        return ""
    text = BeautifulSoup(description, "html.parser").get_text(" ", strip=True)
    return text[:DESCRIPTION_MAX_CHARS]


def extract_generic_title(soup: BeautifulSoup) -> str:
    heading = soup.select_one("h1")
    if heading:
        text = heading.get_text(" ", strip=True)
        if len(text) >= 8:
            return text
    for selector in ("meta[property='og:title']", "meta[name='twitter:title']"):
        meta = soup.select_one(selector)
        if meta and meta.get("content"):
            text = str(meta["content"]).strip()
            if len(text) >= 8:
                return text
    return ""


def extract_generic_company(soup: BeautifulSoup) -> str:
    for element in soup.select("a, span, div"):
        class_hints = " ".join(element.get("class") or [])
        if any(hint in class_hints for hint in COMPANY_CLASS_HINTS):
            text = element.get_text(" ", strip=True)
            if 2 <= len(text) <= 80:
                return text
    meta = soup.select_one("meta[property='og:site_name']")
    if meta and meta.get("content"):
        return str(meta["content"]).strip()
    return ""


def extract_generic_location(soup: BeautifulSoup, full_text: str) -> str:
    for element in soup.select("li, span, div, p"):
        class_hints = " ".join(element.get("class") or [])
        test_ids = " ".join(
            str(element.get(key) or "") for key in ("data-testid", "data-test", "id")
        )
        if not any(hint in f"{class_hints} {test_ids}".lower() for hint in LOCATION_CLASS_HINTS):
            continue
        text = element.get_text(" ", strip=True)
        if 2 <= len(text) <= 80:
            return text

    match = LOCATION_LABEL_RE.search(full_text)
    if match:
        return normalize_whitespace(match.group(1))
    return ""


def extract_generic_workplace(soup: BeautifulSoup) -> str:
    for element in soup.select("li, span, div, p"):
        class_hints = " ".join(element.get("class") or [])
        if not any(hint in class_hints for hint in WORKPLACE_CLASS_HINTS):
            continue
        text = element.get_text(" ", strip=True)
        if 2 <= len(text) <= 60:
            return text
    return ""


def build_generic_scope_text(job: Job, details: dict[str, str]) -> str:
    return " ".join(
        part
        for part in [
            job.get("title", ""),
            job.get("snippet", ""),
            details["title"],
            details["description"],
            details["location"],
            details["workplace"],
        ]
        if part
    )


def merge_generic_job_details(job: Job, details: dict[str, str]) -> Job:
    return {
        **job,
        "title": details["title"],
        "company": details["company"],
        "location": details["location"],
        "workplace": details["workplace"],
        "posted_age": details["posted_age"],
        "source": job_site_name(job["url"]),
    }
