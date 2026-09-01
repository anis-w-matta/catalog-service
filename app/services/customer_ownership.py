from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CustomerOwnershipHistory


def record_ownership_change(session: Session, cust_nb: str,
                            new_salesman_id: str | None) -> None:
    """Close the currently-open ownership period for cust_nb (if any) and
    open a new one for new_salesman_id. Must be called in the same
    transaction as the Customer.salesman_id write it accompanies (see
    app.api.customers.assign_salesman) - the two must never disagree about
    who currently owns the customer.
    """
    now = datetime.now(timezone.utc)
    current = session.scalars(
        select(CustomerOwnershipHistory)
        .where(CustomerOwnershipHistory.cust_nb == cust_nb,
              CustomerOwnershipHistory.effective_to.is_(None))
    ).one_or_none()
    if current is not None:
        current.effective_to = now
    session.add(CustomerOwnershipHistory(
        cust_nb=cust_nb, salesman_id=new_salesman_id,
        effective_from=now, effective_to=None))


class OwnershipAt(NamedTuple):
    # False when no history row covers `at` at all (e.g. a timestamp
    # before ownership tracking started for this customer) - distinct from
    # `found=True, salesman_id=None`, which means the customer was
    # genuinely unassigned at that moment. Callers must not collapse these
    # into the same "unknown" bucket without saying so - "unknown must
    # remain unknown" applies to the tracking gap itself, not just to the
    # unassigned case.
    found: bool
    salesman_id: str | None


def get_owner_at(session: Session, cust_nb: str, at: datetime) -> OwnershipAt:
    """Point-in-time lookup: who owned cust_nb at timestamp `at`?"""
    row = session.scalars(
        select(CustomerOwnershipHistory)
        .where(CustomerOwnershipHistory.cust_nb == cust_nb,
              CustomerOwnershipHistory.effective_from <= at,
              or_(CustomerOwnershipHistory.effective_to.is_(None),
                  CustomerOwnershipHistory.effective_to > at))
    ).one_or_none()
    if row is None:
        return OwnershipAt(found=False, salesman_id=None)
    return OwnershipAt(found=True, salesman_id=row.salesman_id)
