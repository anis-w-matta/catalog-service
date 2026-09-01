from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.models import Customer, CustomerOwnershipHistory
from app.schemas.models import (AssignSalesmanIn, CustomerCacheOut,
                                CustomerCandidateOut, CustomerDetailOut,
                                CustomerMatchOut, OwnershipHistoryEntryOut)
from app.services.customer_ownership import record_ownership_change
from app.services.match_customer import match_customer, search_customers

router = APIRouter(tags=["customers"], dependencies=[Depends(require_api_key)])


@router.get("/customers/by-numbers", response_model=list[CustomerCacheOut])
def by_numbers(nbs: str, s: Session = Depends(get_db)):
    """Batch name lookup for a specific set of customer numbers (comma-
    separated) - backs list views that need many customers' names at once
    (the backend's /queue listing, /orders/recent) without pulling the
    full ~40k-row table for a handful of lookups. Unfiltered by ownership:
    callers only ever pass numbers they've already decided are visible to
    the requester (e.g. queue.py filters PendingRequest rows by ownership
    before batching their names here)."""
    numbers = [n for n in nbs.split(",") if n]
    if not numbers:
        return []
    rows = s.execute(
        select(Customer.customer_number, Customer.customer_name)
        .where(Customer.customer_number.in_(numbers))).all()
    return [CustomerCacheOut(cust_nb=r[0], customer_name=r[1]) for r in rows]


@router.get("/customers/match", response_model=CustomerMatchOut)
def match(q: str = Query(..., min_length=1), s: Session = Depends(get_db)):
    """Auto-resolution for a spoken/typed customer reference (exact number
    match, else a gated/tie-safe rapidfuzz pass) - backs the intake
    pipeline's customer resolution.

    Not restricted to any one salesman's book: the voice pipeline doesn't
    carry recorder identity through to this call (see the backend's
    RequestDetail gap notes), so this can still resolve to a customer the
    eventual reviewer doesn't own - that's caught for real at commit time
    (POST /orders' ownership check), same as any other manual override.
    """
    m = match_customer(s, q)
    return CustomerMatchOut(cust_nb=m.customer_number, customer_name=m.customer_name,
                            score=m.score, status=m.status.value)


def _owned_numbers(s: Session, salesman_id: str | None) -> set[str]:
    if not salesman_id:
        return set()
    return set(s.scalars(
        select(Customer.customer_number)
        .where(Customer.salesman_id == salesman_id)))


@router.get("/customers/search", response_model=list[CustomerCandidateOut])
def search(q: str = Query(..., min_length=1),
          salesman_id: str | None = Query(default=None),
          admin: bool = Query(default=False), s: Session = Depends(get_db)):
    """Ranked customer lookup for an explicit human search (the Request
    screen's "select customer" flow) - no threshold/tie-margin gating.

    salesman_id/admin are supplied by the backend, never a raw end-user
    parameter: the backend has already authenticated the caller and fills
    these in from that verified identity. Unset salesman_id with admin=False
    (the default) matches nothing, not everything - a caller must pass one
    or the other explicitly.
    """
    if not admin and not salesman_id:
        return []
    candidates = search_customers(s, q)
    if not admin:
        owned = _owned_numbers(s, salesman_id)
        candidates = [c for c in candidates if c[0] in owned]
    return [
        CustomerCandidateOut(cust_nb=nb, customer_name=name, score=score)
        for nb, name, score in candidates
    ]


@router.get("/customers/all", response_model=list[CustomerCacheOut])
def list_all(salesman_id: str | None = Query(default=None),
            admin: bool = Query(default=False), s: Session = Depends(get_db)):
    """The full customer list - the backend's /customers/all proxies this
    straight through for the Android app's offline cache, and also reuses
    it to build the ownership set for filtering the review queue.

    salesman_id/admin: see search() above - same trusted-caller contract.
    """
    if not admin and not salesman_id:
        return []
    q = select(Customer.customer_number, Customer.customer_name)
    if not admin:
        q = q.where(Customer.salesman_id == salesman_id)
    rows = s.execute(q.order_by(Customer.customer_number)).all()
    return [CustomerCacheOut(cust_nb=r[0], customer_name=r[1]) for r in rows]


@router.get("/customers/{cust_nb}", response_model=CustomerDetailOut | None)
def get_customer(cust_nb: str, s: Session = Depends(get_db)):
    """Full detail for one customer, including its current salesman_id -
    backs the backend's direct-access endpoint and its ownership checks
    (claim/reject/callback). Unfiltered here: the backend is the one that
    decides, from the returned salesman_id, whether the caller may see it."""
    c = s.get(Customer, cust_nb)
    if c is None:
        return None
    return CustomerDetailOut(
        cust_nb=c.customer_number, customer_name=c.customer_name,
        email=c.email, telephone=c.telephone, city=c.city,
        address1=c.address1, salesman_id=c.salesman_id)


@router.patch("/customers/{cust_nb}/salesman", response_model=CustomerDetailOut)
def assign_salesman(cust_nb: str, body: AssignSalesmanIn,
                    s: Session = Depends(get_db)):
    """Assign (or clear, with salesman_id=null) this customer's owning
    salesman. Whether the caller is allowed to do this at all (admin-only)
    is the backend's job - this service has no notion of roles, only of
    persisting the assignment once the backend has already authorized it.
    Does not validate that salesman_id refers to a real salesman login: that
    identity lives in the backend's own database, not here - the backend
    validates it against its own Salesman table before calling this."""
    c = s.get(Customer, cust_nb)
    if c is None:
        raise HTTPException(404, f"no such customer {cust_nb!r}")
    c.salesman_id = body.salesman_id
    record_ownership_change(s, cust_nb, body.salesman_id)
    s.flush()
    return CustomerDetailOut(
        cust_nb=c.customer_number, customer_name=c.customer_name,
        email=c.email, telephone=c.telephone, city=c.city,
        address1=c.address1, salesman_id=c.salesman_id)


@router.get("/customers/{cust_nb}/ownership-history",
           response_model=list[OwnershipHistoryEntryOut])
def ownership_history(cust_nb: str, s: Session = Depends(get_db)):
    """Full point-in-time ownership record for one customer, oldest first -
    the data behind historical (not just current) salesman attribution.
    Whether the caller is allowed to see this at all (admin-only, same as
    the assign endpoint above) is the backend's job, same trusted-caller
    contract as the rest of this router."""
    rows = s.scalars(
        select(CustomerOwnershipHistory)
        .where(CustomerOwnershipHistory.cust_nb == cust_nb)
        .order_by(CustomerOwnershipHistory.effective_from)
    ).all()
    return [
        OwnershipHistoryEntryOut(
            salesman_id=r.salesman_id, effective_from=r.effective_from,
            effective_to=r.effective_to)
        for r in rows
    ]
