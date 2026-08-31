"""Fuzzy customer resolution.

The customer list is much smaller than the item catalog in match method
(a single RapidFuzz pass against customer_name, plus a direct lookup
against customer_number), but larger in row count (~40k rows) - this is
why customer matching lives here, in the same service/process as the
`customer` table, rather than the backend pulling the whole table over
HTTP on every voice command. The one rule that matters as much here as it
does for items: never silently pick a customer when two candidates are
effectively tied.
"""
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Customer
from app.schemas.enums import MatchStatus
from app.services.item_resolver import tied_with_top
from app.services.normalization import normalize_text


@dataclass
class CustomerMatch:
    customer_number: str | None
    customer_name: str | None
    score: float
    status: MatchStatus
    candidates: list[tuple[str, str, float]] = field(default_factory=list)


def match_customer(session: Session, raw_text: str,
                   threshold: float | None = None,
                   tie_margin: float | None = None) -> CustomerMatch:
    threshold = threshold if threshold is not None else settings.customer_match_threshold
    tie_margin = tie_margin if tie_margin is not None else settings.customer_match_tie_margin

    query = (raw_text or "").strip()
    if not query:
        return CustomerMatch(None, None, 0.0, MatchStatus.not_found)

    exact_nb = session.get(Customer, query.upper())
    if exact_nb is not None:
        return CustomerMatch(exact_nb.customer_number, exact_nb.customer_name,
                             100.0, MatchStatus.matched)

    q_norm = normalize_text(query)
    customers = list(session.scalars(select(Customer)))
    if not customers:
        return CustomerMatch(None, None, 0.0, MatchStatus.not_found)

    scored: list[tuple[str, str, float]] = []
    for c in customers:
        name_score = fuzz.token_sort_ratio(q_norm, normalize_text(c.customer_name))
        nb_score = (fuzz.ratio(q_norm, normalize_text(c.customer_number))
                   if c.customer_number else 0.0)
        scored.append((c.customer_number, c.customer_name,
                       max(name_score, nb_score)))
    scored.sort(key=lambda t: t[2], reverse=True)

    top = scored[0]
    if top[2] < threshold:
        return CustomerMatch(None, None, top[2], MatchStatus.not_found,
                             candidates=scored[:5])

    tied = tied_with_top(scored, epsilon=tie_margin, key=lambda t: t[2])
    if len(tied) > 1:
        return CustomerMatch(None, None, top[2], MatchStatus.ambiguous,
                             candidates=scored[:5])

    return CustomerMatch(top[0], top[1], top[2], MatchStatus.matched,
                         candidates=scored[:5])


def search_customers(session: Session, q: str, limit: int = 5
                     ) -> list[tuple[str, str, float]]:
    """Ranked customer lookup for an explicit human search (the Request
    screen's "select customer" picker) - unlike match_customer, this never
    applies threshold/tie-margin gating, since a human reviewer picking
    from a visible list doesn't need the auto-resolution safety net that
    exists to stop the *pipeline* from silently guessing.
    """
    query = (q or "").strip()
    if not query:
        return []
    q_norm = normalize_text(query)
    customers = list(session.scalars(select(Customer)))
    scored = [
        (c.customer_number, c.customer_name,
         max(fuzz.token_sort_ratio(q_norm, normalize_text(c.customer_name)),
             fuzz.ratio(q_norm, normalize_text(c.customer_number))
             if c.customer_number else 0.0))
        for c in customers
    ]
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:limit]
