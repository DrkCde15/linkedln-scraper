import smtplib
import unittest
from unittest import mock

import emailer


class RejectingSmtpServer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def ehlo(self):
        return None

    def starttls(self, context=None):
        return None

    def login(self, username, password):
        raise smtplib.SMTPAuthenticationError(
            535,
            b"5.7.8 Username and Password not accepted",
        )

    def sendmail(self, sender, recipients, message):
        raise AssertionError("sendmail should not be called after login fails")


class EmailerTests(unittest.TestCase):
    def test_authentication_failure_mentions_gmail_app_password(self):
        with configured_email_settings(), mocked_smtp_server(RejectingSmtpServer()), mocked_error_log():
            with self.assertRaises(emailer.EmailAuthenticationError) as error:
                emailer.send_email([sample_job()])

        self.assertIn("senha de app de 16 caracteres", str(error.exception))


def configured_email_settings():
    return mock.patch.multiple(
        emailer.config,
        SMTP_HOST="smtp.gmail.com",
        SMTP_PORT=587,
        SMTP_USER="sender@example.com",
        SMTP_PASSWORD="wrong-password",
        EMAIL_FROM="sender@example.com",
        EMAIL_USE_SSL=False,
        EMAIL_USE_TLS=False,
        TO_EMAILS=["receiver@example.com"],
    )


def mocked_smtp_server(server):
    return mock.patch.object(emailer, "create_smtp_server", return_value=server)


def mocked_error_log():
    return mock.patch.object(emailer.log, "error")


def sample_job():
    return {
        "title": "Analista de Dados Jr",
        "company": "Acme",
        "location": "Remoto",
        "snippet": "Python e SQL",
        "url": "https://example.com/jobs/1",
    }


if __name__ == "__main__":
    unittest.main()
