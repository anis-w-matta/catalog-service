"""Zero/negative order-line quantities must be rejected, not silently
summed into "item quantity" - see
vendo-intelligence-web/docs/audit/06_data_limitations.md #4 and
09_required_catalog_changes.md #4. Two independent layers: Pydantic at the
API boundary, and a DB CHECK constraint as the last line of defense.
"""
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.models import OrderDetail, OrderHeader
from app.schemas.models import LineEditIn, LineIn


class TestPydanticQtyValidation:
    def test_zero_qty_rejected(self):
        with pytest.raises(ValidationError):
            LineIn(line_nb=1, item_nb="X", qty=Decimal("0"), uom="EACH")

    def test_negative_qty_rejected(self):
        with pytest.raises(ValidationError):
            LineIn(line_nb=1, item_nb="X", qty=Decimal("-1"), uom="EACH")

    def test_positive_qty_accepted(self):
        LineIn(line_nb=1, item_nb="X", qty=Decimal("1"), uom="EACH")

    def test_unresolved_line_with_no_qty_yet_is_still_accepted(self):
        # None means "not yet resolved" - rejected downstream by
        # create_order()'s UnresolvedLines check, not by this schema.
        LineIn(line_nb=1, item_nb=None, qty=None, uom=None)

    def test_line_edit_zero_qty_rejected(self):
        with pytest.raises(ValidationError):
            LineEditIn(line_nb=1, qty=Decimal("0"))

    def test_line_edit_negative_qty_rejected(self):
        with pytest.raises(ValidationError):
            LineEditIn(line_nb=1, qty=Decimal("-2"))


class TestDbQtyConstraint:
    """Bypasses Pydantic entirely (direct ORM insert) to prove the DB
    itself, not just the API layer, refuses a bad quantity."""

    def test_zero_qty_rejected_at_db_level(self, db_session, customer):
        db_session.add(OrderHeader(order_nb="ORDQTY0", order_type="SO",
                                   cust_nb=customer.customer_number))
        db_session.flush()
        db_session.add(OrderDetail(
            order_nb="ORDQTY0", order_type="SO", line_nb=1, item_nb="X",
            item_desc="d", qty=Decimal("0"), uom="EACH"))
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_negative_qty_rejected_at_db_level(self, db_session, customer):
        db_session.add(OrderHeader(order_nb="ORDQTY1", order_type="SO",
                                   cust_nb=customer.customer_number))
        db_session.flush()
        db_session.add(OrderDetail(
            order_nb="ORDQTY1", order_type="SO", line_nb=1, item_nb="X",
            item_desc="d", qty=Decimal("-5"), uom="EACH"))
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_positive_qty_accepted_at_db_level(self, db_session, customer):
        db_session.add(OrderHeader(order_nb="ORDQTY2", order_type="SO",
                                   cust_nb=customer.customer_number))
        db_session.flush()
        db_session.add(OrderDetail(
            order_nb="ORDQTY2", order_type="SO", line_nb=1, item_nb="X",
            item_desc="d", qty=Decimal("3"), uom="EACH"))
        db_session.flush()
