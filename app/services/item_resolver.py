import math
import re
from dataclasses import asdict, dataclass

from rapidfuzz import fuzz
from sqlalchemy import select

from app.config import settings
from app.models import Item, OrderDetail, OrderHeader
from app.schemas.enums import MatchMethod
from app.services.normalization import (COLOR_WORDS, SIZE_SYNONYMS,
                                        SIZE_WORDS, expand_size_synonyms,
                                        normalize_color, normalize_size,
                                        normalize_text)

TIE_EPSILON = settings.resolver_tie_epsilon


def _escape_ilike(value: str) -> str:
    """Escape ILIKE wildcard metacharacters so a literal transcript string
    is matched literally."""
    return (value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_"))


def _letter_bounded(desc_lower: str, token: str) -> bool:
    """True if `token` appears not glued to surrounding LETTERS."""
    return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", desc_lower) is not None


def _desc_size(desc_lower: str) -> str | None:
    for token in SIZE_WORDS:
        if _letter_bounded(desc_lower, token.lower()):
            return token
    for word, abbrev in SIZE_SYNONYMS.items():
        if word.isascii() and _letter_bounded(desc_lower, word):
            return abbrev
    return None


def _desc_color(desc_lower: str) -> str | None:
    for token in COLOR_WORDS:
        if _letter_bounded(desc_lower, token.lower()):
            return token
    return None


_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _desc_discount_percent(item_desc: str) -> float | None:
    m = _PERCENT_RE.search(item_desc)
    return float(m.group(1)) if m else None


def _attribute_conflict(item_desc: str, attributes: dict | None,
                        qualifiers: dict | None = None
                        ) -> tuple[bool, str | None]:
    """True + a human-readable reason if a candidate's inferred size/color/
    promotion (read textually off item_desc) explicitly contradicts an
    attribute or stated discount the customer actually gave."""
    desc_lower = item_desc.lower()

    if attributes:
        wanted_size = normalize_size(attributes.get("size"))
        if wanted_size:
            got_size = _desc_size(desc_lower)
            if got_size and got_size != wanted_size:
                return True, f"size {got_size} != requested {wanted_size}"

        wanted_color = normalize_color(attributes.get("color"))
        if wanted_color:
            got_color = _desc_color(desc_lower)
            if got_color and got_color != wanted_color:
                return True, f"color {got_color} != requested {wanted_color}"

    if qualifiers:
        wanted_pct = qualifiers.get("discount_percent")
        if wanted_pct is not None:
            got_pct = _desc_discount_percent(item_desc)
            if got_pct is not None and not math.isclose(
                    got_pct, float(wanted_pct), abs_tol=1e-6):
                return True, (f"promotion {got_pct:g}% != requested "
                              f"{float(wanted_pct):g}%")

    return False, None


def _on_word_boundary(haystack: str, needle: str) -> bool:
    """True if `needle` occurs in `haystack` not glued to another word."""
    if not needle:
        return False
    i = haystack.find(needle)
    while i != -1:
        before = haystack[i - 1] if i > 0 else " "
        after_i = i + len(needle)
        after = haystack[after_i] if after_i < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = haystack.find(needle, i + 1)
    return False


@dataclass
class Candidate:
    item_nb: str
    item_desc: str
    category: str
    score: float
    method: str
    attribute_conflict: bool = False
    conflict_reason: str | None = None

    def dict(self):
        return asdict(self)


def tied_with_top(cands: list, epsilon: float = TIE_EPSILON,
                  key=lambda c: c.score) -> list:
    """Every candidate within `epsilon` of the top score - a coin flip
    between distinct items, not a genuine match. `cands` must be sorted
    best-first (by `key`, descending); empty input returns empty."""
    if not cands:
        return []
    top_score = key(cands[0])
    return [c for c in cands if top_score - key(c) <= epsilon]


def unique_top(cands: list[Candidate], epsilon: float = TIE_EPSILON
              ) -> Candidate | None:
    """The top-scoring candidate, but only if it's uniquely best per
    `tied_with_top`."""
    tied = tied_with_top(cands, epsilon)
    return cands[0] if len(tied) == 1 else None


class ItemResolver:
    def __init__(self, session, accept=None, suggest=None):
        self.s = session
        self.accept = accept if accept is not None else settings.fuzzy_accept
        self.suggest = suggest if suggest is not None else settings.fuzzy_suggest

    def _history(self, cust_nb: str) -> set[str]:
        return set(self.s.execute(
            select(OrderDetail.item_nb)
            .join(OrderHeader,
                  (OrderDetail.order_nb == OrderHeader.order_nb) &
                  (OrderDetail.order_type == OrderHeader.order_type))
            .where(OrderHeader.cust_nb == cust_nb)
        ).scalars().all())

    def resolve(self, raw: str, cust_nb: str | None = None,
                attributes: dict | None = None, qualifiers: dict | None = None):
        q = (raw or "").strip()
        if not q:
            return None, []

        def _exact(cands_in: list[Candidate]):
            by_item: dict[str, Candidate] = {}
            for c in cands_in:
                by_item.setdefault(c.item_nb, c)
            cands = list(by_item.values())
            for c in cands:
                conflict, reason = _attribute_conflict(c.item_desc, attributes,
                                                       qualifiers)
                c.attribute_conflict, c.conflict_reason = conflict, reason
            clean = [c for c in cands if not c.attribute_conflict]
            if len(clean) == 1:
                return clean[0], cands
            return None, cands

        it = self.s.get(Item, q.upper())
        if it:
            c = Candidate(it.item_number, it.item_desc, it.category, 1.0,
                          MatchMethod.exact.value)
            return _exact([c])

        desc_matches = self.s.scalars(
            select(Item).where(
                Item.item_desc.ilike(_escape_ilike(q), escape="\\"))).all()
        if desc_matches:
            cands = [Candidate(it.item_number, it.item_desc, it.category, 0.98,
                               MatchMethod.exact.value) for it in desc_matches]
            return _exact(cands)

        q_fuzzy = expand_size_synonyms(normalize_text(q))
        # No SQL Server equivalent of Postgres' pg_trgm `%`/similarity() -
        # that operator/function did double duty as prefilter and score
        # here. RapidFuzz (already the second-layer scorer below) now does
        # both: score every catalogue item directly, keep the top 30. If
        # catalogue size makes scoring everything too slow in practice, add
        # a real prefilter (e.g. a SQL Server FULLTEXT index) ahead of this
        # - not done here with no real-catalogue benchmark to size it
        # against.
        catalogue = self.s.execute(
            select(Item.item_number, Item.item_desc, Item.category)).all()
        rows = sorted(
            ((fuzz.token_set_ratio(q_fuzzy.lower(), it.item_desc.lower()) / 100.0,
             it.item_number, it.item_desc, it.category) for it in catalogue),
            key=lambda r: r[0], reverse=True)[:30]

        hist = self._history(cust_nb) if cust_nb else set()
        best_raw: dict[str, tuple[float, str, str | None, str]] = {}
        for score, item_number, item_desc, category in rows:
            if item_number in hist:
                score = min(1.0, score + 0.10)
            if item_number not in best_raw or score > best_raw[item_number][0]:
                best_raw[item_number] = (score, item_desc, category, "fuzzy")

        best: dict[str, Candidate] = {}
        for item_number, (score, item_desc, category, method) in best_raw.items():
            conflict, reason = _attribute_conflict(item_desc, attributes, qualifiers)
            if conflict:
                score = max(0.0, score - settings.attribute_conflict_penalty)
            best[item_number] = Candidate(
                item_number, item_desc, category, round(score, 3), method,
                attribute_conflict=conflict, conflict_reason=reason)

        cands = sorted(best.values(), key=lambda c: c.score, reverse=True)
        cands = [c for c in cands if c.score >= self.suggest][:5]
        top = None
        if cands and cands[0].score >= self.accept and not cands[0].attribute_conflict:
            top = unique_top(cands)
        return top, cands
