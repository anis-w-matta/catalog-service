from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ItemCacheOut(BaseModel):
    item_nb: str
    item_desc: str
    category: str


class CandidateOut(BaseModel):
    item_nb: str
    item_desc: str
    category: str
    score: float
    method: str | None = None
    attribute_conflict: bool = False


class ItemCandidateOut(BaseModel):
    item_number: str
    item_description: str
    item_family: str | None
    score: float
    numeric_compatible: bool
    numeric_conflict_reason: str | None = None


class ItemMatchResultOut(BaseModel):
    item_number: str | None
    item_description: str | None
    item_family: str | None
    status: str  # "matched" | "ambiguous" | "not_found"
    score: float | None
    method: str
    candidates: list[ItemCandidateOut] = []
    explanation: str = ""


class CustomerCacheOut(BaseModel):
    cust_nb: str
    customer_name: str


class CustomerCandidateOut(BaseModel):
    cust_nb: str
    customer_name: str
    score: float


class CustomerMatchOut(BaseModel):
    cust_nb: str | None
    customer_name: str | None
    score: float
    status: str  # "matched" | "ambiguous" | "not_found"


class CustomerDetailOut(BaseModel):
    cust_nb: str
    customer_name: str
    email: str | None = None
    telephone: str | None = None
    city: str | None = None
    address1: str | None = None
    salesman_id: str | None = None


class AssignSalesmanIn(BaseModel):
    salesman_id: str | None = None


class QraDetailCacheOut(BaseModel):
    item_nb_buy: str | None = None
    item_nb_get: str | None = None
    item_nb_price: str | None = None
    qty_buy: Decimal
    qty_get: Decimal | None = None
    qra_type: str
    qra_price: Decimal | None = None


class QraHeaderCacheOut(BaseModel):
    cust_nb: str
    from_date: date
    to_date: date
    status: str
    details: list[QraDetailCacheOut] = []


class LineIn(BaseModel):
    line_nb: int
    item_nb: str | None = None
    item_desc: str | None = None
    category: str | None = None
    qty: Decimal | None = None
    uom: str | None = None


class QraPreviewIn(BaseModel):
    cust_nb: str | None = None
    lines: list[LineIn] = []
    is_return: bool = False


class QraLinePreviewOut(BaseModel):
    line_nb: int
    unit_price: Decimal | None
    is_free: bool
    substituted_item_nb: str | None
    substituted_item_desc: str | None


class QraBonusLinePreviewOut(BaseModel):
    item_nb: str
    item_desc: str
    qty: Decimal
    uom: str | None


class QraPreviewOut(BaseModel):
    lines: list[QraLinePreviewOut]
    bonus_lines: list[QraBonusLinePreviewOut]


class OrderLineOut(BaseModel):
    line_nb: int
    item_nb: str | None
    item_desc: str | None
    qty: Decimal | None
    uom: str | None
    is_free: bool = False


class OrderOut(BaseModel):
    order_nb: str
    order_type: str
    cust_nb: str
    customer_name: str | None = None
    lines: list[OrderLineOut] = []


class ResolveTargetIn(BaseModel):
    cust_nb: str
    # "explicit" (spec: exactly one enum left, order_nb) or "implicit"
    # (free-form reference, falls back to open-order-count disambiguation)
    mode: str
    reference: str | None = None


class ResolveTargetOut(BaseModel):
    order_nb: str | None = None
    order_type: str | None = None
    cust_nb: str | None = None
    ambiguity: str | None = None
    lines: list[OrderLineOut] = []


class LineEditIn(BaseModel):
    line_nb: int
    item_nb: str | None = None
    item_desc: str | None = None
    qty: Decimal | None = None
    uom: str | None = None


class CreateOrderIn(BaseModel):
    commit_intent_id: str
    order_type: str
    cust_nb: str | None = None
    cust_nb_override: str | None = None
    target_order_nb_override: str | None = None
    primary_intent: str | None = None
    full_return: bool = False
    lines: list[LineIn] = []
    line_edits: list[LineEditIn] = []
    removed_line_nbs: list[int] = []
    is_return: bool = False
    # Identity of the salesman the backend has already authenticated (never
    # client-supplied - the backend fills this in from its own verified
    # bearer token before calling here). acting_is_admin lets an admin
    # bypass the per-customer ownership check below.
    acting_salesman_id: str
    acting_is_admin: bool = False


class CreateOrderOut(BaseModel):
    order_nb: str
    order_type: str
    cust_nb: str
    target_order_nb: str | None
    target_order_type: str | None
    lines: list[OrderLineOut]
