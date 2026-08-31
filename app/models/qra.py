from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class QraHeader(Base):
    """A customer-scoped QRA (Quantity Rebate Agreement), synced in from an
    external system - never created in this app. See app/services/
    qra_engine.py for how details get applied automatically at order
    commit time."""
    __tablename__ = "qra_header"

    # Each customer has at most one QRA agreement, so cust_nb is both the
    # natural key and the FK to customer - no separate surrogate id.
    cust_nb: Mapped[str] = mapped_column(
        String(20), ForeignKey("customer.CustomerNumber"), primary_key=True)
    from_date: Mapped[date] = mapped_column(Date)
    to_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")

    details: Mapped[list["QraDetail"]] = relationship(
        back_populates="header", cascade="all, delete-orphan")


class QraDetail(Base):
    __tablename__ = "qra_detail"

    # One rule per customer - qra_type picks which one (T, B, or P).
    cust_nb: Mapped[str] = mapped_column(
        String(20), ForeignKey("qra_header.cust_nb", ondelete="CASCADE"),
        primary_key=True)
    qra_type: Mapped[str] = mapped_column(String(1))
    # Type P is a price override, not a substitution: it overrides the
    # price of item_nb_price specifically (never any other item) once that
    # item reaches qty_buy - item_nb_buy/item_nb_get/qty_get are null for
    # a P row. Types T/B still need the full buy/get pair (T for its bonus
    # item, B for both the bonus and its substitution) and leave
    # item_nb_price null.
    item_nb_buy: Mapped[str | None] = mapped_column(String(30), index=True)
    item_nb_get: Mapped[str | None] = mapped_column(String(30))
    item_nb_price: Mapped[str | None] = mapped_column(String(30))
    qty_buy: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    qty_get: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    qra_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    header: Mapped["QraHeader"] = relationship(back_populates="details")
