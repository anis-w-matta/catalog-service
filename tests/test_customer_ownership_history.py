"""Reassigning a customer must not rewrite who owned it in the past - see
vendo-intelligence-web/docs/audit/07_historical_attribution_risks.md for
the exact bug this closes.
"""
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import CustomerOwnershipHistory
from app.services.customer_ownership import get_owner_at, record_ownership_change


def _open_row(db_session, cust_nb):
    return db_session.scalars(
        select(CustomerOwnershipHistory)
        .where(CustomerOwnershipHistory.cust_nb == cust_nb,
              CustomerOwnershipHistory.effective_to.is_(None))
    ).one()


def test_reassignment_preserves_history(db_session, customer):
    unknown_past = datetime.now(timezone.utc) - timedelta(days=1)

    record_ownership_change(db_session, customer.customer_number,
                            "salesman_a")
    db_session.flush()
    during_a_at = _open_row(db_session, customer.customer_number).effective_from

    # Real separation, not just "the next line of code" - two datetime.now()
    # calls close enough together can land in the same clock tick, which
    # would make row A's effective_to and row B's effective_from compare
    # equal and collapse the two windows' boundary.
    time.sleep(0.05)

    record_ownership_change(db_session, customer.customer_number,
                            "salesman_b")
    db_session.flush()
    during_b_at = _open_row(db_session, customer.customer_number).effective_from

    before_any_assignment = get_owner_at(db_session,
                                         customer.customer_number,
                                         unknown_past)
    assert before_any_assignment.found is False

    during_a = get_owner_at(db_session, customer.customer_number,
                            during_a_at)
    assert during_a.found is True
    assert during_a.salesman_id == "salesman_a"

    during_b = get_owner_at(db_session, customer.customer_number,
                            during_b_at)
    assert during_b.found is True
    assert during_b.salesman_id == "salesman_b"

    # The actual regression this table exists to prevent: asking "who owned
    # this customer during A's window" AFTER a later reassignment to B must
    # still say A, not silently flip to B just because B owns the customer
    # now.
    still_a_after_the_fact = get_owner_at(db_session,
                                          customer.customer_number,
                                          during_a_at)
    assert still_a_after_the_fact.salesman_id == "salesman_a"


def test_unassign_records_null_salesman_not_a_missing_row(
        db_session, customer):
    record_ownership_change(db_session, customer.customer_number,
                            "salesman_a")
    db_session.flush()
    record_ownership_change(db_session, customer.customer_number, None)
    db_session.flush()

    current = get_owner_at(db_session, customer.customer_number,
                           _open_row(db_session,
                                    customer.customer_number).effective_from)
    # found=True (we know the answer) with salesman_id=None (the answer is
    # "nobody") - distinct from found=False (we don't know).
    assert current.found is True
    assert current.salesman_id is None


def test_unknown_before_tracking_started_is_distinguishable_from_unassigned(
        db_session, customer):
    """A customer with no ownership_history rows at all (e.g. a timestamp
    before this feature existed) must report found=False, never silently
    collapse into "unassigned" (found=True, salesman_id=None) - those mean
    different things: one is a known fact, the other is missing data."""
    result = get_owner_at(db_session, customer.customer_number,
                          datetime.now(timezone.utc))
    assert result.found is False
    assert result.salesman_id is None
