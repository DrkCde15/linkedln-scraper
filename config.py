# ============================================================
#  config.py  –  Edite APENAS este arquivo antes de rodar
# ============================================================

# --- Busca ---------------------------------------------------
SEARCH_QUERIES = [
    "python junior remoto site:linkedin.com/jobs",
    "python developer junior remote São Paulo site:linkedin.com/jobs",
    "desenvolvedor python junior remoto site:linkedin.com/jobs",
]
MAX_RESULTS_PER_QUERY = 15          # resultados por busca

# --- E-mail de envio (quem manda) ----------------------------
SMTP_HOST     = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = "juka72918@gmail.com"       # <-- troque
SMTP_PASSWORD = "gzkpfvktinrmmhgd"          # <-- App Password do Google
                                            # https://myaccount.google.com/apppasswords

# --- E-mail de destino (quem recebe) -------------------------
TO_EMAILS = [
    "jcesarsantana215@gmail.com",                       # <-- troque
]

# --- Agendamento ---------------------------------------------
RUN_HOUR   = 8      # hora  (0-23)
RUN_MINUTE = 0      # minuto (0-59)
# Roda todo dia às 08:00

# --- Arquivos de estado --------------------------------------
SEEN_JOBS_FILE = "seen_jobs.json"           # persiste vagas já enviadas
LOG_FILE       = "scraper.log"