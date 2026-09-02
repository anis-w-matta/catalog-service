"""ownership history, order commit date, qty check

Three independent, additive changes for trustworthy non-financial
analytics (VeNdO Intelligence phase 2 - see
vendo-intelligence-web/docs/audit/07_historical_attribution_risks.md and
09_required_catalog_changes.md for the full reasoning):

1. order_header.committed_at - a real order-date column. Added nullable
   with NO default on the ADD COLUMN step, so every existing row stays
   NULL rather than fabricating a commit time for legacy orders (the
   original order_header.created_at was dropped by migration
   5910168e3bcc and never replaced). The server-side default is attached
   afterward with a separate ALTER COLUMN, so only *new* inserts going
   forward get an automatic value - existing rows are untouched.

2. customer_ownership_history - point-in-time record of who owned a
   customer and when, so reassigning a customer (PATCH
   /customers/{cust_nb}/salesman) no longer silently rewrites which
   salesman past orders attribute to when queried
   (customer.salesman_id is a single mutable column with no history).
   Seeded with one open row per existing customer using its *current*
   salesman_id and effective_from = now() (this migration's run time) -
   this only asserts "history tracking starts here", never a claim about
   when the current owner actually took over, which is genuinely unknown
   for pre-existing data.

3. A CHECK constraint on order_details.qty (> 0) - the first CHECK
   constraint in this codebase. Nothing previously stopped a zero or
   negative quantity from being persisted and silently summed into
   "item quantity". No code path was found anywhere that intentionally
   produces a non-positive order-line quantity (returns are their own
   order_type, not negative quantities on a sale line). If this step
   fails against real data, that means violating rows already exist and
   need a manual decision before this migration can proceed - do not
   weaken the constraint to make it pass silently.

Revision ID: 68d68fb195f5
Revises: 36869bd395d1
Create Date: 2026-09-01 11:23:48.846953

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '68d68fb195f5'
down_revision: Union[str, None] = '36869bd395d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("order_header", sa.Column(
        "committed_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("order_header", "committed_at",
                    server_default=sa.text("now()"))

    op.create_table(
        "customer_ownership_history",
        sa.Column("id", sa.BigInteger(), primary_key=True,
                 autoincrement=True),
        sa.Column("cust_nb", sa.String(20),
                 sa.ForeignKey("customer.CustomerNumber"), nullable=False),
        sa.Column("salesman_id", sa.String(50), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True),
                 nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True),
                 nullable=True),
    )
    op.create_index("ix_customer_ownership_history_cust_nb_effective_from",
                    "customer_ownership_history",
                    ["cust_nb", "effective_from"])

    op.execute("""
        INSERT INTO customer_ownership_history
            (cust_nb, salesman_id, effective_from, effective_to)
        SELECT "CustomerNumber", salesman_id, now(), NULL
        FROM customer
    """)

    op.create_check_constraint(
        "ck_order_details_qty_positive", "order_details", "qty > 0")


def downgrade() -> None:
    op.drop_constraint(
        "ck_order_details_qty_positive", "order_details", type_="check")

    op.drop_index("ix_customer_ownership_history_cust_nb_effective_from",
                  table_name="customer_ownership_history")
    op.drop_table("customer_ownership_history")

    op.alter_column("order_header", "committed_at", server_default=None)
    op.drop_column("order_header", "committed_at")
