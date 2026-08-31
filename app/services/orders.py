"""Order creation - the catalog-service side of the backend's commit saga
(see app/api/orders.py). This is almost exactly what used to be the
backend's OrderCommitService.commit() body: RETURN/reorder target
resolution, customer/already-returned validation, line editing, QRA
application, and OrderHeader/OrderDetail creation, all in one local
transaction now that the data those checks touch lives here.

What stayed in the backend: PendingRequest status checks
(already-committed/rejected), the final "mark this request committed"
write, and activity logging - none of that data lives here.
"""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.errors import (CustomerNotAuthorized, CustomerNotFound,
                        OrderAlreadyReturned, TargetOrderNotFound,
                        UnresolvedLines)
from app.models import Customer, OrderDetail, OrderHeader
from app.services.numbering import OrderNumberService
from app.services.prior_order import PriorOrderService
from app.services.qra_engine import OrderLineIn, apply_qra
from app.services.quantity_uom import canonical_uom

# The business only orders in two units - enforced here too, not just by
# the Android picker, since nothing stops a raw API caller from sending
# anything else.
VALID_UOMS = {"EACH", "PKT"}


@dataclass
class LineEditIn:
    line_nb: int
    item_nb: str | None = None
    item_desc: str | None = None
    qty: Decimal | None = None
    uom: str | None = None


@dataclass
class CreateOrderResult:
    order_nb: str
    order_type: str
    cust_nb: str
    target_order_nb: str | None
    target_order_type: str | None
    lines: list[OrderLineIn]


def _lines_from_prior(prior_details) -> list[OrderLineIn]:
    """OrderLineIn rows built from a customer's prior OrderDetail rows -
    the RETURN-full-backfill and reorder-target-correction baseline.
    Drops any line the prior order recorded as a QRA free bonus
    (is_free=True): the caller re-runs apply_qra on the paid lines this
    returns before commit, so carrying a previously-free line forward
    would both re-price it as a paid item and double it up against the
    bonus line QRA freshly computes.
    """
    paid = [d for d in prior_details if not d.is_free]
    return [OrderLineIn(line_nb=n, item_nb=d.item_nb, item_desc=d.item_desc,
                        category=None, qty=d.qty, uom=d.uom)
           for n, d in enumerate(paid, start=1)]


def _apply_edits(lines: list[OrderLineIn], line_edits: list[LineEditIn],
                 removed_line_nbs: list[int]) -> list[OrderLineIn]:
    removed = set(removed_line_nbs)
    result = [l for l in lines if l.line_nb not in removed]
    by_nb = {l.line_nb: l for l in result}
    for e in line_edits:
        if e.line_nb in removed:
            continue
        line = by_nb.get(e.line_nb)
        if not line:
            line = OrderLineIn(line_nb=e.line_nb, item_nb=None,
                               item_desc=None, category=None, qty=None,
                               uom=None)
            result.append(line)
            by_nb[e.line_nb] = line
        if e.item_nb is not None:
            line.item_nb = e.item_nb
        if e.item_desc is not None:
            line.item_desc = e.item_desc
        if e.qty is not None:
            line.qty = e.qty
        if e.uom is not None:
            line.uom = canonical_uom(e.uom) or e.uom
    return result


def create_order(session, *, commit_intent_id: str, order_type: str,
                 cust_nb: str | None, cust_nb_override: str | None,
                 target_order_nb_override: str | None,
                 primary_intent: str | None, full_return: bool,
                 lines_in: list[OrderLineIn],
                 line_edits: list[LineEditIn],
                 removed_line_nbs: list[int],
                 is_return: bool,
                 acting_salesman_id: str,
                 acting_is_admin: bool = False) -> CreateOrderResult:
    # Idempotency: a retried commit (network timeout, the backend's
    # reconciliation sweep after a crash between "catalog-service created
    # the order" and "backend recorded that locally") sends the same
    # commit_intent_id - return the order already created for it instead
    # of re-running validation/QRA a second time (which could legitimately
    # produce a different result if data changed in between) or erroring.
    existing = session.scalars(select(OrderHeader).where(
        OrderHeader.commit_intent_id == commit_intent_id)).first()
    if existing is not None:
        prior = PriorOrderService(session)
        details = prior.lines_of(existing)
        return CreateOrderResult(
            order_nb=existing.order_nb, order_type=existing.order_type,
            cust_nb=existing.cust_nb,
            target_order_nb=(existing.order_nb
                             if existing.order_type == "RETURN" else None),
            target_order_type=None,
            lines=[OrderLineIn(line_nb=d.line_nb, item_nb=d.item_nb,
                               item_desc=d.item_desc, category=None,
                               qty=d.qty, uom=d.uom, is_free=d.is_free)
                  for d in details])

    prior = PriorOrderService(session)
    lines = list(lines_in)
    target_order_nb: str | None = None
    target_order_type: str | None = None

    # A return's customer is never picked independently - it's pulled from
    # the sales order it's returning against.
    if order_type == "RETURN" and target_order_nb_override:
        target = session.get(OrderHeader, (target_order_nb_override, "SO"))
        if target is None:
            raise TargetOrderNotFound(target_order_nb_override)
        target_order_nb = target_order_nb_override
        cust_nb = target.cust_nb
        if not lines and not line_edits and full_return:
            lines = _lines_from_prior(prior.lines_of(target))
    elif (order_type == "SO" and target_order_nb_override and
          primary_intent in ("repeat_order", "repeat_order_adjusted")):
        # An order number identifies its own customer unambiguously - lets
        # a reorder correction resolve even when no customer was named or
        # was misheard.
        target = prior.find_so_by_order_nb(target_order_nb_override)
        if target is None:
            raise TargetOrderNotFound(target_order_nb_override)
        target_order_nb = target.order_nb
        target_order_type = target.order_type
        cust_nb = target.cust_nb
        if not line_edits:
            baseline = _lines_from_prior(prior.lines_of(target))
            merged = list(baseline)
            index_by_key = {(l.item_nb, l.uom): i
                            for i, l in enumerate(merged) if l.item_nb}
            for adj in lines:
                idx = (index_by_key.get((adj.item_nb, adj.uom))
                      if adj.item_nb else None)
                if idx is None:
                    merged.append(adj)
                else:
                    merged[idx] = adj
            for n, line in enumerate(merged, start=1):
                line.line_nb = n
            lines = merged
    elif cust_nb_override:
        cust_nb = cust_nb_override

    # Database is the source of truth for customer identity: an order must
    # never be created for a customer number that doesn't actually exist.
    customer = session.get(Customer, cust_nb) if cust_nb else None
    if customer is None:
        raise CustomerNotFound(cust_nb)

    # Database is also the source of truth for ownership: whichever path
    # above resolved the final cust_nb (RETURN/reorder target, an
    # operator's manual override, or the request's own already-matched
    # customer), a non-admin salesman may only place an order for a
    # customer assigned to them. Checked here - after cust_nb is fully
    # resolved, before any OrderHeader/OrderDetail row is written - so an
    # unauthorized attempt leaves zero durable side effects, and can't be
    # bypassed by any of the target-resolution branches above (a RETURN or
    # reorder-by-order-number can silently resolve to a *different*
    # customer than the one named in the request, so this must run after
    # that resolution, not against the caller-supplied cust_nb).
    if not acting_is_admin and customer.salesman_id != acting_salesman_id:
        raise CustomerNotAuthorized(cust_nb)

    # A sales order can only be returned against once.
    if (order_type == "RETURN" and target_order_nb and
            session.get(OrderHeader,
                       (target_order_nb, "RETURN")) is not None):
        raise OrderAlreadyReturned(target_order_nb)

    lines = _apply_edits(lines, line_edits, removed_line_nbs)

    if not lines or any(
        l.item_nb is None or l.qty is None or
        canonical_uom(l.uom) not in VALID_UOMS for l in lines
    ):
        raise UnresolvedLines()

    final_lines = apply_qra(session, cust_nb, lines, is_return=is_return)

    # A return references the order it's returning against (reusing that
    # number instead of minting a fresh one keeps a return and the order
    # it belongs to under the same document number) - safe unconditionally
    # here, the already-returned guard above already refused a second
    # return before any RETURN row could exist for this target_order_nb.
    reused_nb = order_type == "RETURN" and target_order_nb
    order_nb = target_order_nb if reused_nb else OrderNumberService(session).next()

    session.add(OrderHeader(order_nb=order_nb, order_type=order_type,
                            cust_nb=cust_nb, commit_intent_id=commit_intent_id))
    for i, line in enumerate(final_lines, start=1):
        session.add(OrderDetail(order_nb=order_nb, order_type=order_type,
                                line_nb=i, item_nb=line.item_nb,
                                item_desc=line.item_desc or "",
                                qty=line.qty, uom=canonical_uom(line.uom),
                                line_type="S", is_free=line.is_free))
    session.flush()

    return CreateOrderResult(
        order_nb=order_nb, order_type=order_type, cust_nb=cust_nb,
        target_order_nb=target_order_nb, target_order_type=target_order_type,
        lines=final_lines)
