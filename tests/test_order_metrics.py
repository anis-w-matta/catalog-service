"""Canonical order/line/quantity metric definitions - see
vendo-intelligence-web/docs/audit/02_database_map.md's "Canonical metric
definitions" and the master prompt's own worked example.
"""
from decimal import Decimal

from sqlalchemy import func, select

from app.models import OrderDetail, OrderHeader


def test_master_prompt_worked_example(db_session, customer):
    """1 order, 3 lines, quantities 10/20/5 -> Orders=1, Lines=3,
    Quantity=35. Orders != Order Lines != Item Quantity."""
    db_session.add(OrderHeader(order_nb="ORD0001", order_type="SO",
                               cust_nb=customer.customer_number))
    db_session.flush()
    for line_nb, qty in enumerate([10, 20, 5], start=1):
        db_session.add(OrderDetail(
            order_nb="ORD0001", order_type="SO", line_nb=line_nb,
            item_nb="ITEM1", item_desc="desc", qty=Decimal(qty), uom="EACH"))
    db_session.flush()

    order_count = db_session.execute(
        select(func.count(func.distinct(
            func.concat(OrderHeader.order_nb, "|", OrderHeader.order_type))))
        .where(OrderHeader.order_nb == "ORD0001")
    ).scalar_one()
    line_count = db_session.execute(
        select(func.count()).select_from(OrderDetail)
        .where(OrderDetail.order_nb == "ORD0001")
    ).scalar_one()
    item_quantity = db_session.execute(
        select(func.sum(OrderDetail.qty))
        .where(OrderDetail.order_nb == "ORD0001")
    ).scalar_one()

    assert order_count == 1
    assert line_count == 3
    assert item_quantity == Decimal("35")


def test_composite_order_identity_distinguishes_same_order_nb_different_type(
        db_session, customer):
    """order_nb alone is not the identity - (order_nb, order_type) is. An SO
    and its RETURN can legitimately share an order_nb (orders.py reuses the
    target order's number for a RETURN) and must still count as two orders,
    not one."""
    db_session.add(OrderHeader(order_nb="ORD0002", order_type="SO",
                               cust_nb=customer.customer_number))
    db_session.add(OrderHeader(order_nb="ORD0002", order_type="RETURN",
                               cust_nb=customer.customer_number))
    db_session.flush()

    count = db_session.execute(
        select(func.count()).select_from(OrderHeader)
        .where(OrderHeader.order_nb == "ORD0002")
    ).scalar_one()
    assert count == 2
