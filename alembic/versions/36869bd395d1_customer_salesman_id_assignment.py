"""customer.salesman_id assignment

Nullable, no default: every existing customer row starts unassigned rather
than fabricating an owner for real ERP-imported data (~40k rows) with no
existing source of truth for who sells to whom. An unassigned customer is
admin-only (see app/services/orders.py's ownership check) until an admin
explicitly assigns one via PATCH /customers/{cust_nb}/salesman.

Revision ID: 36869bd395d1
Revises: 3aa07b9dbc40
Create Date: 2026-08-31 06:38:55.058000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36869bd395d1'
down_revision: Union[str, None] = '3aa07b9dbc40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("customer", sa.Column(
        "salesman_id", sa.String(50), nullable=True))
    op.create_index("ix_customer_salesman_id", "customer", ["salesman_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_salesman_id", table_name="customer")
    op.drop_column("customer", "salesman_id")
