from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Customer(Base):
    __tablename__ = "customer"

    customer_number: Mapped[str] = mapped_column(
        "CustomerNumber", String(20), primary_key=True)
    customer_name: Mapped[str] = mapped_column("CustomerName", String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column("City", String(100))
    address1: Mapped[str | None] = mapped_column("Address1", String(200))
    address2: Mapped[str | None] = mapped_column("Address2", String(200))
    # The salesman (backend's Salesman.login_id) this customer is assigned
    # to - null until an admin assigns one (see app/api/customers.py's
    # assign endpoint). No FK constraint: Salesman lives in the backend
    # service's own schema/service boundary, reached only over its API,
    # never this service's database - same deliberate isolation as every
    # other cross-service reference here (order_header.cust_nb has none
    # either). Ownership is instead enforced in application code: this
    # column is what POST /orders checks acting_salesman_id against.
    salesman_id: Mapped[str | None] = mapped_column(
        String(50), index=True, nullable=True)
