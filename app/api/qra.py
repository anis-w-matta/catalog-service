from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db, require_api_key
from app.models import QraHeader
from app.schemas.models import (QraBonusLinePreviewOut, QraDetailCacheOut,
                                QraHeaderCacheOut, QraLinePreviewOut,
                                QraPreviewIn, QraPreviewOut)
from app.services.qra_engine import OrderLineIn, preview_qra

router = APIRouter(tags=["qra"], dependencies=[Depends(require_api_key)])


@router.get("/qra/all", response_model=list[QraHeaderCacheOut])
def list_all(s: Session = Depends(get_db)):
    """Every QRA agreement - the backend's /qra/all proxies this straight
    through for the Android app's offline cache."""
    headers = s.execute(
        select(QraHeader).options(selectinload(QraHeader.details))
        .order_by(QraHeader.cust_nb)).scalars().all()
    return [
        QraHeaderCacheOut(
            cust_nb=h.cust_nb, from_date=h.from_date,
            to_date=h.to_date, status=h.status,
            details=[
                QraDetailCacheOut(
                    item_nb_buy=d.item_nb_buy, item_nb_get=d.item_nb_get,
                    item_nb_price=d.item_nb_price,
                    qty_buy=d.qty_buy, qty_get=d.qty_get,
                    qra_type=d.qra_type, qra_price=d.qra_price)
                for d in h.details
            ])
        for h in headers
    ]


@router.post("/qra/preview", response_model=QraPreviewOut)
def preview(body: QraPreviewIn, s: Session = Depends(get_db)):
    """Read-only preview of what committing `lines` would do under the
    customer's active QRA rule - never writes anything. Backs the
    pending-request review screen's pre-accept preview
    (GET /queue/{id} on the backend)."""
    lines = [OrderLineIn(line_nb=l.line_nb, item_nb=l.item_nb,
                         item_desc=l.item_desc, category=l.category,
                         qty=l.qty, uom=l.uom)
            for l in body.lines]
    previews, bonuses = preview_qra(s, body.cust_nb, lines,
                                    is_return=body.is_return)
    return QraPreviewOut(
        lines=[QraLinePreviewOut(
            line_nb=p.line_nb, unit_price=p.unit_price, is_free=p.is_free,
            substituted_item_nb=p.substituted_item_nb,
            substituted_item_desc=p.substituted_item_desc) for p in previews],
        bonus_lines=[QraBonusLinePreviewOut(
            item_nb=b.item_nb, item_desc=b.item_desc, qty=b.qty, uom=b.uom)
            for b in bonuses])
