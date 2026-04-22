"""
scraper.py  –  Busca vagas Python Junior Remoto no LinkedIn via DuckDuckGo
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
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any
from ddgs import DDGS
import schedule
import time

from bs4 import BeautifulSoup

# Playwright é opcional – se não estiver instalado faz fallback gracioso
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

import config

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


def is_closed_job_page(soup: BeautifulSoup) -> bool:
    """Return True when the page indicates that the job is closed."""
    page_text = soup.get_text(" ", strip=True).lower()
    closed_markers = [
        "nao aceita mais candidaturas",
        "nao esta mais aceitando candidaturas",
        "vaga encerrada",
        "processo seletivo encerrado",
        "esta vaga foi encerrada",
        "this job is no longer available",
        "no longer accepting applications",
        "applications are closed",
        "position has been filled",
        "job has expired",
        "this position has been closed",
    ]
    return any(marker in page_text for marker in closed_markers)



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
    r"linkedin\.com/jobs/(view|search|collections|jobs-in)/",
    re.IGNORECASE,
)

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

                    # Debug útil para entender o que o buscador está trazendo
                    log.debug("DDG hit: %s", url)

                    # Aceita variações comuns de URL de vagas no LinkedIn
                    if "linkedin.com/jobs" not in url.lower():
                        continue
                    if not LINKEDIN_JOB_RE.search(url):
                        continue

                    # Normaliza URL removendo query string e barras finais
                    clean_url = url.split("?")[0].rstrip("/")
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
        log.warning("Playwright não instalado – usando dados do DuckDuckGo apenas.")
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
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")
                if is_closed_job_page(soup):
                    log.info("[CLOSED] Vaga encerrada, ignorando: %s", job["url"])
                    continue

                # Seletores LinkedIn (podem mudar com redesigns)
                def _text(sel: str) -> str:
                    el = soup.select_one(sel)
                    return el.get_text(strip=True) if el else ""

                title   = (
                    _text("h1.top-card-layout__title")
                    or _text("h1.job-title")
                    or _text("h1")
                    or job["title"]
                )
                company = (
                    _text("a.topcard__org-name-link")
                    or _text(".job-details-jobs-unified-top-card__company-name")
                    or _text(".topcard__flavor--black-link")
                    or "—"
                )
                location = (
                    _text(".topcard__flavor--bullet")
                    or _text(".job-details-jobs-unified-top-card__bullet")
                    or "—"
                )

                enriched.append(
                    {
                        **job,
                        "title":    title,
                        "company":  company,
                        "location": location,
                    }
                )
                log.debug("✅  %s @ %s", title, company)

            except PWTimeout:
                log.warning("⏱  Timeout em %s", job["url"])
                enriched.append({**job, "company": "—", "location": "—"})
            except Exception as exc:
                log.warning("⚠️  Erro em %s: %s", job["url"], exc)
                enriched.append({**job, "company": "—", "location": "—"})

        browser.close()

    return enriched


# ── Filtragem de vagas novas ──────────────────────────────────────────────────
def filter_new(jobs: list[dict[str, str]], seen: set[str]) -> list[dict[str, str]]:
    new = [j for j in jobs if j["url"] not in seen]
    log.info("🆕  %d vagas novas (de %d encontradas)", len(new), len(jobs))
    return new


# ── Montagem do e-mail HTML ───────────────────────────────────────────────────
def build_email_html(jobs: list[dict[str, str]]) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    cards = ""
    for j in jobs:
        title    = j.get("title", "Vaga")
        company  = j.get("company", "—")
        location = j.get("location", "—")
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
        🐍 Novas Vagas Python Junior Remoto
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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = (
        f"🐍 {len(jobs)} nova(s) vaga(s) Python Junior Remoto – "
        f"{datetime.now().strftime('%d/%m/%Y')}"
    )
    msg["From"] = config.SMTP_USER
    msg["To"]   = ", ".join(config.TO_EMAILS)

    # Fallback texto plano
    plain = "\n\n".join(
        f"{j.get('title','Vaga')} | {j.get('company','—')}\n{j['url']}"
        for j in jobs
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(build_email_html(jobs), "html", "utf-8"))

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_USER, config.TO_EMAILS, msg.as_string())
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

    # 5. Persiste URLs vistas
    seen.update(j["url"] for j in jobs)
    save_seen(seen)

    log.info("✔  Ciclo concluído.\n")


# ── Agendamento ───────────────────────────────────────────────────────────────
def main(once: bool = False) -> None:
    log.info("🚀  LinkedIn Job Scraper iniciado")
    run_job()

    if once:
        return  # GitHub Actions: roda e termina

    run_time = f"{config.RUN_HOUR:02d}:{config.RUN_MINUTE:02d}"
    schedule.every().day.at(run_time).do(run_job)
    log.info("⏰  Próxima execução agendada para %s", run_time)
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    import sys
    once_mode = "--once" in sys.argv
    main(once=once_mode)