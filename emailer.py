from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import config
from models import Job


log = logging.getLogger(__name__)

DEFAULT_EMAIL_TITLE = "Vaga"
DEFAULT_LOCATION = "Brasil"
EMAIL_SNIPPET_MAX_CHARS = 200
GMAIL_APP_PASSWORD_HELP = (
    "Para Gmail, gere uma senha de app de 16 caracteres e salve-a em "
    "EMAIL_SMTP_PASSWORD ou SMTP_PASSWORD. A senha normal da conta costuma "
    "ser recusada pelo SMTP."
)
SMTP_CREDENTIALS_HELP = (
    "Confira tambem se EMAIL_SMTP_USER/SMTP_USER e EMAIL_FROM usam a conta "
    "correta ou estao autorizados para envio."
)


class EmailConfigurationError(RuntimeError):
    """Configuração obrigatória para envio de e-mail não foi encontrada."""


class EmailDeliveryError(RuntimeError):
    """Falha esperada durante o envio de e-mail."""


class EmailAuthenticationError(EmailDeliveryError):
    """Credenciais SMTP recusadas pelo servidor."""


def send_email(jobs: list[Job]) -> None:
    if not jobs:
        log.info("📭  Nenhuma vaga nova. E-mail não enviado.")
        return

    ensure_email_configured()
    message = build_email_message(jobs)
    try:
        deliver_email(message)
        log.info("📧  E-mail enviado para: %s", ", ".join(config.TO_EMAILS))
    except EmailDeliveryError as exc:
        log.error("❌  %s", exc)
        raise


def deliver_email(message: MIMEMultipart) -> None:
    try:
        with create_smtp_server() as server:
            prepare_smtp_server(server)
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.EMAIL_FROM, config.TO_EMAILS, message.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailAuthenticationError(build_smtp_authentication_error_message(exc)) from exc
    except smtplib.SMTPException as exc:
        raise EmailDeliveryError(f"Falha SMTP ao enviar e-mail: {exc}") from exc
    except OSError as exc:
        raise EmailDeliveryError(
            f"Falha de rede ao conectar em {config.SMTP_HOST}:{config.SMTP_PORT}: {exc}"
        ) from exc


def ensure_email_configured() -> None:
    missing_fields = [
        field_name
        for field_name, value in {
            "EMAIL_SMTP_USER": config.SMTP_USER,
            "EMAIL_SMTP_PASSWORD": config.SMTP_PASSWORD,
            "EMAIL_TO": config.TO_EMAILS,
        }.items()
        if not value
    ]
    if missing_fields:
        raise EmailConfigurationError(
            "Configuracao de e-mail incompleta: " + ", ".join(missing_fields)
        )


def build_smtp_authentication_error_message(exc: smtplib.SMTPAuthenticationError) -> str:
    server_response = decode_smtp_response(exc.smtp_error)
    return (
        "Falha de autenticacao SMTP. "
        f"{GMAIL_APP_PASSWORD_HELP} "
        f"{SMTP_CREDENTIALS_HELP} "
        f"Resposta do servidor: {server_response}"
    )


def decode_smtp_response(response: object) -> str:
    if isinstance(response, bytes):
        return response.decode("utf-8", errors="replace").strip()
    return str(response).strip()


def build_email_message(jobs: list[Job]) -> MIMEMultipart:
    message = MIMEMultipart("alternative")
    message["Subject"] = build_email_subject(jobs)
    message["From"] = config.EMAIL_FROM
    message["To"] = ", ".join(config.TO_EMAILS)
    message.attach(MIMEText(build_plain_email(jobs), "plain", "utf-8"))
    message.attach(MIMEText(build_email_html(jobs), "html", "utf-8"))
    return message


def build_email_subject(jobs: list[Job]) -> str:
    today = datetime.now().strftime("%d/%m/%Y")
    return f"{len(jobs)} nova(s) vaga(s): {config.JOB_ALERT_TITLE} - {today}"


def build_plain_email(jobs: list[Job]) -> str:
    return "\n\n".join(
        f"{job.get('title', DEFAULT_EMAIL_TITLE)} | {job.get('company', '—')}\n{job['url']}"
        for job in jobs
    )


def build_email_html(jobs: list[Job]) -> str:
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    cards = "\n".join(build_email_card(job) for job in jobs)
    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f7fafc;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="max-width:620px;margin:32px auto;padding:0 16px;">
    {build_email_header(now)}
    {build_email_body(jobs, cards)}
    {build_email_footer()}
  </div>
</body>
</html>"""


def build_email_header(now: str) -> str:
    return f"""
    <div style="background:linear-gradient(135deg,#0a66c2,#0d4f9e);
                border-radius:16px 16px 0 0;padding:28px 32px;text-align:center;">
      <h1 style="color:#fff;margin:0;font-size:22px;letter-spacing:-0.3px;">
        Novas vagas: {escape(config.JOB_ALERT_TITLE)}
      </h1>
      <p style="color:#bfdbfe;margin:6px 0 0;font-size:13px;">{escape(now)} · São Paulo</p>
    </div>"""


def build_email_body(jobs: list[Job], cards: str) -> str:
    return f"""
    <div style="background:#f7fafc;padding:24px 0;">
      <p style="color:#4a5568;font-size:14px;margin:0 0 20px;text-align:center;">
        Encontramos <strong>{len(jobs)} nova(s) vaga(s)</strong> para você hoje.
      </p>
      {cards}
    </div>"""


def build_email_footer() -> str:
    return """
    <div style="text-align:center;padding:16px;color:#a0aec0;font-size:11px;">
      Enviado automaticamente pelo Job Scraper 🤖
    </div>"""


def build_email_card(job: Job) -> str:
    title = escape(job.get("title", DEFAULT_EMAIL_TITLE))
    company = escape(job.get("company", "—"))
    location = escape(job.get("location", DEFAULT_LOCATION))
    snippet = escape(job.get("snippet", "")[:EMAIL_SNIPPET_MAX_CHARS])
    url = escape(job["url"], quote=True)
    meta_parts = [
        escape(str(job.get("workplace"))).strip(),
        escape(str(job.get("posted_age"))).strip(),
        escape(str(job.get("source"))).strip(),
    ]
    meta_line = " · ".join(part for part in meta_parts if part)

    return f"""
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;
                    padding:20px 24px;margin-bottom:16px;">
          <h2 style="margin:0 0 4px;font-size:17px;color:#1a202c;">{title}</h2>
          <p style="margin:0 0 2px;font-size:14px;color:#4a5568;">
            🏢 {company} &nbsp;|&nbsp; 📍 {location}
          </p>
          {f'<p style="margin:0 0 8px;font-size:12px;color:#a0aec0;">{meta_line}</p>' if meta_line else ''}
          <p style="margin:8px 0 12px;font-size:13px;color:#718096;">{snippet}</p>
          <a href="{url}" target="_blank"
             style="display:inline-block;background:#0a66c2;color:#fff;
                    text-decoration:none;padding:9px 20px;border-radius:8px;
                    font-size:13px;font-weight:600;">
            Ver vaga →
          </a>
        </div>"""


def create_smtp_server() -> smtplib.SMTP:
    if config.EMAIL_USE_SSL:
        return smtplib.SMTP_SSL(
            config.SMTP_HOST,
            config.SMTP_PORT,
            context=ssl.create_default_context(),
        )
    return smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT)


def prepare_smtp_server(server: smtplib.SMTP) -> None:
    server.ehlo()
    if config.EMAIL_USE_TLS and not config.EMAIL_USE_SSL:
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
