from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CustomerOwnershipHistory(Base):
    """Point-in-time record of which salesman owned a customer, so
    reassigning a customer (PATCH /customers/{cust_nb}/salesman) does not
    silently rewrite which salesman past orders attribute to.

    customer.salesman_id is the current owner only. This table is the
    append-only log behind it: closing the previously-open row
    (effective_to = now()) and opening a new one is done together, in the
    same transaction as the customer.salesman_id write, by
    app.services.customer_ownership.record_ownership_change(). A row with
    effective_to = NULL is the currently-open period for that customer.
    """

    __tablename__ = "customer_ownership_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Indexed together with effective_from below (composite index, see the
    # migration) rather than on its own - every real lookup filters by both.
    cust_nb: Mapped[str] = mapped_column(
        String(20), ForeignKey("customer.CustomerNumber"))
    # NULL means "unassigned" during this period - same meaning as a NULL
    # customer.salesman_id.
    salesman_id: Mapped[str | None] = mapped_column(String(50))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    __table_args__ = (
        Index("ix_customer_ownership_history_cust_nb_effective_from",
              "cust_nb", "effective_from"),
    )
