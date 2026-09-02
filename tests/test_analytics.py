"""VeNdO Intelligence Phase 3 aggregation queries - see
app/services/analytics.py. Every test here works in terms of the same
canonical definitions as test_order_metrics.py: Orders != Order Lines !=
Item Quantity, and salesman attribution must reflect who owned the
customer AT COMMIT TIME, never who owns it now.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import OrderDetail, OrderHeader
from app.services import analytics


def _order(db_session, order_nb, cust_nb, *, committed_at="default",
          order_type="SO", lines=((1, "ITEM1", "5", "EACH"),)):
    kwargs = {}
    if committed_at != "default":
        kwargs["committed_at"] = committed_at
    db_session.add(OrderHeader(order_nb=order_nb, order_type=order_type,
                               cust_nb=cust_nb, **kwargs))
    db_session.flush()
    for line_nb, item_nb, qty, uom in lines:
        db_session.add(OrderDetail(
            order_nb=order_nb, order_type=order_type, line_nb=line_nb,
            item_nb=item_nb, item_desc="desc", qty=Decimal(qty), uom=uom))
    db_session.flush()


class TestOrdersSummary:
    def test_worked_example(self, db_session, customer):
        _order(db_session, "A0001", customer.customer_number,
              lines=[(1, "I1", "10", "EACH"), (2, "I2", "20", "EACH"),
                    (3, "I3", "5", "EACH")])
        r = analytics.orders_summary(
            db_session, analytics.OrdersFilter(cust_nb=customer.customer_number))
        assert r.order_count == 1
        assert r.order_line_count == 3
        assert r.item_quantity == Decimal("35")
        assert r.avg_items_per_order == Decimal("35")

    def test_date_filter_excludes_and_counts_missing_commit_date(
            self, db_session, customer):
        _order(db_session, "A0002", customer.customer_number,
              committed_at=None)
        r = analytics.orders_summary(
            db_session, analytics.OrdersFilter(
                cust_nb=customer.customer_number,
                date_from=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        assert r.order_count == 0
        assert r.orders_excluded_missing_commit_date == 1

    def test_no_date_filter_does_not_report_exclusions(
            self, db_session, customer):
        _order(db_session, "A0003", customer.customer_number,
              committed_at=None)
        r = analytics.orders_summary(
            db_session,
            analytics.OrdersFilter(cust_nb=customer.customer_number))
        # No date/salesman filter active - nothing was "excluded", the
        # order legitimately has no known date and that's just absent
        # from a date-scoped view, not a fact this call is asserting.
        assert r.orders_excluded_missing_commit_date == 0


class TestItemsPerOrderHistogram:
    def test_buckets_by_total_order_quantity(self, db_session, customer):
        _order(db_session, "H0001", customer.customer_number,
              lines=[(1, "I1", "1", "EACH")])  # total qty 1 -> "0-1"
        _order(db_session, "H0002", customer.customer_number,
              lines=[(1, "I1", "3", "EACH")])  # total qty 3 -> "1-5"

        rows = analytics.items_per_order_histogram(
            db_session, analytics.OrdersFilter(cust_nb=customer.customer_number))
        by_bucket = {r.bucket: r.order_count for r in rows}
        assert by_bucket["0-1"] == 1
        assert by_bucket["1-5"] == 1


class TestSalesmenOrderMetrics:
    def test_attributes_by_ownership_at_commit_time_not_current_owner(
            self, db_session, customer):
        """An order committed while A owned the customer must attribute to
        A forever, even after a later reassignment to B - the exact bug
        vendo-intelligence-web/docs/audit/07_historical_attribution_risks.md
        describes. Ownership rows are built directly (not via
        record_ownership_change) so both periods' boundaries are exact and
        deterministic rather than depending on wall-clock timing."""
        from app.models import CustomerOwnershipHistory

        t_a = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t_reassign = datetime(2026, 6, 1, tzinfo=timezone.utc)
        db_session.add_all([
            CustomerOwnershipHistory(
                cust_nb=customer.customer_number, salesman_id="sm_a",
                effective_from=t_a, effective_to=t_reassign),
            CustomerOwnershipHistory(
                cust_nb=customer.customer_number, salesman_id="sm_b",
                effective_from=t_reassign, effective_to=None),
        ])
        db_session.flush()

        _order(db_session, "A0010", customer.customer_number,
              committed_at=t_a + timedelta(days=1),
              lines=[(1, "I1", "3", "EACH")])
        _order(db_session, "A0011", customer.customer_number,
              committed_at=t_reassign + timedelta(days=1),
              lines=[(1, "I1", "7", "EACH")])

        r = analytics.salesmen_order_metrics(
            db_session,
            analytics.OrdersFilter(cust_nb=customer.customer_number))
        by_salesman = {row.salesman_id: row for row in r.by_salesman}

        assert by_salesman["sm_a"].order_count == 1
        assert by_salesman["sm_a"].item_quantity == Decimal("3")
        assert by_salesman["sm_b"].order_count == 1
        assert by_salesman["sm_b"].item_quantity == Decimal("7")


class TestRankings:
    def test_top_customers_two_separate_rankings(self, db_session):
        # Unfiltered rankings would also see whatever else already lives in
        # this shared test schema, so scope every assertion to a unique
        # item_nb these two orders alone use (item_nb is a real filter
        # top_customers() already supports for exactly this reason).
        from app.models import Customer
        c1 = Customer(customer_number="RC1", customer_name="Few Big Orders")
        c2 = Customer(customer_number="RC2", customer_name="Many Small Orders")
        db_session.add_all([c1, c2])
        db_session.flush()
        _order(db_session, "R0001", "RC1", lines=[(1, "RANKITEM", "100", "EACH")])
        _order(db_session, "R0002", "RC2", lines=[(1, "RANKITEM", "1", "EACH")])
        _order(db_session, "R0003", "RC2", lines=[(1, "RANKITEM", "1", "EACH")])

        f = analytics.OrdersFilter(item_nb="RANKITEM")
        by_qty = analytics.top_customers(db_session, f, "item_quantity", 10)
        by_orders = analytics.top_customers(db_session, f, "order_count", 10)

        assert by_qty[0].cust_nb == "RC1"  # highest quantity
        assert by_orders[0].cust_nb == "RC2"  # highest order count

    def test_top_items_two_separate_rankings(self, db_session, customer):
        from app.models import Item
        db_session.add_all([
            Item(item_number="BIGQTY", item_desc="d", category="c"),
            Item(item_number="FREQ", item_desc="d", category="c"),
        ])
        db_session.flush()
        _order(db_session, "R0010", customer.customer_number,
              lines=[(1, "BIGQTY", "50", "EACH")])
        _order(db_session, "R0011", customer.customer_number,
              lines=[(1, "FREQ", "1", "EACH")])
        _order(db_session, "R0012", customer.customer_number,
              lines=[(1, "FREQ", "1", "EACH")])

        # Scoped to this customer alone, same reasoning as above.
        f = analytics.OrdersFilter(cust_nb=customer.customer_number)
        by_qty = analytics.top_items(db_session, f, "quantity", 10)
        by_freq = analytics.top_items(db_session, f, "order_frequency", 10)

        assert by_qty[0].item_nb == "BIGQTY"
        assert by_freq[0].item_nb == "FREQ"

    def test_customer_penetration_counts_distinct_customers_not_orders(
            self, db_session):
        """Phase 9 'customer penetration' - two customers ordering the
        same item twice each must count as 2, not 4."""
        from app.models import Customer, Item
        db_session.add_all([
            Customer(customer_number="PEN1", customer_name="a"),
            Customer(customer_number="PEN2", customer_name="b"),
            Item(item_number="PENITEM", item_desc="d", category="c"),
        ])
        db_session.flush()
        _order(db_session, "P0001", "PEN1", lines=[(1, "PENITEM", "1", "EACH")])
        _order(db_session, "P0002", "PEN1", lines=[(1, "PENITEM", "1", "EACH")])
        _order(db_session, "P0003", "PEN2", lines=[(1, "PENITEM", "1", "EACH")])

        f = analytics.OrdersFilter(item_nb="PENITEM")
        rows = analytics.top_items(db_session, f, "quantity", 10)
        assert rows[0].order_count == 3
        assert rows[0].customer_count == 2

        item = analytics.item_summary(db_session, "PENITEM")
        assert item.customer_count == 2

        categories = analytics.categories_summary(db_session, f)
        assert categories[0].customer_count == 2


class TestOrdersTrend:
    def test_buckets_by_month(self, db_session, customer):
        _order(db_session, "T0001", customer.customer_number,
              committed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
              lines=[(1, "I1", "10", "EACH")])
        _order(db_session, "T0002", customer.customer_number,
              committed_at=datetime(2026, 2, 3, tzinfo=timezone.utc),
              lines=[(1, "I1", "4", "EACH")])
        r = analytics.orders_trend(
            db_session, analytics.OrdersFilter(cust_nb=customer.customer_number))
        by_bucket = {p.bucket: p for p in r.points}
        assert by_bucket["2026-01"].order_count == 1
        assert by_bucket["2026-01"].item_quantity == Decimal("10")
        assert by_bucket["2026-02"].item_quantity == Decimal("4")

    def test_day_granularity_for_anomaly_baselines(self, db_session, customer):
        _order(db_session, "T0004", customer.customer_number,
              committed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
              lines=[(1, "I1", "10", "EACH")])
        _order(db_session, "T0005", customer.customer_number,
              committed_at=datetime(2026, 1, 16, tzinfo=timezone.utc),
              lines=[(1, "I1", "4", "EACH")])
        r = analytics.orders_trend(
            db_session, analytics.OrdersFilter(cust_nb=customer.customer_number),
            granularity="day")
        by_bucket = {p.bucket: p for p in r.points}
        assert by_bucket["2026-01-15"].item_quantity == Decimal("10")
        assert by_bucket["2026-01-16"].item_quantity == Decimal("4")

    def test_day_bucket_boundary_is_utc_not_session_timezone(
            self, db_session, customer):
        """Day boundaries in UTC, explicitly (see the docstring on
        analytics.orders_trend) - this deployment's Postgres session
        defaults to Europe/Chisinau (currently UTC+3), so
        date_trunc('day', a timestamptz) without a UTC() wrap would shift
        an order committed late on a UTC day into the next local day's
        bucket instead. These two commits are 30 minutes apart in UTC
        (straddling the UTC day boundary) but >1.5 hours apart in
        Chisinau local time."""
        _order(db_session, "T0006", customer.customer_number,
              committed_at=datetime(2026, 1, 15, 23, 45, tzinfo=timezone.utc),
              lines=[(1, "I1", "10", "EACH")])
        _order(db_session, "T0007", customer.customer_number,
              committed_at=datetime(2026, 1, 16, 0, 15, tzinfo=timezone.utc),
              lines=[(1, "I1", "4", "EACH")])
        r = analytics.orders_trend(
            db_session, analytics.OrdersFilter(cust_nb=customer.customer_number),
            granularity="day")
        by_bucket = {p.bucket: p for p in r.points}
        assert by_bucket["2026-01-15"].item_quantity == Decimal("10")
        assert by_bucket["2026-01-16"].item_quantity == Decimal("4")

    def test_rejects_unknown_granularity(self, db_session, customer):
        with pytest.raises(ValueError):
            analytics.orders_trend(
                db_session, analytics.OrdersFilter(cust_nb=customer.customer_number),
                granularity="week")

    def test_excludes_orders_with_no_commit_date(self, db_session, customer):
        _order(db_session, "T0003", customer.customer_number, committed_at=None)
        r = analytics.orders_trend(
            db_session, analytics.OrdersFilter(
                cust_nb=customer.customer_number,
                date_from=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        assert r.points == []
        assert r.orders_excluded_missing_commit_date == 1

    def test_reports_exclusions_even_with_no_date_or_salesman_filter(
            self, db_session, customer):
        """Unlike orders_summary (see
        test_no_date_filter_does_not_report_exclusions above), every point
        on a trend structurally requires committed_at - so an unfiltered
        call must still report exclusions, not silently show an empty,
        falsely-COMPLETE trend. This was a real bug: orders_trend
        originally reused _excluded_missing_commit_date, which returns 0
        whenever no date/salesman filter is active."""
        _order(db_session, "T0004", customer.customer_number, committed_at=None)
        r = analytics.orders_trend(
            db_session, analytics.OrdersFilter(cust_nb=customer.customer_number))
        assert r.points == []
        assert r.orders_excluded_missing_commit_date == 1

    def test_attributes_by_ownership_at_commit_time(self, db_session, customer):
        from app.models import CustomerOwnershipHistory

        t_a = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t_reassign = datetime(2026, 6, 1, tzinfo=timezone.utc)
        db_session.add_all([
            CustomerOwnershipHistory(
                cust_nb=customer.customer_number, salesman_id="trend_a",
                effective_from=t_a, effective_to=t_reassign),
            CustomerOwnershipHistory(
                cust_nb=customer.customer_number, salesman_id="trend_b",
                effective_from=t_reassign, effective_to=None),
        ])
        db_session.flush()
        _order(db_session, "T0010", customer.customer_number,
              committed_at=t_a + timedelta(days=1), lines=[(1, "I1", "3", "EACH")])
        _order(db_session, "T0011", customer.customer_number,
              committed_at=t_reassign + timedelta(days=1), lines=[(1, "I1", "7", "EACH")])

        r_a = analytics.orders_trend(
            db_session, analytics.OrdersFilter(
                cust_nb=customer.customer_number, salesman_id="trend_a"))
        r_b = analytics.orders_trend(
            db_session, analytics.OrdersFilter(
                cust_nb=customer.customer_number, salesman_id="trend_b"))
        assert sum(p.order_count for p in r_a.points) == 1
        assert sum(p.order_count for p in r_b.points) == 1


class TestCustomerOrderHistory:
    def test_ordered_oldest_first_excludes_missing_commit_date(
            self, db_session, customer):
        _order(db_session, "H0100", customer.customer_number,
              committed_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
              lines=[(1, "I1", "5", "EACH")])
        _order(db_session, "H0099", customer.customer_number,
              committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
              lines=[(1, "I1", "2", "EACH"), (2, "I2", "3", "EACH")])
        _order(db_session, "H0101", customer.customer_number, committed_at=None)

        rows = analytics.customer_order_history(db_session, customer.customer_number)
        assert [r.order_nb for r in rows] == ["H0099", "H0100"]
        assert rows[0].item_quantity == Decimal("5")
        assert rows[0].order_line_count == 2


class TestCustomersSummary:
    def test_counts_assigned_and_unassigned(self, db_session):
        from app.models import Customer
        db_session.add_all([
            Customer(customer_number="CS1", customer_name="a",
                    salesman_id="sm_x"),
            Customer(customer_number="CS2", customer_name="b",
                    salesman_id=None),
        ])
        db_session.flush()
        r = analytics.customers_summary(db_session)
        assert r.assigned >= 1
        assert r.unassigned >= 1
        assert r.total == r.assigned + r.unassigned


class TestDataHealth:
    def test_qty_constraint_violations_always_zero(self, db_session):
        # Structural guarantee, not a query finding - see the docstring on
        # analytics.data_health. Nothing to set up; just confirm the field
        # is present and zero.
        r = analytics.data_health(db_session)
        assert r.order_details_violating_qty_constraint == 0

    def test_committed_at_completeness_reflects_missing_dates(
            self, db_session, customer):
        _order(db_session, "DH0001", customer.customer_number,
              committed_at=None)
        r = analytics.data_health(db_session)
        assert r.total_orders >= 1
        assert r.orders_with_committed_at <= r.total_orders

    def test_orphaned_details_always_zero(self, db_session):
        # FK-enforced, not a query finding - see the docstring.
        r = analytics.data_health(db_session)
        assert r.order_details_orphaned == 0

    def test_detects_invalid_item_reference(self, db_session, customer):
        from app.models import OrderDetail as OD, OrderHeader as OH
        db_session.add(OH(order_nb="DH0002", order_type="SO",
                          cust_nb=customer.customer_number))
        db_session.flush()
        db_session.add(OD(order_nb="DH0002", order_type="SO", line_nb=1,
                          item_nb="NO-SUCH-ITEM", item_desc="d",
                          qty=Decimal("1"), uom="EACH"))
        db_session.flush()
        r = analytics.data_health(db_session)
        assert r.order_details_invalid_item_ref >= 1

    def test_detects_orders_with_no_lines(self, db_session, customer):
        from app.models import OrderHeader as OH
        db_session.add(OH(order_nb="DH0003", order_type="SO",
                          cust_nb=customer.customer_number))
        db_session.flush()
        r = analytics.data_health(db_session)
        assert r.orders_with_no_lines >= 1

    def test_customers_with_salesman_completeness(self, db_session):
        from app.models import Customer
        db_session.add_all([
            Customer(customer_number="DHC1", customer_name="a", salesman_id="sm_x"),
            Customer(customer_number="DHC2", customer_name="b", salesman_id=None),
        ])
        db_session.flush()
        r = analytics.data_health(db_session)
        assert r.customers_with_salesman >= 1
        assert r.customers_with_salesman <= r.total_customers

    def test_duplicate_order_groups_narrow_heuristic(self, db_session, customer):
        t = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _order(db_session, "DH0004", customer.customer_number, committed_at=t)
        _order(db_session, "DH0005", customer.customer_number, committed_at=t)
        r = analytics.data_health(db_session)
        assert r.duplicate_order_groups >= 1
