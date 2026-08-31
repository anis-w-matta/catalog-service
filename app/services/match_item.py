"""Layered item resolution for a spoken/typed item span: cheapest
deterministic method first, fully deterministic - no LLM anywhere in this
path.

Reuses the DB-backed ItemResolver (app/services/item_resolver.py -
exact/pg_trgm+rapidfuzz staged matching, already handling duplicate-
description safety and size/color/discount attribute conflicts) for the
text-similarity stage, and layers on top of it the one thing that stage
doesn't do: numeric pack/size-code conflict checking. A candidate that
isn't confidently unique after that is reported AMBIGUOUS/NOT_FOUND rather
than guessed - never resolved by a fallback.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.schemas.enums import MatchStatus
from app.services.item_normalization import extract_numeric_tokens
from app.services.item_resolver import ItemResolver
from app.services.normalization import normalize_color, normalize_size


@dataclass
class ItemCandidate:
    item_number: str
    item_description: str
    item_family: str | None
    score: float
    numeric_compatible: bool
    numeric_conflict_reason: str | None = None


@dataclass
class ItemMatchResult:
    item_number: str | None
    item_description: str | None
    item_family: str | None
    status: MatchStatus
    score: float | None
    method: str
    candidates: list[ItemCandidate] = field(default_factory=list)
    explanation: str = ""


def _numeric_check(spoken: set[str], item_desc: str) -> tuple[bool, str | None]:
    """True (+ None) unless both sides name numeric pack/size tokens and
    they're disjoint - a spoken '20x4' against a candidate whose
    description says '12x4' is a real conflict, not a fuzzy-similarity
    rounding error."""
    candidate = extract_numeric_tokens(item_desc)
    if not spoken or not candidate:
        return True, None
    if spoken & candidate:
        return True, None
    return False, (f"spoken numeric token(s) {sorted(spoken)} do not match "
                   f"candidate token(s) {sorted(candidate)}")


def _spoken_attributes(item_span: str) -> dict:
    """Size/color explicitly named in the spoken item span, so
    ItemResolver's existing attribute-conflict penalty (size/color read
    off item_desc) is actually exercised here."""
    attrs: dict[str, str] = {}
    for token in item_span.split():
        size = normalize_size(token)
        if size and "size" not in attrs:
            attrs["size"] = token
        color = normalize_color(token)
        if color and "color" not in attrs:
            attrs["color"] = token
    return attrs


def resolve_item(session: Session, item_span: str,
                 fuzzy_threshold: float | None = None,
                 ambiguity_margin: float | None = None,
                 numeric_conflict_penalty: float | None = None,
                 top_k: int | None = None) -> ItemMatchResult:
    fuzzy_threshold = fuzzy_threshold if fuzzy_threshold is not None \
        else settings.item_fuzzy_threshold
    ambiguity_margin = ambiguity_margin if ambiguity_margin is not None \
        else settings.item_ambiguity_margin
    numeric_conflict_penalty = numeric_conflict_penalty if numeric_conflict_penalty is not None \
        else settings.numeric_conflict_penalty
    top_k = top_k if top_k is not None else settings.top_k_candidates

    query = (item_span or "").strip()
    if not query:
        return ItemMatchResult(None, None, None, MatchStatus.not_found,
                               None, "none", explanation="empty item text")

    resolver = ItemResolver(session)
    exact_match, raw_cands = resolver.resolve(
        query, attributes=_spoken_attributes(query))

    spoken_numeric = extract_numeric_tokens(query)
    candidates: list[ItemCandidate] = []
    for c in raw_cands[:top_k]:
        numeric_ok, reason = _numeric_check(spoken_numeric, c.item_desc)
        score = round(c.score * 100, 2)
        if not numeric_ok:
            score = max(0.0, score - numeric_conflict_penalty)
        candidates.append(ItemCandidate(
            item_number=c.item_nb, item_description=c.item_desc,
            item_family=c.category, score=score,
            numeric_compatible=numeric_ok, numeric_conflict_reason=reason))
    candidates.sort(key=lambda c: c.score, reverse=True)

    # Fast, deterministic accept: a single exact-normalized match that
    # ItemResolver already uniquely resolved (no duplicate description, no
    # attribute conflict), and that carries no numeric conflict either.
    if exact_match is not None:
        top = next((c for c in candidates if c.item_number == exact_match.item_nb),
                   None)
        if top is not None and top.numeric_compatible:
            return ItemMatchResult(
                top.item_number, top.item_description, top.item_family,
                MatchStatus.matched, top.score, "exact", candidates,
                explanation=f"exact normalized match, score={top.score:g}")

    if not candidates:
        return ItemMatchResult(None, None, None, MatchStatus.not_found, None,
                               "none", candidates,
                               explanation="no candidates found for "
                                           f"{query!r}")

    top, second = candidates[0], (candidates[1] if len(candidates) > 1 else None)
    confident_fuzzy = (top.score >= fuzzy_threshold and top.numeric_compatible
                      and (second is None
                           or top.score - second.score >= ambiguity_margin))
    if confident_fuzzy:
        return ItemMatchResult(
            top.item_number, top.item_description, top.item_family,
            MatchStatus.matched, top.score, "fuzzy", candidates,
            explanation=(f"rapidfuzz top-1 score={top.score:g}, "
                        f"margin over top-2="
                        f"{'n/a' if second is None else round(top.score - second.score, 2)}"))

    if not top.numeric_compatible:
        return ItemMatchResult(None, None, None, MatchStatus.ambiguous,
                               top.score, "fuzzy", candidates,
                               explanation=f"numeric conflict: {top.numeric_conflict_reason}")
    if second is not None and top.score - second.score < ambiguity_margin:
        return ItemMatchResult(None, None, None, MatchStatus.ambiguous,
                               top.score, "fuzzy", candidates,
                               explanation=(f"top-1/top-2 scores tied "
                                           f"({top.score:g} vs {second.score:g})"))
    return ItemMatchResult(None, None, None, MatchStatus.not_found, top.score,
                           "fuzzy", candidates,
                           explanation=f"below threshold, top score={top.score:g}")
