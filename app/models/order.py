from datetime import datetime
from decimal import Decimal

from sqlalchemy import (Boolean, CheckConstraint, DateTime,
                        ForeignKeyConstraint, Numeric, String)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class OrderHeader(Base):
    __tablename__ = "order_header"

    order_nb: Mapped[str] = mapped_column(String(30), primary_key=True)
    order_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    cust_nb: Mapped[str] = mapped_column(String(20), index=True)
    # The backend's idempotency key for POST /orders (see app/api/orders.py)
    # - a retried commit (network timeout, worker reconciliation sweep after
    # a crash) sends the same commit_intent_id and gets the same order back
    # instead of a duplicate. Unique/nullable: only orders created through
    # the commit saga have one; nothing else does.
    commit_intent_id: Mapped[str | None] = mapped_column(
        String(36), unique=True, index=True)
    # When this order was actually committed - NULL for every order that
    # existed before this column was added (no fabricated backfill; the
    # original order_header.created_at was dropped by an earlier migration
    # and this is not a resurrection of it, just a fresh start). Set
    # automatically by the DB default on insert, never by application code.
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    lines: Mapped[list["OrderDetail"]] = relationship(
        back_populates="header", cascade="all, delete-orphan",
        order_by="OrderDetail.line_nb")


class OrderDetail(Base):
    __tablename__ = "order_details"

    order_nb: Mapped[str] = mapped_column(String(30), primary_key=True)
    order_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    line_nb: Mapped[int] = mapped_column(primary_key=True)
    item_nb: Mapped[str] = mapped_column(String(30))
    item_desc: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    uom: Mapped[str] = mapped_column(String(20))
    # Always "S" today - every line this app produces is a standard sale
    # line. Kept as a real column (not hardcoded downstream) since the
    # wider ERP this integrates with defines other line types.
    line_type: Mapped[str] = mapped_column(String(1), default="S")
    # True only for a bonus line the QRA engine added at commit time -
    # never set by anything else.
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)

    header: Mapped["OrderHeader"] = relationship(back_populates="lines")

    __table_args__ = (
        ForeignKeyConstraint(
            ["order_nb", "order_type"],
            ["order_header.order_nb", "order_header.order_type"]),
        # Nothing legitimately produces a zero/negative order-line quantity
        # today (returns are their own order_type, not negative quantities
        # on a sale line) - enforced at the DB level so "item quantity" can
        # never silently include a bad row.
        CheckConstraint("qty > 0", name="ck_order_details_qty_positive"),
    )
