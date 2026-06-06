from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from bs4 import BeautifulSoup


Job: TypeAlias = dict[str, str]


@dataclass(frozen=True)
class LinkedInPageSnapshot:
    url: str
    title: str
    soup: BeautifulSoup
    full_text: str


@dataclass(frozen=True)
class JobDetails:
    title: str
    company: str
    location: str
    workplace: str
    posted_age: str
    top_card_text: str


class JobRejected(Exception):
    """Rejeição esperada durante a validação de uma vaga."""
