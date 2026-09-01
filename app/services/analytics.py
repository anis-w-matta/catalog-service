"""Read-only aggregation queries for VeNdO Intelligence (see
vendo-intelligence-web/docs/audit and the Phase 3 plan) - the first
GROUP BY/COUNT/SUM queries in this codebase. Every function here is a
plain read against this service's own tables; nothing here writes
anything or is reachable from the operational (order-committing) code
path, and none of it ever computes or returns a price/revenue/amount
field.

Salesman attribution for any order-scoped query uses
customer_ownership_history point-in-time (at the order's committed_at),
never Customer.salesman_id directly - see
vendo-intelligence-web/docs/audit/07_historical_attribution_risks.md for
why current ownership must never be used to attribute a historical order.
Orders with committed_at IS NULL (legacy, pre-Phase-2) can never be
placed in time or attributed this way - every function below excludes
them from date/salesman-filtered results and reports how many were
excluded rather than silently dropping or including them.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.models import (Customer, CustomerOwnershipHistory, Item,
                        OrderDetail, OrderHeader)


def _ownership_at_exists(salesman_id: str | None):
    """EXISTS(...): some customer_ownership_history row says `salesman_id`
    (None = unassigned) owned order_header.cust_nb at
    order_header.committed_at. Correlated to whatever OrderHeader is in
    scope in the enclosing query."""
    h = CustomerOwnershipHistory
    cond = [h.cust_nb == OrderHeader.cust_nb,
           h.effective_from <= OrderHeader.committed_at,
           (h.effective_to.is_(None)) | (h.effective_to > OrderHeader.committed_at)]
    cond.append(h.salesman_id.is_(None) if salesman_id is None
               else h.salesman_id == salesman_id)
    return exists(select(1).where(*cond))


@dataclass
class OrdersFilter:
    date_from: datetime | None = None
    date_to: datetime | None = None
    cust_nb: str | None = None
    item_nb: str | None = None
    category: str | None = None
    order_type: str | None = None
    salesman_id: str | None = None


def _details_query(f: OrdersFilter, *, only_missing_commit_date: bool = False):
    """OrderDetail rows matching f's structural filters (cust_nb, item_nb,
    category, order_type). If only_missing_commit_date, date_from/date_to/
    salesman_id are ignored and committed_at IS NULL is required instead -
    used to size the "excluded, no commit date" completeness count with
    the exact same structural filters as the real query."""
    q = select(OrderDetail, OrderHeader.cust_nb.label("cust_nb"),
              OrderHeader.committed_at.label("committed_at")).join(
        OrderHeader,
        (OrderDetail.order_nb == OrderHeader.order_nb) &
        (OrderDetail.order_type == OrderHeader.order_type))
    if f.category is not None:
        q = q.join(Item, Item.item_number == OrderDetail.item_nb)
        q = q.where(Item.category == f.category)
    if f.cust_nb is not None:
        q = q.where(OrderHeader.cust_nb == f.cust_nb)
    if f.order_type is not None:
        q = q.where(OrderHeader.order_type == f.order_type)
    if f.item_nb is not None:
        q = q.where(OrderDetail.item_nb == f.item_nb)

    if only_missing_commit_date:
        return q.where(OrderHeader.committed_at.is_(None))

    if f.date_from is not None:
        q = q.where(OrderHeader.committed_at >= f.date_from)
    if f.date_to is not None:
        q = q.where(OrderHeader.committed_at <= f.date_to)
    if f.salesman_id is not None:
        q = q.where(OrderHeader.committed_at.is_not(None))
        q = q.where(_ownership_at_exists(f.salesman_id))
    return q


def _order_id_concat(details_subquery):
    return func.concat(details_subquery.c.order_nb, "|",
                       details_subquery.c.order_type)


def _aggregate(session: Session, q) -> tuple[int, int, Decimal]:
    """(distinct order count, line count, summed qty) for a
    _details_query()-shaped OrderDetail selectable."""
    sub = q.subquery()
    order_count = session.scalar(
        select(func.count(func.distinct(_order_id_concat(sub))))) or 0
    line_count = session.scalar(select(func.count()).select_from(sub)) or 0
    item_quantity = session.scalar(
        select(func.coalesce(func.sum(sub.c.qty), 0))) or Decimal(0)
    return order_count, line_count, Decimal(item_quantity)


def _count_missing_commit_date(session: Session, f: OrdersFilter) -> int:
    """How many orders (matching every structural filter - cust_nb/item_nb/
    category/order_type - but ignoring date/salesman) have no committed_at
    at all, unconditionally. Callers whose result is only ever date/
    salesman-scoped in the first place (orders_trend: every point is a
    month bucket) must use this directly rather than
    _excluded_missing_commit_date, which suppresses the count when no
    date/salesman filter is active - correct for a query that only
    *optionally* cares about dates, wrong for one that structurally
    requires committed_at on every row regardless of filters."""
    q = _details_query(f, only_missing_commit_date=True)
    sub = q.subquery()
    return session.scalar(
        select(func.count(func.distinct(_order_id_concat(sub))))) or 0


def _excluded_missing_commit_date(session: Session, f: OrdersFilter) -> int:
    """How many orders (matching every filter except date/salesman) were
    left out of a date- or salesman-filtered result because they have no
    committed_at at all - zero when neither filter is active, since
    nothing was excluded on that basis. See _count_missing_commit_date for
    the unconditional version, used by queries that always require
    committed_at regardless of filters."""
    if f.date_from is None and f.date_to is None and f.salesman_id is None:
        return 0
    return _count_missing_commit_date(session, f)


@dataclass
class OrdersSummary:
    order_count: int
    order_line_count: int
    item_quantity: Decimal
    avg_items_per_order: Decimal | None
    orders_excluded_missing_commit_date: int


def orders_summary(session: Session, f: OrdersFilter) -> OrdersSummary:
    order_count, line_count, item_quantity = _aggregate(
        session, _details_query(f))
    avg = (item_quantity / order_count) if order_count else None
    return OrdersSummary(
        order_count=order_count, order_line_count=line_count,
        item_quantity=item_quantity, avg_items_per_order=avg,
        orders_excluded_missing_commit_date=_excluded_missing_commit_date(
            session, f))


_HISTOGRAM_BUCKETS = [
    (Decimal(0), Decimal(1), "0-1"), (Decimal(1), Decimal(5), "1-5"),
    (Decimal(5), Decimal(10), "5-10"), (Decimal(10), Decimal(25), "10-25"),
    (Decimal(25), Decimal(50), "25-50"),
]
_HISTOGRAM_LABELS = [label for _, _, label in _HISTOGRAM_BUCKETS] + ["50+"]


def _bucket_for(qty: Decimal) -> str:
    for lo, hi, label in _HISTOGRAM_BUCKETS:
        if lo < qty <= hi:
            return label
    return "50+"


@dataclass
class HistogramBucket:
    bucket: str
    order_count: int


def items_per_order_histogram(session: Session,
                              f: OrdersFilter) -> list[HistogramBucket]:
    """Buckets orders by their total item quantity (sum of qty across the
    order's lines) - "items per order" here means quantity, matching
    avg_items_per_order's own formula (item_quantity / order_count) above,
    not line count. Shows the distribution, not a salesman ranking - see
    07_phase_7_operations_ai.md's identical warning for the turnaround
    histogram."""
    sub = _details_query(f).subquery()
    order_key = _order_id_concat(sub)
    rows = session.execute(
        select(func.coalesce(func.sum(sub.c.qty), 0))
        .select_from(sub).group_by(order_key)).all()
    counts = {label: 0 for label in _HISTOGRAM_LABELS}
    for (qty,) in rows:
        counts[_bucket_for(Decimal(qty))] += 1
    return [HistogramBucket(bucket=label, order_count=counts[label])
           for label in _HISTOGRAM_LABELS]


@dataclass
class SalesmanOrderMetrics:
    salesman_id: str | None  # None row = the "unassigned at commit time" bucket
    order_count: int
    order_line_count: int
    item_quantity: Decimal
    customer_count: int


@dataclass
class SalesmenOrderMetricsResult:
    by_salesman: list[SalesmanOrderMetrics]
    orders_excluded_missing_commit_date: int


def salesmen_order_metrics(session: Session,
                           f: OrdersFilter) -> SalesmenOrderMetricsResult:
    """Per-salesman order/line/quantity/customer-count, attributed by who
    owned the customer at each order's committed_at (never current
    ownership). Orders lacking committed_at cannot be attributed to
    anyone and are excluded (counted, not silently dropped) - see module
    docstring."""
    excluded = _excluded_missing_commit_date(session, f)

    base = _details_query(f).where(OrderHeader.committed_at.is_not(None))
    sub = base.subquery()
    h = CustomerOwnershipHistory
    owner = (
        select(h.salesman_id)
        .where(h.cust_nb == sub.c.cust_nb,
              h.effective_from <= sub.c.committed_at,
              (h.effective_to.is_(None)) | (h.effective_to > sub.c.committed_at))
        .correlate(sub)
        .scalar_subquery()
    ).label("owner_salesman_id")

    rows = session.execute(
        select(owner,
              func.count(func.distinct(_order_id_concat(sub))),
              func.count(),
              func.coalesce(func.sum(sub.c.qty), 0),
              func.count(func.distinct(sub.c.cust_nb)))
        .select_from(sub)
        .group_by(owner)
    ).all()

    by_salesman = [
        SalesmanOrderMetrics(
            salesman_id=r[0], order_count=r[1], order_line_count=r[2],
            item_quantity=Decimal(r[3]), customer_count=r[4])
        for r in rows
    ]
    return SalesmenOrderMetricsResult(
        by_salesman=by_salesman,
        orders_excluded_missing_commit_date=excluded)


@dataclass
class TrendPoint:
    bucket: str  # "YYYY-MM"
    order_count: int
    order_line_count: int
    item_quantity: Decimal


@dataclass
class OrdersTrendResult:
    points: list[TrendPoint]
    orders_excluded_missing_commit_date: int


_TREND_FORMATS = {"day": "YYYY-MM-DD", "month": "YYYY-MM"}


def orders_trend(session: Session, f: OrdersFilter,
                 granularity: str = "month") -> OrdersTrendResult:
    """Order/line/quantity trend, bucketed by order_header.committed_at
    (never a request timestamp - see
    docs/audit/07_historical_attribution_risks.md). granularity: "month"
    (default - Phase 6 Command Center, Phase 7 salesman trends) or "day"
    (Phase 12 anomaly baselines, which need daily resolution for
    meaningful 7-day/30-day rolling comparisons - a monthly bucket is too
    coarse to detect a volume spike inside a single month). Reused
    fleet-wide and per-salesman (via f.salesman_id - same point-in-time
    ownership join as salesmen_order_metrics). Orders without a
    committed_at can't be placed on a timeline at all and are excluded
    (counted, not silently dropped) - always counted, even with no date/
    salesman filter, since every point on this trend structurally requires
    committed_at (unlike orders_summary/salesmen_order_metrics, which only
    need it once a date/salesman filter narrows the result)."""
    if granularity not in _TREND_FORMATS:
        raise ValueError(f"unknown granularity {granularity!r}")
    excluded = _count_missing_commit_date(session, f)
    base = _details_query(f).where(OrderHeader.committed_at.is_not(None))
    sub = base.subquery()
    bucket = func.to_char(func.date_trunc(granularity, sub.c.committed_at),
                          _TREND_FORMATS[granularity]).label("bucket")
    rows = session.execute(
        select(bucket,
              func.count(func.distinct(_order_id_concat(sub))),
              func.count(),
              func.coalesce(func.sum(sub.c.qty), 0))
        .select_from(sub)
        .group_by(bucket)
        .order_by(bucket)
    ).all()
    points = [TrendPoint(bucket=r[0], order_count=r[1], order_line_count=r[2],
                         item_quantity=Decimal(r[3])) for r in rows]
    return OrdersTrendResult(points=points,
                             orders_excluded_missing_commit_date=excluded)


@dataclass
class CustomerOrderHistoryRow:
    order_nb: str
    order_type: str
    committed_at: datetime
    item_quantity: Decimal
    order_line_count: int


def customer_order_history(session: Session,
                           cust_nb: str) -> list[CustomerOrderHistoryRow]:
    """One customer's committed orders, oldest first - the raw material
    Phase 8's frequency/interval/activity-state classification is computed
    from (in the BFF, not here - this function only returns real per-order
    facts, never a derived judgment). Orders with no committed_at (legacy,
    pre-Phase-2) are excluded: they can't be placed on a timeline, and
    this endpoint's whole purpose is interval analysis over time - see
    docs/audit/06_data_limitations.md."""
    rows = session.execute(
        select(OrderDetail.order_nb, OrderDetail.order_type,
              OrderHeader.committed_at, func.coalesce(func.sum(OrderDetail.qty), 0),
              func.count())
        .select_from(OrderDetail)
        .join(OrderHeader, (OrderDetail.order_nb == OrderHeader.order_nb) &
              (OrderDetail.order_type == OrderHeader.order_type))
        .where(OrderHeader.cust_nb == cust_nb, OrderHeader.committed_at.is_not(None))
        .group_by(OrderDetail.order_nb, OrderDetail.order_type, OrderHeader.committed_at)
        .order_by(OrderHeader.committed_at)
    ).all()
    return [CustomerOrderHistoryRow(order_nb=r[0], order_type=r[1],
                                    committed_at=r[2], item_quantity=Decimal(r[3]),
                                    order_line_count=r[4]) for r in rows]


@dataclass
class RankedCustomer:
    cust_nb: str
    customer_name: str
    order_count: int
    item_quantity: Decimal


def top_customers(session: Session, f: OrdersFilter, order_by: str,
                  limit: int) -> list[RankedCustomer]:
    """order_by: "order_count" or "item_quantity" - two separate rankings,
    never blended into one ambiguous "value" metric."""
    sub = _details_query(f).subquery()
    order_metric = func.count(func.distinct(_order_id_concat(sub)))
    qty_metric = func.coalesce(func.sum(sub.c.qty), 0)
    rank_col = order_metric if order_by == "order_count" else qty_metric

    rows = session.execute(
        select(sub.c.cust_nb, Customer.customer_name, order_metric, qty_metric)
        .select_from(sub)
        .join(Customer, Customer.customer_number == sub.c.cust_nb)
        .group_by(sub.c.cust_nb, Customer.customer_name)
        .order_by(rank_col.desc())
        .limit(limit)
    ).all()
    return [RankedCustomer(cust_nb=r[0], customer_name=r[1], order_count=r[2],
                           item_quantity=Decimal(r[3])) for r in rows]


@dataclass
class RankedItem:
    item_nb: str
    item_desc: str
    category: str
    item_quantity: Decimal
    order_count: int
    customer_count: int


def top_items(session: Session, f: OrdersFilter, order_by: str,
              limit: int) -> list[RankedItem]:
    """order_by: "quantity" (sum of qty) or "order_frequency" (distinct
    orders containing the item) - two separate rankings. customer_count
    (Phase 9 "customer penetration") is a third, always-included column,
    never a rankable metric of its own here - see item_penetration_rank
    below for that."""
    sub = _details_query(f).subquery()
    qty_metric = func.coalesce(func.sum(sub.c.qty), 0)
    order_metric = func.count(func.distinct(_order_id_concat(sub)))
    customer_metric = func.count(func.distinct(sub.c.cust_nb))
    rank_col = qty_metric if order_by == "quantity" else order_metric

    rows = session.execute(
        select(sub.c.item_nb, Item.item_desc, Item.category, qty_metric,
              order_metric, customer_metric)
        .select_from(sub)
        .join(Item, Item.item_number == sub.c.item_nb)
        .group_by(sub.c.item_nb, Item.item_desc, Item.category)
        .order_by(rank_col.desc())
        .limit(limit)
    ).all()
    return [RankedItem(item_nb=r[0], item_desc=r[1], category=r[2],
                       item_quantity=Decimal(r[3]), order_count=r[4],
                       customer_count=r[5])
           for r in rows]


@dataclass
class CategorySummary:
    category: str
    item_quantity: Decimal
    order_count: int
    customer_count: int
    share_of_total_quantity: Decimal | None


def categories_summary(session: Session, f: OrdersFilter) -> list[CategorySummary]:
    sub = _details_query(f).subquery()
    qty_metric = func.coalesce(func.sum(sub.c.qty), 0)
    order_metric = func.count(func.distinct(_order_id_concat(sub)))
    customer_metric = func.count(func.distinct(sub.c.cust_nb))

    rows = session.execute(
        select(Item.category, qty_metric, order_metric, customer_metric)
        .select_from(sub)
        .join(Item, Item.item_number == sub.c.item_nb)
        .group_by(Item.category)
        .order_by(qty_metric.desc())
    ).all()
    total = sum((Decimal(r[1]) for r in rows), Decimal(0))
    return [
        CategorySummary(
            category=r[0], item_quantity=Decimal(r[1]), order_count=r[2],
            customer_count=r[3],
            share_of_total_quantity=(Decimal(r[1]) / total) if total else None)
        for r in rows
    ]


@dataclass
class CustomersSummary:
    total: int
    assigned: int
    unassigned: int


def customers_summary(session: Session) -> CustomersSummary:
    total = session.scalar(select(func.count()).select_from(Customer)) or 0
    assigned = session.scalar(
        select(func.count()).select_from(Customer)
        .where(Customer.salesman_id.is_not(None))) or 0
    return CustomersSummary(total=total, assigned=assigned,
                            unassigned=total - assigned)


@dataclass
class CustomerDetailSummary:
    cust_nb: str
    customer_name: str
    current_salesman_id: str | None
    order_count: int
    order_line_count: int
    item_quantity: Decimal
    avg_items_per_order: Decimal | None
    last_order_committed_at: datetime | None


def customer_summary(session: Session, cust_nb: str) -> CustomerDetailSummary | None:
    customer = session.get(Customer, cust_nb)
    if customer is None:
        return None
    f = OrdersFilter(cust_nb=cust_nb)
    order_count, line_count, item_quantity = _aggregate(
        session, _details_query(f))
    avg = (item_quantity / order_count) if order_count else None
    last_committed_at = session.scalar(
        select(func.max(OrderHeader.committed_at))
        .where(OrderHeader.cust_nb == cust_nb))
    return CustomerDetailSummary(
        cust_nb=customer.customer_number, customer_name=customer.customer_name,
        current_salesman_id=customer.salesman_id, order_count=order_count,
        order_line_count=line_count, item_quantity=item_quantity,
        avg_items_per_order=avg, last_order_committed_at=last_committed_at)


@dataclass
class ItemDetailSummary:
    item_nb: str
    item_desc: str
    category: str
    item_quantity: Decimal
    order_count: int
    customer_count: int
    avg_qty_per_occurrence: Decimal | None


def item_summary(session: Session, item_nb: str) -> ItemDetailSummary | None:
    item = session.get(Item, item_nb)
    if item is None:
        return None
    f = OrdersFilter(item_nb=item_nb)
    order_count, line_count, item_quantity = _aggregate(
        session, _details_query(f))
    avg = (item_quantity / line_count) if line_count else None
    sub = _details_query(f).subquery()
    customer_count = session.scalar(
        select(func.count(func.distinct(sub.c.cust_nb)))) or 0
    return ItemDetailSummary(
        item_nb=item.item_number, item_desc=item.item_desc,
        category=item.category, item_quantity=item_quantity,
        order_count=order_count, customer_count=customer_count,
        avg_qty_per_occurrence=avg)


@dataclass
class DataHealth:
    total_orders: int
    orders_with_committed_at: int
    orders_with_resolvable_attribution: int
    total_order_details: int
    order_details_violating_qty_constraint: int  # always 0 - see docstring
    order_details_orphaned: int  # always 0 - FK-enforced, see docstring
    order_details_invalid_item_ref: int
    orders_with_no_lines: int
    total_customers: int
    customers_with_salesman: int
    duplicate_order_groups: int  # narrow heuristic - see docstring


def data_health(session: Session) -> DataHealth:
    """Completeness counts this service owns for the Data Health page
    (Phase 16). Two fields are always 0 by construction, not by query
    result, kept explicit so the page can say so plainly rather than
    omitting the concern:
    - order_details_violating_qty_constraint: the Phase 2 migration added a
      DB CHECK (qty > 0), validated against every existing row at
      migration time.
    - order_details_orphaned (order_details referencing a nonexistent
      order_header): order_details.__table_args__ has a real
      ForeignKeyConstraint to order_header - structurally impossible.

    order_details_invalid_item_ref (item_nb with no matching item.item_number)
    is a REAL query, not structural - there is no FK from order_details to
    item, so a bad reference (e.g. a discontinued/renamed item from the
    legacy ERP import) is genuinely possible and must be checked, not
    assumed away.

    duplicate_order_groups uses a deliberately narrow, conservative
    heuristic: orders sharing the same (cust_nb, committed_at) to-the-
    second timestamp - two genuinely independent commits landing at the
    exact same instant is implausible, and same-key retried commits are
    already prevented by commit_intent_id's unique constraint. This will
    UNDER-count real duplicates (e.g. two ERP-imported rows for the same
    sale a few seconds apart) rather than risk flagging legitimate
    back-to-back orders as duplicates - the safe direction for an honesty-
    first metric.
    """
    total_orders = session.scalar(
        select(func.count()).select_from(OrderHeader)) or 0
    with_committed_at = session.scalar(
        select(func.count()).select_from(OrderHeader)
        .where(OrderHeader.committed_at.is_not(None))) or 0

    h = CustomerOwnershipHistory
    resolvable = session.scalar(
        select(func.count()).select_from(OrderHeader)
        .where(OrderHeader.committed_at.is_not(None))
        .where(exists(select(1).where(
            h.cust_nb == OrderHeader.cust_nb,
            h.effective_from <= OrderHeader.committed_at,
            (h.effective_to.is_(None)) | (h.effective_to > OrderHeader.committed_at))))
    ) or 0

    total_details = session.scalar(
        select(func.count()).select_from(OrderDetail)) or 0

    invalid_item_ref = session.scalar(
        select(func.count()).select_from(OrderDetail)
        .where(~exists(select(1).where(
            Item.item_number == OrderDetail.item_nb)))
    ) or 0

    no_lines = session.scalar(
        select(func.count()).select_from(OrderHeader)
        .where(~exists(select(1).where(
            OrderDetail.order_nb == OrderHeader.order_nb,
            OrderDetail.order_type == OrderHeader.order_type)))
    ) or 0

    total_customers = session.scalar(
        select(func.count()).select_from(Customer)) or 0
    customers_with_salesman = session.scalar(
        select(func.count()).select_from(Customer)
        .where(Customer.salesman_id.is_not(None))) or 0

    dup_group_sub = (
        select(OrderHeader.cust_nb, OrderHeader.committed_at)
        .where(OrderHeader.committed_at.is_not(None))
        .group_by(OrderHeader.cust_nb, OrderHeader.committed_at)
        .having(func.count() > 1)
    ).subquery()
    duplicate_groups = session.scalar(
        select(func.count()).select_from(dup_group_sub)) or 0

    return DataHealth(
        total_orders=total_orders, orders_with_committed_at=with_committed_at,
        orders_with_resolvable_attribution=resolvable,
        order_details_orphaned=0,
        order_details_invalid_item_ref=invalid_item_ref,
        orders_with_no_lines=no_lines,
        total_customers=total_customers,
        customers_with_salesman=customers_with_salesman,
        duplicate_order_groups=duplicate_groups,
        total_order_details=total_details,
        order_details_violating_qty_constraint=0)
