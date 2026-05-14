"""
scraper.py  –  Busca vagas tech remotas no LinkedIn via DuckDuckGo
              e envia e-mail com as novidades.

Estratégia:
  1. DuckDuckGo Search  →  encontra URLs do linkedin.com/jobs sem precisar
                            de login nem cookie (mais confiável que Selenium direto)
  2. Playwright (headless) →  abre cada URL do LinkedIn para extrair título,
                               empresa e localidade reais (fallback: título do DDG)
  3. BeautifulSoup        →  faz o parse do HTML retornado pelo Playwright
  4. smtplib              →  envia e-mail HTML com a lista de novas vagas
  5. schedule             →  agenda execução diária
"""

from __future__ import annotations
import json
import logging
import re
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from ddgs import DDGS
import schedule
import time
import unicodedata
from bs4 import BeautifulSoup

# Playwright é opcional – se não estiver instalado faz fallback gracioso
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

import config
import io

# Tenta configurar o stdout para UTF-8 para evitar erros com emojis no Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """Normalize text for accent-insensitive comparisons."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def contains_any_marker(text: str, markers: set[str] | list[str] | tuple[str, ...]) -> bool:
    """Return True when a normalized marker appears as a phrase or whole word."""
    normalized = normalize_text(text or "")
    if not normalized:
        return False

    for marker in markers:
        marker_norm = normalize_text(marker)
        if not marker_norm:
            continue
        if re.search(r"[^\w\s]", marker_norm) or " " in marker_norm:
            if marker_norm in normalized:
                return True
            continue
        if re.search(rf"\b{re.escape(marker_norm)}\b", normalized):
            return True
    return False


CLOSED_JOB_MARKERS = [
    "nao aceita mais candidaturas",
    "nao esta mais aceitando candidaturas",
    "nao estamos mais aceitando candidaturas",
    "nao recebe mais candidaturas",
    "nao esta recebendo candidaturas",
    "inscricoes encerradas",
    "candidaturas encerradas",
    "vaga encerrada",
    "vaga expirada",
    "vaga indisponivel",
    "vaga pausada",
    "processo seletivo encerrado",
    "esta vaga foi encerrada",
    "esta vaga nao esta mais disponivel",
    "essa vaga nao esta mais disponivel",
    "no acepta mas candidaturas",
    "ya no acepta solicitudes",
    "inscripciones cerradas",
    "this job is no longer available",
    "no longer accepting applications",
    "applications are closed",
    "application period has ended",
    "position has been filled",
    "job has expired",
    "job expired",
    "this job has closed",
    "this position has been closed",
    "posting has expired",
    "posting expired",
    "job closed",
    "not accepting applications",
    "hiring has concluded",
    "we couldn't find this job",
    "we couldnt find this job",
    "we could not find this job",
    "we couldn't find that job",
    "we couldnt find that job",
    "nao encontramos esta vaga",
    "nao encontramos essa vaga",
    "job posting is no longer available",
    "this job posting is no longer available",
    "vaga nao encontrada",
    "pagina nao encontrada",
    "page not found",
    "this page does not exist",
    "this page doesn't exist",
    "something went wrong",
    "no matching jobs found",
    "expired_jd_redirect",
]

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

TITLE_SELECTORS = [
    "h1.top-card-layout__title",
    "h1.job-title",
    ".top-card-layout__title",
    ".job-details-jobs-unified-top-card__job-title",
    "h1",
]


def is_closed_text(text: str) -> bool:
    return contains_any_marker(text, CLOSED_JOB_MARKERS)


def has_apply_evidence(page, soup: BeautifulSoup, full_text: str) -> bool:
    for sel in APPLY_SELECTORS:
        for el in soup.select(sel):
            href = normalize_text(el.get("href") or "")
            if "/jobs/apply/" in href or "public_jobs_apply-link" in href or "externalapply" in href:
                return True

            signal = " ".join(
                str(part)
                for part in [
                    el.get_text(" ", strip=True),
                    el.get("aria-label") or "",
                    el.get("title") or "",
                    el.get("data-tracking-control-name") or "",
                    el.get("data-control-name") or "",
                ]
                if part
            )
            if contains_any_marker(signal, NON_APPLY_TEXT_MARKERS):
                continue
            if contains_any_marker(signal, APPLY_TEXT_MARKERS):
                return True

    # Last pass over visible buttons/links catches minor LinkedIn selector changes
    # without accepting generic full-width sign-in or save buttons.
    for el in soup.select("button, a"):
        signal = " ".join(
            str(part)
            for part in [
                el.get_text(" ", strip=True),
                el.get("aria-label") or "",
                el.get("title") or "",
            ]
            if part
        )
        href = normalize_text(el.get("href") or "")
        if "/jobs/apply/" in href or "public_jobs_apply-link" in href or "externalapply" in href:
            return True
        if contains_any_marker(signal, NON_APPLY_TEXT_MARKERS):
            continue
        if contains_any_marker(signal, APPLY_TEXT_MARKERS):
            return True

    return False


def has_job_title(soup: BeautifulSoup, page_title: str) -> bool:
    for sel in TITLE_SELECTORS:
        el = soup.select_one(sel)
        if el and len(el.get_text(" ", strip=True)) >= 8:
            return True

    title = normalize_text(page_title or "")
    if not title or "sign in" in title or "entrar" in title:
        return False
    return "linkedin" in title and ("job" in title or "vaga" in title)


def is_closed_job_page(page, soup: BeautifulSoup) -> bool:
    """
    Retorna True se a vaga estiver encerrada.
    Verifica texto, banners de erro e a presença de botões de candidatura.
    """
    try:
        current_url = normalize_text(page.url)
        if "expired_jd_redirect" in current_url:
            return True
    except Exception:
        pass

    # 1. Verifica os marcadores de texto tradicionais
    full_text = get_full_page_text(page, soup)
    if is_closed_text(full_text):
        return True

    # 2. Verifica se existem banners de erro/aviso do LinkedIn (comuns em vagas fechadas)
    error_selectors = [
        ".artdeco-inline-feedback--error",
        ".top-card-layout__error-message",
        ".message-bubble",
        ".closed-job-banner"
    ]
    for sel in error_selectors:
        if soup.select_one(sel):
            return True

    # 3. Verifica a ausência de botões de candidatura
    # No LinkedIn para visitantes, o botão costuma ter estas classes ou atributos
    try:
        page_title = page.title()
    except Exception:
        page_title = ""

    # Se nem conseguimos identificar uma vaga individual, nao arriscamos enviar.
    if not has_job_title(soup, page_title):
        return True

    # Se nao ha sinal real de candidatura, provavelmente a vaga fechou ou a
    # pagina caiu em login/erro. Preferimos perder vagas a mandar link morto.
    if config.REQUIRE_APPLY_EVIDENCE and not has_apply_evidence(page, soup, full_text):
        return True

    return False


def get_full_page_text(page, soup: BeautifulSoup) -> str:
    """Collect as much text as possible from the loaded page."""
    parts: list[str] = [soup.get_text(" ", strip=True)]
    try:
        parts.append(page.inner_text("body", timeout=5_000))
    except Exception:
        pass
    try:
        parts.append(page.title())
    except Exception:
        pass
    return " ".join(p for p in parts if p)


TARGET_LOCATIONS_NORM = {
    "sao paulo, brasil",
    "sao paulo, sao paulo, brasil",
    "sao paulo e regiao, brasil",
    "brasil",
    "brazil",
    "remote, brazil",
    "remote - brazil",
    "remoto, brasil",
    "remoto - brasil",
}

BRAZIL_LOCATION_TERMS = {
    "brasil",
    "brazil",
    "sao paulo",
    "rio de janeiro",
    "belo horizonte",
    "curitiba",
    "porto alegre",
    "florianopolis",
    "brasilia",
    "campinas",
    "recife",
    "salvador",
    "fortaleza",
    "goiania",
    "vitoria",
    "barueri",
    "santos",
    "ribeirao preto",
    "sorocaba",
    "minas gerais",
    "rio grande do sul",
    "santa catarina",
    "parana",
    "bahia",
    "pernambuco",
    "ceara",
    "distrito federal",
    "espirito santo",
    "goias",
    "amazonas",
    "mato grosso",
    "mato grosso do sul",
}

REMOTE_MARKERS = {
    "remote",
    "remoto",
    "remota",
    "home office",
    "home-office",
    "work from home",
    "trabalho remoto",
    "anywhere in brazil",
    "anywhere from brazil",
    "100% remote",
    "100% remoto",
}

NON_REMOTE_MARKERS = {
    "presencial",
    "hibrido",
    "hybrid",
    "on-site",
    "on site",
    "onsite",
    "not remote",
    "no remote",
    "nao remoto",
    "nao remota",
}

AGE_PATTERN_PT = re.compile(
    r"\bha\s*\d+\s*(?:minuto|minutos|hora|horas|dia|dias|semana|semanas|mes|meses|ano|anos)\b",
    re.IGNORECASE,
)
AGE_PATTERN_EN = re.compile(
    r"\b\d+\s*(?:minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago\b",
    re.IGNORECASE,
)
AGE_PATTERN_ES = re.compile(
    r"\bhace\s*\d+\s*(?:minuto|minutos|hora|horas|dia|dias|semana|semanas|mes|meses|ano|anos)\b",
    re.IGNORECASE,
)

ALLOWED_AGE_UNITS = (
    "minuto",
    "minutos",
    "minute",
    "minutes",
    "hora",
    "horas",
    "hour",
    "hours",
    "dia",
    "dias",
    "day",
    "days",
    "semana",
    "semanas",
    "week",
    "weeks",
    "mes",
    "meses",
    "month",
    "months",
)


def extract_posted_age_text(text: str) -> str:
    normalized = normalize_text(text or "")
    for pattern in (AGE_PATTERN_PT, AGE_PATTERN_EN, AGE_PATTERN_ES):
        match = pattern.search(normalized)
        if match:
            return match.group(0)
    return ""


def parse_job_age_to_days(age_text: str) -> int | None:
    """
    Converte texto de idade da vaga para número de dias.
    Retorna None se não conseguir parsear.
    """
    if not age_text:
        return None

    age_norm = normalize_text(age_text)

    # Extrai número
    match = re.search(r"(\d+)", age_norm)
    if not match:
        return None
    count = int(match.group(1))

    # Detecta unidade e converte para dias
    if any(u in age_norm for u in ["ano", "anos", "year", "years"]):
        return count * 365
    if any(u in age_norm for u in ["mes", "meses", "month", "months"]):
        return count * 30
    if any(u in age_norm for u in ["semana", "semanas", "week", "weeks"]):
        return count * 7
    if any(u in age_norm for u in ["dia", "dias", "day", "days"]):
        return count
    if any(u in age_norm for u in ["hora", "horas", "hour", "hours"]):
        return 1
    if any(u in age_norm for u in ["minuto", "minutos", "minute", "minutes"]):
        return 1

    return None


def is_allowed_posted_age(age_text: str) -> bool:
    """
    Retorna True se a vaga for recente (até MAX_JOB_AGE_DAYS dias).
    """
    if not age_text:
        return not config.REQUIRE_POSTED_AGE

    days = parse_job_age_to_days(age_text)
    if days is None:
        return not config.REQUIRE_POSTED_AGE

    return days <= config.MAX_JOB_AGE_DAYS


# Locais explicitamente ignorados (fora do Brasil)
EXCLUDED_LOCATIONS = {
    "portugal", "espanha", "spain", "united states", "eua", "usa", "india",
    "reino unido", "united kingdom", "uk", "argentina", "mexico", "madri",
    "lisboa", "porto", "barcelona", "berlim", "berlin", "london", "londres",
    "latam", "latin america", "europe", "emea", "worldwide", "anywhere",
    "canada", "chile", "colombia", "peru", "uruguay", "germany", "france",
    "netherlands", "australia", "singapore", "south africa",
}

SENIORITY_BLOCKLIST = {
    "senior", "principal", "staff", "lead", "architect", "manager", "diretor",
    "especialista", "pleno", "mid-level", "mid level", "experienced",
    "sr", "sr.", "pl", "pl.", "sênior", "coordenador", "coordinator",
    "head", "tech lead", "team lead", "10+ years", "8+ years", "5+ years",
}

JUNIOR_MARKERS = {
    "junior", "jr", "jr.", "estagiario", "estágio", "estagio", "trainee",
    "intern", "interns", "internship", "entry level", "entry-level",
    "starter", "graduate", "recém", "recem", "iniciante", "começante",
    "comecante",
}

TARGET_ROLE_MARKERS = {
    "dados", "data", "analytics", "analyst", "bi", "business intelligence",
    "sql", "cientista de dados", "engenheiro de dados", "machine learning",
    "inteligencia artificial", "ia", "ai", "ml", "llm", "nlp", "backend",
    "back-end", "back end", "developer", "desenvolvedor", "programador",
    "software", "engenheiro de software", "python", "node", "node.js",
    "javascript", "typescript", "api", "cybersecurity", "cyber",
    "seguranca", "segurança", "seguranca da informacao", "security", "soc",
    "appsec", "devsecops", "frontend", "front-end", "front end", "fullstack",
    "full-stack", "react", "reactjs", "web",
}

ROLE_BLOCKLIST = {
    "marketing", "sales", "vendas", "comercial", "financeiro", "contabil",
    "contabilidade", "administrativo", "rh", "recursos humanos", "recruiter",
    "recrutador", "customer success", "sucesso do cliente", "atendimento",
    "suporte", "support", "designer", "product manager", "produto",
}

OUT_OF_SCOPE_TITLE_MARKERS = {
    "banco de talentos",
    "talent pool",
    "cadastro reserva",
    "pipeline",
    "jovem aprendiz",
    "apprentice",
    "data entry",
}

EXPERIENCE_BLOCK_RE = re.compile(
    r"\b(?:[4-9]|[1-9]\d)\+?\s*(?:anos?|years?)\b",
    re.IGNORECASE,
)

def has_brazil_location(text: str) -> bool:
    loc = normalize_text(text)
    loc = re.sub(r"\s+", " ", loc).strip()
    if not loc:
        return False
    if loc in TARGET_LOCATIONS_NORM:
        return True
    if any(term in loc for term in BRAZIL_LOCATION_TERMS):
        return True
    return bool(
        re.search(
            r"\b(?:sp|rj|mg|pr|rs|sc|ba|pe|ce|df|es|ms|mt)\b"
            r"(?:\s*,\s*|\s*-\s*)(?:brasil|brazil)\b",
            loc,
        )
    )


def has_remote_signal(*texts: str) -> bool:
    combined = " ".join(text for text in texts if text)
    return contains_any_marker(combined, REMOTE_MARKERS)


def has_non_remote_signal(*texts: str) -> bool:
    combined = " ".join(text for text in texts if text)
    return contains_any_marker(combined, NON_REMOTE_MARKERS)


def is_target_location(location_text: str, context_text: str = "") -> bool:
    """
    Retorna True somente se a vaga tiver local brasileiro explicito e,
    por padrao, sinal claro de trabalho remoto.
    """
    if not location_text:
        return False

    loc = normalize_text(location_text)
    loc = re.sub(r"\s+", " ", loc).strip()
    context = normalize_text(context_text)

    if contains_any_marker(loc, EXCLUDED_LOCATIONS):
        return False

    if has_brazil_location(loc):
        if not config.REQUIRE_REMOTE:
            return True
        if has_non_remote_signal(loc, context):
            return False
        return has_remote_signal(loc, context)

    return False


def has_seniority_block(text: str) -> bool:
    focused = normalize_text(text)
    return contains_any_marker(focused, SENIORITY_BLOCKLIST) or bool(EXPERIENCE_BLOCK_RE.search(focused))


def is_junior_or_intern(title: str, supporting_text: str) -> bool:
    """
    Retorna True se a vaga for de nível junior ou estágio.
    Usa texto focado no resultado/top card para nao capturar vagas recomendadas.
    """
    combined = f"{title} {supporting_text}"
    if has_seniority_block(combined):
        return False

    return contains_any_marker(combined, JUNIOR_MARKERS)


def is_target_role(title: str, supporting_text: str = "") -> bool:
    """
    Confirma que a vaga pertence ao escopo tech buscado.
    """
    if contains_any_marker(title, OUT_OF_SCOPE_TITLE_MARKERS):
        return False

    combined = f"{title} {supporting_text}"
    has_target = contains_any_marker(combined, TARGET_ROLE_MARKERS)
    if not has_target:
        return False

    # Bloqueia areas claramente fora do recorte quando o alvo tech nao aparece
    # no titulo. Isso evita descartar "Analista de Dados" por conter "analyst"
    # na descricao, mas barra "Analista Financeiro Jr".
    title_has_target = contains_any_marker(title, TARGET_ROLE_MARKERS)
    if not title_has_target and contains_any_marker(title, ROLE_BLOCKLIST):
        return False

    return True


# ── Persistência de vagas já vistas ──────────────────────────────────────────
def load_seen() -> set[str]:
    p = Path(config.SEEN_JOBS_FILE)
    if p.exists():
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(seen: set[str]) -> None:
    Path(config.SEEN_JOBS_FILE).write_text(
        json.dumps(sorted(seen), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Busca via DuckDuckGo ──────────────────────────────────────────────────────
LINKEDIN_JOB_RE = re.compile(
    r"linkedin\.com/jobs/view/",
    re.IGNORECASE,
)

LINKEDIN_CANONICAL_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/jobs/(view/\d+|view/[^/?#]+)",
    re.IGNORECASE,
)


def normalize_linkedin_url(url: str) -> str:
    """Normalize LinkedIn job URLs to reduce duplicates."""
    raw = (url or "").strip()
    if not raw:
        return ""

    base = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    m = LINKEDIN_CANONICAL_URL_RE.search(base)
    if m:
        return f"https://www.linkedin.com/jobs/{m.group(1)}"
    return base


def job_signature(job: dict[str, str]) -> str:
    """Build a stable key from title/company/location."""
    title = normalize_text(job.get("title", "")).strip()
    company = normalize_text(job.get("company", "")).strip()
    location = normalize_text(job.get("location", "Brasil")).strip() or "brasil"
    return f"sig::{title}|{company}|{location}"


def job_seen_keys(job: dict[str, str]) -> set[str]:
    """Keys used to avoid duplicates across runs."""
    url_key = f"url::{normalize_linkedin_url(job.get('url', ''))}"
    return {url_key, job_signature(job)}


def ddg_search() -> list[dict[str, str]]:
    """Retorna lista de {url, title, snippet} de vagas no LinkedIn."""
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    with DDGS() as ddgs:
        for query in config.SEARCH_QUERIES:
            log.info("🔍  Buscando: %s", query)
            try:
                hits = ddgs.text(query, max_results=config.MAX_RESULTS_PER_QUERY) or []
                kept = 0
                total = 0

                for h in hits:
                    total += 1
                    url = (h.get("href") or "").strip()
                    title = (h.get("title") or "Vaga LinkedIn").strip()
                    snippet = (h.get("body") or "").strip()

                    if not url:
                        continue

                    result_text = f"{title} {snippet}"
                    if is_closed_text(result_text):
                        continue

                    posted_age = extract_posted_age_text(result_text)
                    if posted_age and not is_allowed_posted_age(posted_age):
                        continue

                    if not is_junior_or_intern(title, snippet):
                        continue

                    if config.REQUIRE_TARGET_ROLE and not is_target_role(title, snippet):
                        continue

                    # Debug útil para entender o que o buscador está trazendo
                    log.debug("DDG hit: %s", url)

                    # Aceita variações comuns de URL de vagas no LinkedIn
                    if "linkedin.com/jobs" not in url.lower():
                        continue
                    if not LINKEDIN_JOB_RE.search(url):
                        continue

                    # Normaliza URL para reduzir duplicidade entre dom?nios/params
                    clean_url = normalize_linkedin_url(url)
                    if "/jobs/view/" not in clean_url.lower():
                        continue
                    if clean_url in seen_urls:
                        continue

                    seen_urls.add(clean_url)
                    results.append(
                        {
                            "url": clean_url,
                            "title": title,
                            "snippet": snippet,
                        }
                    )
                    kept += 1

                log.info("✅ Query '%s': %d/%d links aproveitados", query, kept, total)

            except Exception as exc:
                log.warning("Erro na busca DDG ('%s'): %s", query, exc)

    log.info("🔎  %d vagas únicas encontradas via DDG", len(results))
    return results


# ── Enriquecimento via Playwright + BeautifulSoup ─────────────────────────────
def enrich_with_playwright(jobs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Visita cada URL e tenta extrair título, empresa e localidade do LinkedIn."""
    if not PLAYWRIGHT_AVAILABLE:
        if config.REQUIRE_PLAYWRIGHT_VALIDATION:
            log.warning("Playwright nao instalado; pulando envio para evitar vagas nao validadas.")
            return []
        log.warning("Playwright nao instalado; usando dados do DuckDuckGo sem validacao.")
        return jobs

    enriched = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
        )
        page = ctx.new_page()

        for job in jobs:
            try:
                page.goto(job["url"], timeout=20_000, wait_until="domcontentloaded")
                final_url = normalize_linkedin_url(page.url)
                if "/jobs/view/" not in final_url.lower():
                    log.info("[SKIP] URL redirecionada para pagina nao individual: %s", page.url)
                    continue
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass

                # Scroll to trigger lazy content before reading full page text.
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(600)
                    page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

                page.wait_for_timeout(2000) # Espera 2s para o JS carregar banners de erro/status
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                if is_closed_job_page(page, soup):
                    log.info("[CLOSED] Vaga encerrada ou indisponivel, ignorando: %s", job["url"])
                    continue

                full_page_text = get_full_page_text(page, soup)

                # Seletores LinkedIn (podem mudar com redesigns)
                def _text(sel: str) -> str:
                    el = soup.select_one(sel)
                    return el.get_text(" ", strip=True) if el else ""

                top_card_text = (
                    _text("section.top-card-layout")
                    or _text(".job-details-jobs-unified-top-card")
                    or ""
                )
                workplace = (
                    _text(".job-details-jobs-unified-top-card__workplace-type")
                    or _text(".topcard__flavor--metadata")
                    or ""
                )

                posted_age = extract_posted_age_text(top_card_text) or extract_posted_age_text(full_page_text)
                if not is_allowed_posted_age(posted_age):
                    days = parse_job_age_to_days(posted_age)
                    reason = "idade nao identificada" if not posted_age else f"idade fora do filtro ({posted_age} = ~{days or 0} dias)"
                    log.info("[SKIP] %s: %s", reason, job["url"])
                    continue

                # Fallback: tentar extrair do Título da página se os seletores falharem
                page_title = ""
                try:
                    page_title = page.title()
                except:
                    pass

                title   = (
                    _text("h1.top-card-layout__title")
                    or _text("h1.job-title")
                    or _text("h1")
                    or (page_title.split("|")[0].strip() if "|" in page_title else "")
                    or job["title"]
                )
                company = (
                    _text("a.topcard__org-name-link")
                    or _text(".job-details-jobs-unified-top-card__company-name")
                    or _text(".topcard__flavor--black-link")
                    or (page_title.split("at")[-1].split("|")[0].strip() if " at " in page_title else "N/A")
                    or "N/A"
                )
                location = (
                    _text(".topcard__flavor--bullet")
                    or _text(".job-details-jobs-unified-top-card__bullet")
                    or (page_title.split("|")[-2].strip() if page_title.count("|") >= 2 else "")
                    or ""
                )

                focused_scope_text = " ".join(
                    part
                    for part in [
                        job.get("title", ""),
                        job.get("snippet", ""),
                        top_card_text,
                        workplace,
                    ]
                    if part
                )

                if not is_target_location(location, focused_scope_text):
                    log.info("[SKIP] Local/remoto fora do filtro (%s | %s): %s", location or "vazio", workplace or "sem modalidade", job["url"])
                    continue

                if config.REQUIRE_TARGET_ROLE and not is_target_role(title, focused_scope_text):
                    log.info("[SKIP] Area fora do escopo tech: %s", job["url"])
                    continue

                if not is_junior_or_intern(title, focused_scope_text):
                    log.info("[SKIP] Nível não é junior/estágio: %s", job["url"])
                    continue

                enriched.append(
                    {
                        **job,
                        "title":    title,
                        "company":  company,
                        "location": location,
                        "workplace": workplace,
                        "posted_age": posted_age,
                    }
                )
                log.debug("[OK] %s @ %s", title, company)
            except PWTimeout:
                log.warning("[SKIP] Timeout ao validar vaga: %s", job["url"])
                continue
            except Exception as exc:
                log.warning("[SKIP] Erro ao validar vaga %s: %s", job["url"], exc)
                continue

        browser.close()

    return enriched


# ── Filtragem de vagas novas ──────────────────────────────────────────────────
def filter_new(jobs: list[dict[str, str]], seen: set[str]) -> list[dict[str, str]]:
    new = [j for j in jobs if not (job_seen_keys(j) & seen)]
    log.info("[NEW]  %d vagas novas (de %d encontradas)", len(new), len(jobs))
    return new


# ── Montagem do e-mail HTML ───────────────────────────────────────────────────
def build_email_html(jobs: list[dict[str, str]]) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    cards = ""
    for j in jobs:
        title    = j.get("title", "Vaga")
        company  = j.get("company", "—")
        location = j.get("location", "Brasil")
        snippet  = j.get("snippet", "")[:200]
        url      = j["url"]

        cards += f"""
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;
                    padding:20px 24px;margin-bottom:16px;">
          <h2 style="margin:0 0 4px;font-size:17px;color:#1a202c;">{title}</h2>
          <p  style="margin:0 0 2px;font-size:14px;color:#4a5568;">
            🏢 {company} &nbsp;|&nbsp; 📍 {location}
          </p>
          <p  style="margin:8px 0 12px;font-size:13px;color:#718096;">{snippet}</p>
          <a  href="{url}" target="_blank"
              style="display:inline-block;background:#0a66c2;color:#fff;
                     text-decoration:none;padding:9px 20px;border-radius:8px;
                     font-size:13px;font-weight:600;">
            Ver vaga no LinkedIn →
          </a>
        </div>"""

    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f7fafc;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="max-width:620px;margin:32px auto;padding:0 16px;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#0a66c2,#0d4f9e);
                border-radius:16px 16px 0 0;padding:28px 32px;text-align:center;">
      <h1 style="color:#fff;margin:0;font-size:22px;letter-spacing:-0.3px;">
        Novas vagas: {config.JOB_ALERT_TITLE}
      </h1>
      <p style="color:#bfdbfe;margin:6px 0 0;font-size:13px;">{now} · São Paulo</p>
    </div>

    <!-- Body -->
    <div style="background:#f7fafc;padding:24px 0;">
      <p style="color:#4a5568;font-size:14px;margin:0 0 20px;text-align:center;">
        Encontramos <strong>{len(jobs)} nova(s) vaga(s)</strong> para você hoje.
      </p>
      {cards}
    </div>

    <!-- Footer -->
    <div style="text-align:center;padding:16px;color:#a0aec0;font-size:11px;">
      Enviado automaticamente pelo LinkedIn Job Scraper 🤖
    </div>

  </div>
</body>
</html>"""


# ── Envio do e-mail ───────────────────────────────────────────────────────────
def send_email(jobs: list[dict[str, str]]) -> None:
    if not jobs:
        log.info("📭  Nenhuma vaga nova. E-mail não enviado.")
        return
    if not config.SMTP_USER or not config.SMTP_PASSWORD or not config.TO_EMAILS:
        raise RuntimeError(
            "Configuracao de e-mail incompleta. Defina EMAIL_SMTP_USER, "
            "EMAIL_SMTP_PASSWORD e EMAIL_TO."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"{len(jobs)} nova(s) vaga(s): {config.JOB_ALERT_TITLE} - "
        f"{datetime.now().strftime('%d/%m/%Y')}"
    )
    msg["From"] = config.EMAIL_FROM
    msg["To"]   = ", ".join(config.TO_EMAILS)

    # Fallback texto plano
    plain = "\n\n".join(
        f"{j.get('title','Vaga')} | {j.get('company','—')}\n{j['url']}"
        for j in jobs
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(build_email_html(jobs), "html", "utf-8"))

    try:
        if config.EMAIL_USE_SSL:
            server_context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(
                config.SMTP_HOST,
                config.SMTP_PORT,
                context=server_context,
            )
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT)
        with server:
            server.ehlo()
            if config.EMAIL_USE_TLS and not config.EMAIL_USE_SSL:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, config.TO_EMAILS, msg.as_string())
        log.info("📧  E-mail enviado para: %s", ", ".join(config.TO_EMAILS))
    except Exception as exc:
        log.error("❌  Falha ao enviar e-mail: %s", exc)
        raise


# ── Ciclo principal ───────────────────────────────────────────────────────────
def run_job() -> None:
    log.info("=" * 60)
    log.info("▶  Iniciando varredura  %s", datetime.now().isoformat(sep=" ", timespec="seconds"))

    seen = load_seen()

    # 1. Busca via DuckDuckGo
    raw_jobs = ddg_search()

    # 2. Enriquece com Playwright (detalhes reais da página)
    jobs = enrich_with_playwright(raw_jobs)

    # 3. Filtra só as novas
    new_jobs = filter_new(jobs, seen)

    # 4. Envia e-mail
    send_email(new_jobs)

    # 5. Persiste chaves vistas (URL normalizada + assinatura da vaga)
    for j in jobs:
        seen.update(job_seen_keys(j))
    save_seen(seen)

    log.info("✔  Ciclo concluído.\n")


# ── Agendamento ───────────────────────────────────────────────────────────────
def main(once: bool = False) -> None:
    log.info("🚀  LinkedIn Job Scraper iniciado")
    run_job()

    if once:
        return  # GitHub Actions: roda e termina

    schedule.every(config.RUN_INTERVAL_MINUTES).minutes.do(run_job)
    log.info(
        "⏰  Proxima execucao agendada a cada %s minuto(s)",
        config.RUN_INTERVAL_MINUTES,
    )
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    import sys
    once_mode = "--once" in sys.argv
    main(once=once_mode)
