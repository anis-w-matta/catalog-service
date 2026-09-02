"""SQL Server baseline - fresh schema for the Postgres -> SQL Server 2025
migration, replacing the 3 Postgres-targeted migrations previously in this
directory (moved to alembic/versions_postgres_legacy/ for reference, not
deleted). Reflects the CURRENT final shape of app/models/ directly - the
same "start from the final shape" approach the original initial_schema
migration already used for the same reason.

Not carried forward from the old migrations:

- `CREATE EXTENSION pg_trgm` + the trigram GIN index on item.item_desc -
  item_resolver.py no longer does a DB-level trigram query (see that
  file's resolve()); fuzzy scoring is RapidFuzz-only now, so there's
  nothing here for an index to speed up.
- The old ownership-history backfill (`INSERT INTO
  customer_ownership_history SELECT ... FROM customer`) - that seeded
  history for customers that already existed *in Postgres* at the time it
  ran. Real history data now exists in Postgres and gets carried over by
  the data cutover (copying customer_ownership_history rows directly,
  same as every other table), not reconstructed here against an empty
  table.
- order_header.committed_at's original two-step add-then-backfill-null
  dance - that existed to avoid fabricating a value for rows that already
  existed in Postgres at that migration's run time. Not relevant to a
  fresh table; the server default is attached at creation directly.

order_nb_seq is a real SQL Server SEQUENCE here (unlike the backend's own
copy of this migration, which drops it as confirmed-dead code) - this is
the one order-numbering sequence actually read by live code
(app/services/numbering.py, called from orders.py's create_order()).

This is a genuinely new baseline: run against an empty SQL Server database/
schema only. It has not been executed against a live SQL Server instance
(none was reachable in the environment this was written in) - review the
DDL and run `alembic upgrade head` against a real target before trusting
it.

Revision ID: b4d8e6a1c3f7
Revises:
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4d8e6a1c3f7'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item",
        sa.Column("item_number", sa.String(30), primary_key=True),
        sa.Column("item_desc", sa.String(300), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
    )
    op.create_index("ix_item_category", "item", ["category"])

    op.create_table(
        "customer",
        sa.Column("CustomerNumber", sa.String(20), primary_key=True),
        sa.Column("CustomerName", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("telephone", sa.String(50), nullable=True),
        sa.Column("City", sa.String(100), nullable=True),
        sa.Column("Address1", sa.String(200), nullable=True),
        sa.Column("Address2", sa.String(200), nullable=True),
        # The backend's Salesman.login_id this customer is assigned to -
        # null until an admin assigns one. No FK: Salesman lives in the
        # backend service's own schema/service boundary.
        sa.Column("salesman_id", sa.String(50), nullable=True),
    )
    op.create_index("ix_customer_salesman_id", "customer", ["salesman_id"])

    op.execute("CREATE SEQUENCE order_nb_seq START WITH 1 INCREMENT BY 1")

    op.create_table(
        "order_header",
        sa.Column("order_nb", sa.String(30), primary_key=True),
        sa.Column("order_type", sa.String(10), primary_key=True),
        sa.Column("cust_nb", sa.String(20), nullable=False),
        sa.Column("commit_intent_id", sa.String(36), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True),
                 server_default=sa.text("SYSUTCDATETIME()")),
    )
    op.create_index("ix_order_header_cust_nb", "order_header", ["cust_nb"])
    op.create_index("ix_order_header_commit_intent_id", "order_header",
                    ["commit_intent_id"], unique=True)

    op.create_table(
        "order_details",
        sa.Column("order_nb", sa.String(30), primary_key=True),
        sa.Column("order_type", sa.String(10), primary_key=True),
        sa.Column("line_nb", sa.Integer, primary_key=True),
        sa.Column("item_nb", sa.String(30), nullable=False),
        sa.Column("item_desc", sa.String(300), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("uom", sa.String(20), nullable=False),
        sa.Column("line_type", sa.String(1), server_default="S"),
        sa.Column("is_free", sa.Boolean, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ["order_nb", "order_type"],
            ["order_header.order_nb", "order_header.order_type"]),
        sa.CheckConstraint("qty > 0", name="ck_order_details_qty_positive"),
    )

    op.create_table(
        "qra_header",
        sa.Column("cust_nb", sa.String(20),
                 sa.ForeignKey("customer.CustomerNumber"), primary_key=True),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), server_default="active"),
    )

    op.create_table(
        "qra_detail",
        sa.Column("cust_nb", sa.String(20),
                 sa.ForeignKey("qra_header.cust_nb", ondelete="CASCADE"),
                 primary_key=True),
        sa.Column("qra_type", sa.String(1), nullable=False),
        sa.Column("item_nb_buy", sa.String(30), nullable=True),
        sa.Column("item_nb_get", sa.String(30), nullable=True),
        sa.Column("item_nb_price", sa.String(30), nullable=True),
        sa.Column("qty_buy", sa.Numeric(12, 3), nullable=False),
        sa.Column("qty_get", sa.Numeric(12, 3), nullable=True),
        sa.Column("qra_price", sa.Numeric(12, 2), nullable=True),
    )
    op.create_index("ix_qra_detail_item_nb_buy", "qra_detail", ["item_nb_buy"])

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


def downgrade() -> None:
    op.drop_table("customer_ownership_history")
    op.drop_table("qra_detail")
    op.drop_table("qra_header")
    op.drop_table("order_details")
    op.drop_table("order_header")
    op.execute("DROP SEQUENCE order_nb_seq")
    op.drop_table("customer")
    op.drop_table("item")
