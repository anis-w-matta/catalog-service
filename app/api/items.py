from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.models import Item
from app.schemas.models import (CandidateOut, ItemCacheOut,
                                ItemCandidateOut, ItemMatchResultOut)
from app.services.match_item import resolve_item

router = APIRouter(tags=["items"], dependencies=[Depends(require_api_key)])


def _to_result_out(match) -> ItemMatchResultOut:
    return ItemMatchResultOut(
        item_number=match.item_number, item_description=match.item_description,
        item_family=match.item_family, status=match.status.value,
        score=match.score, method=match.method, explanation=match.explanation,
        candidates=[
            ItemCandidateOut(
                item_number=c.item_number, item_description=c.item_description,
                item_family=c.item_family, score=c.score,
                numeric_compatible=c.numeric_compatible,
                numeric_conflict_reason=c.numeric_conflict_reason)
            for c in match.candidates
        ])


@router.get("/items/resolve", response_model=ItemMatchResultOut)
def resolve(q: str = Query(..., min_length=1), s: Session = Depends(get_db)):
    """Full layered item resolution (exact/pg_trgm+rapidfuzz + numeric
    pack-code conflict checking) for one spoken/typed item span - backs
    both the backend's free-form intake pipeline and its scripted-command
    pipeline. Mirrors the shape of the old in-process
    app.services.scripted.match_item.resolve_item exactly, so the
    backend's catalog_client.py can rebuild the same result object its
    callers already expect."""
    return _to_result_out(resolve_item(s, q))


@router.get("/items/search", response_model=list[CandidateOut])
def search(q: str = Query(..., min_length=1), s: Session = Depends(get_db)):
    """Ranked-candidate lookup - backs the Request screen's "add item"
    dropdown, always returning candidates even when none is confident
    enough to auto-resolve."""
    match = resolve_item(s, q)
    return [
        CandidateOut(item_nb=c.item_number, item_desc=c.item_description,
                    category=c.item_family or "", score=c.score,
                    method=match.method, attribute_conflict=not c.numeric_compatible)
        for c in match.candidates[:5]
    ]


@router.get("/items/all", response_model=list[ItemCacheOut])
def list_all(s: Session = Depends(get_db)):
    """The full item catalogue - the backend's /items/all proxies this
    straight through for the Android app's offline cache."""
    rows = s.execute(
        select(Item.item_number, Item.item_desc, Item.category)
        .order_by(Item.item_number)).all()
    return [ItemCacheOut(item_nb=r[0], item_desc=r[1], category=r[2])
           for r in rows]


@router.get("/items/by-numbers", response_model=list[ItemCacheOut])
def by_numbers(nbs: str, s: Session = Depends(get_db)):
    """Batch lookup for a specific set of item numbers (comma-separated) -
    backs draft_builder.py's "look up each item's current category"
    step (lines_from_prior_order) without pulling the full catalogue for
    a handful of lookups."""
    numbers = [n for n in nbs.split(",") if n]
    if not numbers:
        return []
    rows = s.execute(
        select(Item.item_number, Item.item_desc, Item.category)
        .where(Item.item_number.in_(numbers))).all()
    return [ItemCacheOut(item_nb=r[0], item_desc=r[1], category=r[2])
           for r in rows]
