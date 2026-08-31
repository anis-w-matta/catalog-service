"""QRA (Quantity Rebate Agreement) matching. Same core logic that used to
live in the backend's app/services/qra_engine.py, adapted to work against
plain line dicts (OrderLineIn) instead of the backend's PendingLine ORM
rows, since PendingLine stays in the backend - this service only ever
sees the resolved item/qty/uom values the backend sends over the wire.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import Item, QraDetail, QraHeader
from app.services.quantity_uom import canonical_uom


@dataclass
class OrderLineIn:
    """One resolved line as sent by the backend for order creation/QRA
    evaluation - line_nb is 1-based and matches the order the backend
    displays the lines in."""
    line_nb: int
    item_nb: str | None
    item_desc: str | None
    category: str | None
    qty: Decimal | None
    uom: str | None
    is_free: bool = False


@dataclass
class QraLineEffect:
    unit_price: Decimal | None
    is_free: bool


@dataclass
class _EvalResult:
    effects: dict[int, QraLineEffect] = field(default_factory=dict)
    # line_nb -> (item_nb, item_desc, category) for a type B substitution.
    substitutions: dict[int, tuple[str, str | None, str | None]] = field(
        default_factory=dict)
    bonus_lines: list[OrderLineIn] = field(default_factory=list)


def _active_detail(session, cust_nb: str) -> QraDetail | None:
    # cust_nb is qra_detail's primary key - at most one rule (of one type:
    # T, B, or P) can ever be active for a customer at a time.
    today = date.today()
    return session.execute(
        select(QraDetail).join(
            QraHeader, QraDetail.cust_nb == QraHeader.cust_nb
        ).where(QraHeader.cust_nb == cust_nb, QraHeader.status == "active",
               QraHeader.from_date <= today, QraHeader.to_date >= today)
    ).scalars().first()


def _bonus_multiples(qty: Decimal, qty_buy: Decimal, is_return: bool) -> int:
    """How many complete qty_buy groups `qty` represents, for T/B bonus
    quantity (qty_get * multiples).

    A forward sale only earns a bonus for whole groups actually reached -
    floor division. A return works the other way: the business wants
    every free item tied to any touched group clawed back, so a nonzero
    remainder rounds the group count up rather than down.
    """
    b, a = divmod(qty, qty_buy)
    b = int(b)
    return b + 1 if is_return and a != 0 else b


def _evaluate(session, detail: QraDetail | None, lines: list[OrderLineIn],
             is_return: bool = False) -> _EvalResult:
    """Shared matching core for apply_qra/preview_qra - never mutates
    `lines`.

    A customer has at most one active rule (`detail`), of exactly one
    type. Type P is keyed by item_nb_price - it applies only to a line
    ordering that specific item, once it reaches qty_buy. Types T/B are
    keyed by item_nb_buy the same way. Quantity matching is unit-agnostic
    (no uom_buy/uom_get on qra_detail).

    `is_return` selects the T/B bonus-multiples rounding rule.
    """
    result = _EvalResult()
    if detail is None or not detail.qty_buy or detail.qty_buy <= 0:
        return result

    next_line_nb = max((l.line_nb for l in lines), default=0) + 1
    match_item_nb = (detail.item_nb_price if detail.qra_type == "P"
                     else detail.item_nb_buy)

    for line in lines:
        if line.item_nb is None or line.qty is None:
            continue
        if line.item_nb != match_item_nb or line.qty < detail.qty_buy:
            continue

        if detail.qra_type == "P":
            result.effects[line.line_nb] = QraLineEffect(
                unit_price=detail.qra_price, is_free=False)
            continue

        if detail.qra_type == "B":
            get_item = session.get(Item, detail.item_nb_get)
            item_desc = get_item.item_desc if get_item else line.item_desc
            category = get_item.category if get_item else line.category
            result.substitutions[line.line_nb] = (
                detail.item_nb_get, item_desc, category)
            result.effects[line.line_nb] = QraLineEffect(
                unit_price=detail.qra_price, is_free=False)

        if detail.qra_type in ("T", "B"):
            multiples = _bonus_multiples(line.qty, detail.qty_buy, is_return)
            if multiples <= 0:
                continue
            get_item = session.get(Item, detail.item_nb_get)
            result.bonus_lines.append(OrderLineIn(
                line_nb=next_line_nb,
                item_nb=detail.item_nb_get,
                item_desc=get_item.item_desc if get_item else "",
                category=get_item.category if get_item else None,
                qty=detail.qty_get * multiples,
                uom=canonical_uom(line.uom) or line.uom,
                is_free=True))
            result.effects[next_line_nb] = QraLineEffect(
                unit_price=Decimal("0"), is_free=True)
            next_line_nb += 1

    return result


def apply_qra(session, cust_nb: str | None, lines: list[OrderLineIn],
              is_return: bool = False) -> list[OrderLineIn]:
    """Evaluates the active QRA rule for `cust_nb` against `lines` and
    returns a new, final line list: a type P match overrides the price of
    its one named item in place (no item change, and price isn't
    persisted - see orders.py); a type B match converts a line's item to
    the rule's get-item; a type T/B match appends a new free bonus line.

    `is_return` must be True when committing a RETURN - see
    _bonus_multiples for why a return's bonus quantity rounds differently
    than a forward sale's.
    """
    if not cust_nb:
        return list(lines)
    detail = _active_detail(session, cust_nb)
    if detail is None:
        return list(lines)

    result = _evaluate(session, detail, lines, is_return=is_return)

    out: list[OrderLineIn] = []
    for line in lines:
        sub = result.substitutions.get(line.line_nb)
        if sub is None:
            out.append(line)
            continue
        item_nb, item_desc, category = sub
        out.append(OrderLineIn(
            line_nb=line.line_nb, item_nb=item_nb, item_desc=item_desc,
            category=category, qty=line.qty, uom=line.uom, is_free=False))
    out.extend(result.bonus_lines)
    return out


@dataclass
class QraLinePreview:
    """One line's QRA preview, shown on the pending-request review screen
    before Accept, since QRA only actually applies at commit time and
    there's no committed-order screen yet for a reviewer to see the
    effect otherwise."""
    line_nb: int
    unit_price: Decimal | None
    is_free: bool
    substituted_item_nb: str | None
    substituted_item_desc: str | None


@dataclass
class QraBonusLinePreview:
    item_nb: str
    item_desc: str
    qty: Decimal
    uom: str | None


def preview_qra(session, cust_nb: str | None, lines: list[OrderLineIn],
                is_return: bool = False
                ) -> tuple[list[QraLinePreview], list[QraBonusLinePreview]]:
    """Read-only preview of what apply_qra() would do to `lines` at commit
    time - never mutates `lines`, never writes anything. `is_return` must
    match what the eventual commit will pass - see apply_qra.
    """
    if not cust_nb:
        return [], []
    detail = _active_detail(session, cust_nb)
    if detail is None:
        return [], []

    views = [l for l in lines if l.item_nb is not None and l.qty is not None]
    result = _evaluate(session, detail, views, is_return=is_return)

    line_previews = [
        QraLinePreview(
            line_nb=line_nb, unit_price=effect.unit_price,
            is_free=effect.is_free,
            substituted_item_nb=result.substitutions.get(line_nb, (None,))[0],
            substituted_item_desc=result.substitutions.get(
                line_nb, (None, None))[1])
        for line_nb, effect in result.effects.items()
        if line_nb in {v.line_nb for v in views}
    ]
    bonus_previews = [
        QraBonusLinePreview(item_nb=b.item_nb, item_desc=b.item_desc,
                            qty=b.qty, uom=b.uom)
        for b in result.bonus_lines
    ]
    return line_previews, bonus_previews
