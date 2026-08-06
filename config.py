# ============================================================
#  config.py - configuracao do scraper
# ============================================================
from pathlib import Path
import os
import re

from dotenv import load_dotenv


# Mesmo padrao do freela-scraper: aceita .env local e data/.env.
load_dotenv()
load_dotenv(Path("data") / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_email_list(value: str | None) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,;\n]+", value or "")
        if item.strip()
    ]


# --- Busca ---------------------------------------------------
JOB_ALERT_TITLE = "Vagas Estágio, Júnior e Pleno - Tech"

# Sites de vagas varridos (nome -> cláusula de domínio para a busca).
SITES = {
    "linkedin": "site:linkedin.com/jobs/view",
    "indeed": "site:br.indeed.com",
    "infojobs": "site:infojobs.com.br",
    "vagas": "site:vagas.com.br",
    "catho": "site:catho.com.br",
    "programathor": "site:programathor.com.br",
    "geekhunter": "site:geekhunter.com.br",
    "gupy": "site:gupy.io",
    "trampos": "site:trampos.co",
}

# Permite restringir os sites via .env, ex.: ENABLED_SITES=linkedin,indeed,infojobs
ENABLED_SITES = [
    item.strip()
    for item in os.getenv("ENABLED_SITES", "").split(",")
    if item.strip()
] or list(SITES)

QUERY_STEMS = [
    # Estágio
    "estagio tecnologia brasil",
    "estagio dados brasil",
    "estagio desenvolvimento brasil",
    "estagio seguranca brasil",
    "estagiario dados brasil",
    "estagiario desenvolvimento brasil",
    "estagiario seguranca brasil",
    # Júnior / trainee
    "junior tecnologia brasil",
    "desenvolvedor junior brasil",
    "desenvolvedor backend junior brasil",
    "desenvolvedor frontend junior brasil",
    "desenvolvedor fullstack junior brasil",
    "analista de dados junior brasil",
    "engenheiro de dados junior brasil",
    "cybersecurity junior brasil",
    "trainee tecnologia brasil",
    # Pleno
    "desenvolvedor pleno brasil",
    "desenvolvedor backend pleno brasil",
    "desenvolvedor frontend pleno brasil",
    "desenvolvedor fullstack pleno brasil",
    "analista de dados pleno brasil",
    "engenheiro de dados pleno brasil",
    "devops pleno brasil",
    "qa pleno brasil",
]

SEARCH_QUERIES = [
    f"{stem} {SITES[site_name]}"
    for site_name in ENABLED_SITES
    for stem in QUERY_STEMS
]
MAX_RESULTS_PER_QUERY = 20

# --- E-mail de envio ----------------------------------------
SMTP_HOST = os.getenv("EMAIL_SMTP_HOST") or os.getenv("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT") or os.getenv("SMTP_PORT") or "587")
SMTP_USER = os.getenv("EMAIL_SMTP_USER") or os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("EMAIL_SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM") or os.getenv("SMTP_FROM") or SMTP_USER
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", default=env_bool("SMTP_USE_SSL", default=False))
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=env_bool("SMTP_USE_TLS", default=not EMAIL_USE_SSL))

# --- E-mail de destino --------------------------------------
TO_EMAILS = parse_email_list(
    os.getenv("EMAIL_TO") or os.getenv("TO_EMAILS") or os.getenv("TO_EMAIL")
)

# --- Agendamento local --------------------------------------
RUN_INTERVAL_MINUTES = int(os.getenv("RUN_INTERVAL_MINUTES", "360"))

# --- Validacao de disponibilidade ---------------------------
# Mantemos True por padrao para evitar enviar vagas que o LinkedIn ja fechou.
REQUIRE_PLAYWRIGHT_VALIDATION = env_bool("REQUIRE_PLAYWRIGHT_VALIDATION", default=True)
REQUIRE_APPLY_EVIDENCE = env_bool("REQUIRE_APPLY_EVIDENCE", default=True)
# Aceita qualquer local no Brasil (remoto, hibrido ou presencial).
REQUIRE_REMOTE = env_bool("REQUIRE_REMOTE", default=False)
REQUIRE_TARGET_ROLE = env_bool("REQUIRE_TARGET_ROLE", default=True)

# --- Filtro de idade de vagas (dias) ---------------------------
MAX_JOB_AGE_DAYS = int(os.getenv("MAX_JOB_AGE_DAYS", "7"))
REQUIRE_POSTED_AGE = env_bool("REQUIRE_POSTED_AGE", default=True)

# --- Arquivos de estado -------------------------------------
SEEN_JOBS_FILE = "seen_jobs.json"
LOG_FILE = "scraper.log"
