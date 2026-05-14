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
JOB_ALERT_TITLE = "Vagas Junior e Estágio - Tech"

SEARCH_QUERIES = [
    # Dados - Junior/Estágio
    "analista de dados junior remoto brasil site:linkedin.com/jobs/view",
    "cientista de dados junior remoto brasil site:linkedin.com/jobs/view",
    "engenheiro de dados junior remoto brasil site:linkedin.com/jobs/view",
    "estagiario dados remoto brasil site:linkedin.com/jobs/view",
    "data analyst junior remote brazil site:linkedin.com/jobs/view",
    "data engineer junior remote brazil site:linkedin.com/jobs/view",
    "intern data analyst remote brazil site:linkedin.com/jobs/view",

    # IA / Machine Learning - Junior/Estágio
    "inteligencia artificial junior remoto brasil site:linkedin.com/jobs/view",
    "machine learning junior remoto brasil site:linkedin.com/jobs/view",
    "engenheiro machine learning junior remoto brasil site:linkedin.com/jobs/view",
    "estagiario machine learning remoto brasil site:linkedin.com/jobs/view",
    "ai engineer junior remote brazil site:linkedin.com/jobs/view",
    "intern ai remote brazil site:linkedin.com/jobs/view",

    # Backend - Junior/Estágio
    "desenvolvedor backend junior remoto brasil site:linkedin.com/jobs/view",
    "backend developer junior remote brazil site:linkedin.com/jobs/view",
    "estagiario backend remoto brasil site:linkedin.com/jobs/view",
    "python backend junior remoto brasil site:linkedin.com/jobs/view",
    "node backend junior remoto brasil site:linkedin.com/jobs/view",
    "intern backend remote brazil site:linkedin.com/jobs/view",

    # Cyberseguranca - Junior/Estágio
    "seguranca da informacao junior remoto brasil site:linkedin.com/jobs/view",
    "analista de seguranca junior remoto brasil site:linkedin.com/jobs/view",
    "estagiario cybersecurity remoto brasil site:linkedin.com/jobs/view",
    "cybersecurity junior remote brazil site:linkedin.com/jobs/view",
    "soc analyst junior remote brazil site:linkedin.com/jobs/view",
    "intern cybersecurity remote brazil site:linkedin.com/jobs/view",

    # Web - Junior/Estágio
    "desenvolvedor web junior remoto brasil site:linkedin.com/jobs/view",
    "frontend junior remoto brasil site:linkedin.com/jobs/view",
    "fullstack junior remoto brasil site:linkedin.com/jobs/view",
    "estagiario frontend remoto brasil site:linkedin.com/jobs/view",
    "react junior remote brazil site:linkedin.com/jobs/view",
    "intern frontend remote brazil site:linkedin.com/jobs/view",
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
REQUIRE_REMOTE = env_bool("REQUIRE_REMOTE", default=True)
REQUIRE_TARGET_ROLE = env_bool("REQUIRE_TARGET_ROLE", default=True)

# --- Filtro de idade de vagas (dias) ---------------------------
MAX_JOB_AGE_DAYS = int(os.getenv("MAX_JOB_AGE_DAYS", "7"))
REQUIRE_POSTED_AGE = env_bool("REQUIRE_POSTED_AGE", default=True)

# --- Arquivos de estado -------------------------------------
SEEN_JOBS_FILE = "seen_jobs.json"
LOG_FILE = "scraper.log"
