import logging
import re

from sqlalchemy import select

from app.models import OrderDetail, OrderHeader

_log = logging.getLogger(__name__)

# Reorders are read-only by construction: every query below is either an
# ORM `select().where(Model.col == value)` or (in item_resolver.py) a
# `text()` query with named bound parameters - there is no string-built SQL
# anywhere on this path for a payload to inject into. This pattern is a
# best-effort *detector*, not the actual defence: it exists so an attempted
# injection still gets refused and logged instead of just silently failing
# to match anything, which would look identical to an ordinary typo.
#
# Deliberately excludes UPDATE/ALTER even though they're classic SQL
# keywords: "update"/"alter" are ordinary words a customer might actually
# say about their own order, and this service is reached from the
# update_order intent - flagging those would misfire on legitimate
# requests far more often than it would catch anything real. DROP/DELETE/
# INSERT/TRUNCATE/UNION/EXEC have no such legitimate use here.
_SUSPICIOUS = re.compile(
    r";|--|/\*|\bDROP\b|\bDELETE\b|\bINSERT\b|\bUNION\b|"
    r"\bTRUNCATE\b|\bEXEC(UTE)?\b",
    re.IGNORECASE)
MAX_REFERENCE_LEN = 200


class PriorOrderService:
    def __init__(self, session):
        self.s = session

    def open_orders(self, cust_nb: str):
        # No status column to filter on - every order is "open" by
        # construction. Ordered by order_nb for a deterministic result.
        return list(self.s.scalars(
            select(OrderHeader)
            .where(OrderHeader.cust_nb == cust_nb)
            .order_by(OrderHeader.order_nb)).all())

    def find_so_by_order_nb(self, order_nb: str | None):
        """The sales order (order_type="SO") OrderHeader for `order_nb`,
        regardless of customer - used by both return_order (references an
        order number directly, no customer named at all) and reorder's
        mode=order_nb. Scoping to SO means this can never go ambiguous
        just because a RETURN has since reused the same order_nb.
        (order_nb, order_type) is the primary key, so this is a single
        unambiguous lookup, never a guess among candidates. None if the
        reference is missing, doesn't exist, or looks SQL-injection-
        shaped - never guessed.

        Tries the reference exactly as given first; if that finds
        nothing, falls back to a digits-only reading of it, the same
        normalization resolve_target_explicit's order_nb mode applies.
        """
        if not order_nb:
            return None
        ref_text = order_nb[:MAX_REFERENCE_LEN]
        if _SUSPICIOUS.search(ref_text):
            _log.warning("blocked_injection_attempt: reorder order_nb "
                        "reference looked SQL-injection-shaped; refused "
                        "(reference=%r)", ref_text)
            return None
        header = self.s.get(OrderHeader, (ref_text, "SO"))
        if header is not None:
            return header
        digits = "".join(ch for ch in ref_text if ch.isdigit())
        if not digits or digits == ref_text:
            return None
        return self.s.get(OrderHeader, (digits, "SO"))

    def lines_of(self, header):
        return list(self.s.scalars(
            select(OrderDetail)
            .where(OrderDetail.order_nb == header.order_nb,
                   OrderDetail.order_type == header.order_type)
            .order_by(OrderDetail.line_nb)).all())

    def resolve_target(self, cust_nb: str, reference: str | None):
        if reference:
            ref_text = reference[:MAX_REFERENCE_LEN]
            if _SUSPICIOUS.search(ref_text):
                # Abort using this reference entirely rather than salvaging
                # digits out of it - a payload that trips the heuristic is
                # untrusted in full, not just in the parts that don't look
                # like digits.
                _log.warning("blocked_injection_attempt: reorder reference "
                            "for customer %s looked SQL-injection-shaped; "
                            "ignored, falling back to normal open-order "
                            "resolution (reference=%r)", cust_nb, ref_text)
            else:
                ref = "".join(ch for ch in ref_text if ch.isdigit())
                if ref:
                    h = self.s.scalars(select(OrderHeader).where(
                        OrderHeader.cust_nb == cust_nb,
                        OrderHeader.order_nb == ref)).first()
                    if h:
                        return h, None
        opens = self.open_orders(cust_nb)
        if len(opens) == 1:
            return opens[0], None
        if not opens:
            return None, "no_open_orders"
        return None, "multiple_open_orders"

    def resolve_target_explicit(self, cust_nb: str, mode: str,
                                value: str | None):
        """Resolve a reorder target from an explicitly-stated mode (only
        "order_nb" exists - "last"/"date" have no substitute since
        order_header carries no timestamp). Same (header, ambiguity_reason)
        contract: header is None (never guessed) whenever the target can't
        be resolved with certainty.
        """
        if mode == "order_nb":
            if not value:
                return None, "no_order_reference"
            ref_text = value[:MAX_REFERENCE_LEN]
            if _SUSPICIOUS.search(ref_text):
                _log.warning("blocked_injection_attempt: reorder order_nb "
                            "for customer %s looked SQL-injection-shaped; "
                            "refused (reference=%r)", cust_nb, ref_text)
                return None, "invalid_reference"
            ref = "".join(ch for ch in ref_text if ch.isdigit())
            if not ref:
                return None, "invalid_reference"
            rows = list(self.s.scalars(select(OrderHeader).where(
                OrderHeader.cust_nb == cust_nb, OrderHeader.order_nb == ref)))
            if len(rows) != 1:
                return None, ("order_not_found" if not rows else
                             "multiple_order_types")
            return rows[0], None

        return None, "unknown_mode"
