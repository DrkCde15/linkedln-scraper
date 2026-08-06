from __future__ import annotations

import datetime
import re

import config
from text_utils import contains_any_marker, join_texts, normalize_text, normalize_whitespace


CLOSED_JOB_MARKERS = {
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
    "essa vaga nao existe mais",
    "esta vaga nao existe mais",
    "a vaga nao existe mais",
    "vaga nao existe mais",
    "essa vaga nao existe",
    "esta vaga nao existe",
    "a vaga nao existe",
    "essa vaga foi preenchida",
    "vaga preenchida",
    "foi preenchida",
    "vaga expirada ou indisponivel",
    "essa vaga expirou",
    "esta vaga expirou",
    "vaga encerrada ou preenchida",
    "processo seletivo finalizado",
    "candidaturas finalizadas",
    "inscricoes finalizadas",
    "job posting has been filled",
    "this position has been filled",
    "esta vacante ya no esta disponible",
    "ya no esta disponible",
    "vacante expirada",
}

NORMALIZED_TARGET_LOCATIONS = {
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
    "amazonas",
    "bahia",
    "barueri",
    "belo horizonte",
    "brasil",
    "brasilia",
    "brazil",
    "campinas",
    "ceara",
    "curitiba",
    "distrito federal",
    "espirito santo",
    "florianopolis",
    "fortaleza",
    "goiania",
    "goias",
    "mato grosso",
    "mato grosso do sul",
    "minas gerais",
    "parana",
    "pernambuco",
    "porto alegre",
    "recife",
    "remoto",
    "remota",
    "remote",
    "home office",
    "home-office",
    "ribeirao preto",
    "rio de janeiro",
    "rio grande do sul",
    "salvador",
    "santa catarina",
    "santos",
    "sao paulo",
    "sorocaba",
    "vitoria",
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

EXCLUDED_LOCATION_MARKERS = {
    "portugal",
    "espanha",
    "spain",
    "united states",
    "eua",
    "usa",
    "india",
    "reino unido",
    "united kingdom",
    "uk",
    "argentina",
    "mexico",
    "madri",
    "lisboa",
    "porto",
    "barcelona",
    "berlim",
    "berlin",
    "london",
    "londres",
    "latam",
    "latin america",
    "europe",
    "emea",
    "worldwide",
    "canada",
    "chile",
    "colombia",
    "peru",
    "uruguay",
    "germany",
    "france",
    "netherlands",
    "australia",
    "singapore",
    "south africa",
}

SENIORITY_BLOCKLIST = {
    "senior",
    "sênior",
    "principal",
    "staff",
    "lead",
    "architect",
    "manager",
    "diretor",
    "especialista",
    "experienced",
    "sr",
    "sr.",
    "coordenador",
    "coordinator",
    "head",
    "tech lead",
    "team lead",
    "10+ years",
    "8+ years",
    "5+ years",
}

JUNIOR_MARKERS = {
    "junior",
    "jr",
    "jr.",
    "estagiario",
    "estágio",
    "estagio",
    "trainee",
    "intern",
    "interns",
    "internship",
    "entry level",
    "entry-level",
    "starter",
    "graduate",
    "recém",
    "recem",
    "iniciante",
    "começante",
    "comecante",
}

PLENO_MARKERS = {
    "pleno",
    "pl",
    "pl.",
    "mid-level",
    "mid level",
    "midlevel",
}

TARGET_ROLE_MARKERS = {
    "dados",
    "data",
    "analytics",
    "analyst",
    "bi",
    "business intelligence",
    "sql",
    "cientista de dados",
    "engenheiro de dados",
    "machine learning",
    "inteligencia artificial",
    "ia",
    "ai",
    "ml",
    "llm",
    "nlp",
    "backend",
    "back-end",
    "back end",
    "developer",
    "desenvolvedor",
    "programador",
    "software",
    "engenheiro de software",
    "python",
    "node",
    "node.js",
    "javascript",
    "typescript",
    "api",
    "cybersecurity",
    "cyber",
    "seguranca",
    "segurança",
    "seguranca da informacao",
    "security",
    "soc",
    "appsec",
    "devsecops",
    "frontend",
    "front-end",
    "front end",
    "fullstack",
    "full-stack",
    "react",
    "reactjs",
    "web",
}

ROLE_BLOCKLIST = {
    "marketing",
    "sales",
    "vendas",
    "comercial",
    "financeiro",
    "contabil",
    "contabilidade",
    "administrativo",
    "rh",
    "recursos humanos",
    "recruiter",
    "recrutador",
    "customer success",
    "sucesso do cliente",
    "atendimento",
    "suporte",
    "support",
    "designer",
    "product manager",
    "produto",
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

AGE_PATTERNS = [
    re.compile(
        r"\bha\s*\d+\s*(?:minuto|minutos|hora|horas|dia|dias|semana|semanas|mes|meses|ano|anos)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d+\s*(?:minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s+ago\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhace\s*\d+\s*(?:minuto|minutos|hora|horas|dia|dias|semana|semanas|mes|meses|ano|anos)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:publicad[oa]|postad[oa])\s*(?:em\s*)?\d{1,2}/\d{1,2}/\d{2,4}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:publicad[oa]|postad[oa])\s*(?:on|el)\s+\d{1,2}/\d{1,2}/\d{2,4}",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
]

DATE_IN_AGE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")

AGE_UNIT_DAYS = [
    (("ano", "anos", "year", "years"), 365),
    (("mes", "meses", "month", "months"), 30),
    (("semana", "semanas", "week", "weeks"), 7),
    (("dia", "dias", "day", "days"), 1),
    (("hora", "horas", "hour", "hours"), 1),
    (("minuto", "minutos", "minute", "minutes"), 1),
]

EXPERIENCE_BLOCK_RE = re.compile(
    r"\b(?:[4-9]|[1-9]\d)\+?\s*(?:anos?|years?)\b",
    re.IGNORECASE,
)

BRAZIL_STATE_WITH_COUNTRY_RE = re.compile(
    r"\b(?:sp|rj|mg|pr|rs|sc|ba|pe|ce|df|es|ms|mt)\b"
    r"(?:\s*,\s*|\s*-\s*)(?:brasil|brazil)\b"
)


def is_closed_text(text: str) -> bool:
    return contains_any_marker(text, CLOSED_JOB_MARKERS)


def extract_posted_age_text(text: str) -> str:
    normalized_text = normalize_text(text or "")
    for pattern in AGE_PATTERNS:
        match = pattern.search(normalized_text)
        if match:
            return match.group(0)
    return ""


def parse_job_age_to_days(age_text: str) -> int | None:
    if not age_text:
        return None

    normalized_age = normalize_text(age_text)
    date_match = DATE_IN_AGE_RE.search(normalized_age)
    if date_match:
        return days_since_date(date_match.group(0))

    count_match = re.search(r"(\d+)", normalized_age)
    if not count_match:
        return None

    count = int(count_match.group(1))
    for unit_markers, days_per_unit in AGE_UNIT_DAYS:
        if any(unit in normalized_age for unit in unit_markers):
            return count * days_per_unit
    return None


def days_since_date(date_text: str) -> int:
    day, month, year = map(int, DATE_IN_AGE_RE.search(date_text).groups())
    if year < 100:
        year += 2000
    try:
        posted = datetime.date(year, month, day)
    except ValueError:
        return 0
    days = (datetime.date.today() - posted).days
    return max(days, 0)


def is_allowed_posted_age(age_text: str) -> bool:
    if not age_text:
        return not config.REQUIRE_POSTED_AGE

    days = parse_job_age_to_days(age_text)
    if days is None:
        return not config.REQUIRE_POSTED_AGE
    return days <= config.MAX_JOB_AGE_DAYS


def has_known_blocked_age(text: str) -> bool:
    posted_age = extract_posted_age_text(text)
    return bool(posted_age) and not is_allowed_posted_age(posted_age)


def has_brazil_location(text: str) -> bool:
    location = normalize_whitespace(normalize_text(text))
    if not location:
        return False
    if location in NORMALIZED_TARGET_LOCATIONS:
        return True
    if any(term in location for term in BRAZIL_LOCATION_TERMS):
        return True
    return bool(BRAZIL_STATE_WITH_COUNTRY_RE.search(location))


def has_remote_signal(*texts: str) -> bool:
    return contains_any_marker(join_texts(texts), REMOTE_MARKERS)


def has_non_remote_signal(*texts: str) -> bool:
    return contains_any_marker(join_texts(texts), NON_REMOTE_MARKERS)


def is_target_location(location_text: str, context_text: str = "") -> bool:
    if not location_text:
        return False

    location = normalize_whitespace(normalize_text(location_text))
    context = normalize_text(context_text)
    if contains_any_marker(location, EXCLUDED_LOCATION_MARKERS):
        return False
    if not has_brazil_location(location):
        return False
    if not config.REQUIRE_REMOTE:
        return True
    if has_non_remote_signal(location, context):
        return False
    return has_remote_signal(location, context)


def has_seniority_block(text: str) -> bool:
    normalized_text = normalize_text(text)
    return contains_any_marker(normalized_text, SENIORITY_BLOCKLIST) or bool(
        EXPERIENCE_BLOCK_RE.search(normalized_text)
    )


def is_junior_or_intern(title: str, supporting_text: str) -> bool:
    focused_text = f"{title} {supporting_text}"
    if has_seniority_block(focused_text):
        return False
    return contains_any_marker(focused_text, JUNIOR_MARKERS)


def is_desired_seniority(title: str, supporting_text: str) -> bool:
    focused_text = f"{title} {supporting_text}"
    if has_seniority_block(focused_text):
        return False
    return contains_any_marker(focused_text, JUNIOR_MARKERS) or contains_any_marker(
        focused_text,
        PLENO_MARKERS,
    )


def is_target_role(title: str, supporting_text: str = "") -> bool:
    if contains_any_marker(title, OUT_OF_SCOPE_TITLE_MARKERS):
        return False

    combined_text = f"{title} {supporting_text}"
    if not contains_any_marker(combined_text, TARGET_ROLE_MARKERS):
        return False

    title_has_target = contains_any_marker(title, TARGET_ROLE_MARKERS)
    return title_has_target or not contains_any_marker(title, ROLE_BLOCKLIST)
