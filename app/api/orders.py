from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.errors import CatalogError
from app.models import Customer, OrderHeader
from app.schemas.models import (CreateOrderIn, CreateOrderOut, OrderLineOut,
                                OrderOut, ResolveTargetIn, ResolveTargetOut)
from app.services.orders import LineEditIn as ServiceLineEdit
from app.services.orders import create_order
from app.services.prior_order import PriorOrderService
from app.services.qra_engine import OrderLineIn

router = APIRouter(tags=["orders"], dependencies=[Depends(require_api_key)])


def _lines_out(details) -> list[OrderLineOut]:
    return [OrderLineOut(line_nb=d.line_nb, item_nb=d.item_nb,
                         item_desc=d.item_desc, qty=d.qty, uom=d.uom,
                         is_free=d.is_free) for d in details]


@router.get("/orders/recent", response_model=list[OrderOut])
def recent_orders(salesman_id: str | None = Query(default=None),
                  admin: bool = Query(default=False),
                  limit: int = Query(30, le=100), s: Session = Depends(get_db)):
    """Recently committed orders for the caller's own customers (or every
    customer, for an admin) - backs the backend's /orders/recent (the
    Android app's "reorder without a network round trip" cache).

    Ordered by order_nb, newest first: order_header carries no created_at
    of its own (dropped - see alembic history), but order_nb is minted from
    a global monotonic sequence prefixed with the 2-digit year
    (OrderNumberService.next), so it sorts chronologically for any
    realistic span without needing a real timestamp column.

    salesman_id/admin: same trusted-caller contract as /customers/search -
    supplied by the backend from its own authenticated identity, never a
    raw end-user parameter. This is what replaces the backend's old
    decided_by-based lookup (see PendingRequest, no longer kept around
    after a request is committed) with something that works purely off
    order_header/customer, which live here."""
    if not admin and not salesman_id:
        return []
    q = (select(OrderHeader)
        .order_by(OrderHeader.order_nb.desc())
        .limit(limit))
    if not admin:
        q = q.join(Customer, Customer.customer_number == OrderHeader.cust_nb
                  ).where(Customer.salesman_id == salesman_id)
    headers = list(s.scalars(q))
    prior = PriorOrderService(s)
    out = []
    for header in headers:
        cust = s.get(Customer, header.cust_nb)
        out.append(OrderOut(
            order_nb=header.order_nb, order_type=header.order_type,
            cust_nb=header.cust_nb,
            customer_name=cust.customer_name if cust else None,
            lines=_lines_out(prior.lines_of(header))))
    return out


@router.get("/orders/{order_nb}/{order_type}", response_model=OrderOut | None)
def get_order(order_nb: str, order_type: str, s: Session = Depends(get_db)):
    """A specific order and its lines, by primary key - backs the
    backend's return/reorder flows (fetch the order a return/reorder
    references) once the order_nb is already known."""
    header = s.get(OrderHeader, (order_nb, order_type))
    if header is None:
        return None
    cust = s.get(Customer, header.cust_nb)
    lines = PriorOrderService(s).lines_of(header)
    return OrderOut(order_nb=header.order_nb, order_type=header.order_type,
                    cust_nb=header.cust_nb,
                    customer_name=cust.customer_name if cust else None,
                    lines=_lines_out(lines))


@router.get("/orders/by-so-nb/{ref}", response_model=OrderOut | None)
def get_so_by_nb(ref: str, s: Session = Depends(get_db)):
    """The sales order (order_type="SO") for `ref`, regardless of
    customer, with exact-then-digits-only fallback normalization - see
    PriorOrderService.find_so_by_order_nb. Used by both return_order
    (references an order number directly) and reorder's order_nb mode."""
    header = PriorOrderService(s).find_so_by_order_nb(ref)
    if header is None:
        return None
    cust = s.get(Customer, header.cust_nb)
    lines = PriorOrderService(s).lines_of(header)
    return OrderOut(order_nb=header.order_nb, order_type=header.order_type,
                    cust_nb=header.cust_nb,
                    customer_name=cust.customer_name if cust else None,
                    lines=_lines_out(lines))


@router.post("/orders/resolve-target", response_model=ResolveTargetOut)
def resolve_target(body: ResolveTargetIn, s: Session = Depends(get_db)):
    """Resolve a reorder target for `cust_nb`. mode="explicit" is
    PriorOrderService.resolve_target_explicit (only "order_nb" reference
    kind exists); mode="implicit" is resolve_target (free-form reference,
    falling back to open-order-count disambiguation when absent/not
    found) - see that service for the exact semantics."""
    prior = PriorOrderService(s)
    if body.mode == "explicit":
        header, ambiguity = prior.resolve_target_explicit(
            body.cust_nb, "order_nb", body.reference)
    else:
        header, ambiguity = prior.resolve_target(body.cust_nb, body.reference)

    if header is None:
        return ResolveTargetOut(ambiguity=ambiguity)
    lines = prior.lines_of(header)
    return ResolveTargetOut(order_nb=header.order_nb, order_type=header.order_type,
                            cust_nb=header.cust_nb, ambiguity=None,
                            lines=_lines_out(lines))


@router.post("/orders", response_model=CreateOrderOut)
def commit_order(body: CreateOrderIn, s: Session = Depends(get_db)):
    """Create an order (RETURN/reorder target resolution, customer/
    already-returned validation, line editing, QRA application, and
    OrderHeader/OrderDetail creation, all in one transaction) - the
    catalog-service side of the backend's commit saga. Idempotent on
    commit_intent_id: a retried call returns the order already created
    for it instead of erroring or double-creating.
    """
    try:
        result = create_order(
            s, commit_intent_id=body.commit_intent_id,
            order_type=body.order_type, cust_nb=body.cust_nb,
            cust_nb_override=body.cust_nb_override,
            target_order_nb_override=body.target_order_nb_override,
            primary_intent=body.primary_intent, full_return=body.full_return,
            lines_in=[OrderLineIn(line_nb=l.line_nb, item_nb=l.item_nb,
                                  item_desc=l.item_desc, category=l.category,
                                  qty=l.qty, uom=l.uom) for l in body.lines],
            line_edits=[ServiceLineEdit(line_nb=e.line_nb, item_nb=e.item_nb,
                                        item_desc=e.item_desc, qty=e.qty, uom=e.uom)
                       for e in body.line_edits],
            removed_line_nbs=body.removed_line_nbs, is_return=body.is_return,
            acting_salesman_id=body.acting_salesman_id,
            acting_is_admin=body.acting_is_admin)
    except CatalogError as e:
        raise HTTPException(422, {"code": e.code, "detail": str(e)})

    return CreateOrderOut(
        order_nb=result.order_nb, order_type=result.order_type,
        cust_nb=result.cust_nb, target_order_nb=result.target_order_nb,
        target_order_type=result.target_order_type,
        lines=[OrderLineOut(line_nb=l.line_nb, item_nb=l.item_nb,
                            item_desc=l.item_desc, qty=l.qty, uom=l.uom,
                            is_free=l.is_free) for l in result.lines])
