"""initial schema: item, customer, order_header, order_details,
qra_header, qra_detail, order_nb_seq

Schema matches the backend's models as of the catalog-service split (see
vendo-app/backend's own migration history for how these tables evolved
before the split - status/created_at/unit_price/category/qra_detail_id
etc. were already dropped there before this service existed, so this
starts straight from the final shape, not from the backend's full
history).

Revision ID: 3aa07b9dbc40
Revises:
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3aa07b9dbc40'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "item",
        sa.Column("item_number", sa.String(30), primary_key=True),
        sa.Column("item_desc", sa.String(300), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
    )
    op.create_index("ix_item_category", "item", ["category"])
    op.execute("CREATE INDEX idx_item_desc_trgm ON item "
              "USING gin (item_desc gin_trgm_ops)")

    op.create_table(
        "customer",
        sa.Column("CustomerNumber", sa.String(20), primary_key=True),
        sa.Column("CustomerName", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("telephone", sa.String(50), nullable=True),
        sa.Column("City", sa.String(100), nullable=True),
        sa.Column("Address1", sa.String(200), nullable=True),
        sa.Column("Address2", sa.String(200), nullable=True),
    )

    op.execute("CREATE SEQUENCE IF NOT EXISTS order_nb_seq START 1")

    op.create_table(
        "order_header",
        sa.Column("order_nb", sa.String(30), primary_key=True),
        sa.Column("order_type", sa.String(10), primary_key=True),
        sa.Column("cust_nb", sa.String(20), nullable=False),
        sa.Column("commit_intent_id", sa.String(36), nullable=True),
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


def downgrade() -> None:
    op.drop_table("qra_detail")
    op.drop_table("qra_header")
    op.drop_table("order_details")
    op.drop_table("order_header")
    op.execute("DROP SEQUENCE IF EXISTS order_nb_seq")
    op.drop_table("customer")
    op.execute("DROP INDEX IF EXISTS idx_item_desc_trgm")
    op.drop_table("item")
