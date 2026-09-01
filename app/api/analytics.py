from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_db, require_api_key
from app.schemas.models import (CategorySummaryOut, CustomerDetailSummaryOut,
                                CustomerOrderHistoryRowOut, CustomersSummaryOut,
                                DataHealthOut, HistogramBucketOut,
                                ItemDetailSummaryOut, OrdersSummaryOut,
                                OrdersTrendOut, RankedCustomerOut,
                                RankedItemOut, SalesmanOrderMetricsOut,
                                SalesmenOrderMetricsOut, TrendPointOut)
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["analytics"],
                   dependencies=[Depends(require_api_key)])

# Whether the caller is allowed to see analytics at all (admin-only) is the
# backend's job, same trusted-caller contract as every other endpoint in
# this service - see app/api/customers.py's assign_salesman for the same
# pattern.


def _filter(date_from: datetime | None, date_to: datetime | None,
           cust_nb: str | None, item_nb: str | None, category: str | None,
           order_type: str | None, salesman_id: str | None
           ) -> analytics.OrdersFilter:
    return analytics.OrdersFilter(
        date_from=date_from, date_to=date_to, cust_nb=cust_nb,
        item_nb=item_nb, category=category, order_type=order_type,
        salesman_id=salesman_id)


@router.get("/orders-summary", response_model=OrdersSummaryOut)
def orders_summary(date_from: datetime | None = None,
                   date_to: datetime | None = None,
                   cust_nb: str | None = None, item_nb: str | None = None,
                   category: str | None = None, order_type: str | None = None,
                   salesman_id: str | None = None, s=Depends(get_db)):
    r = analytics.orders_summary(
        s, _filter(date_from, date_to, cust_nb, item_nb, category,
                  order_type, salesman_id))
    return OrdersSummaryOut(**vars(r))


@router.get("/items-per-order-histogram", response_model=list[HistogramBucketOut])
def items_per_order_histogram(date_from: datetime | None = None,
                              date_to: datetime | None = None,
                              cust_nb: str | None = None,
                              salesman_id: str | None = None, s=Depends(get_db)):
    r = analytics.items_per_order_histogram(
        s, _filter(date_from, date_to, cust_nb, None, None, None, salesman_id))
    return [HistogramBucketOut(**vars(x)) for x in r]


@router.get("/salesmen-order-metrics", response_model=SalesmenOrderMetricsOut)
def salesmen_order_metrics(date_from: datetime | None = None,
                           date_to: datetime | None = None,
                           cust_nb: str | None = None,
                           item_nb: str | None = None,
                           category: str | None = None,
                           order_type: str | None = None, s=Depends(get_db)):
    r = analytics.salesmen_order_metrics(
        s, _filter(date_from, date_to, cust_nb, item_nb, category,
                  order_type, None))
    return SalesmenOrderMetricsOut(
        by_salesman=[SalesmanOrderMetricsOut(**vars(x)) for x in r.by_salesman],
        orders_excluded_missing_commit_date=r.orders_excluded_missing_commit_date)


@router.get("/top-customers", response_model=list[RankedCustomerOut])
def top_customers(order_by: str = Query("order_count",
                                        pattern="^(order_count|item_quantity)$"),
                  limit: int = Query(20, le=100), date_from: datetime | None = None,
                  date_to: datetime | None = None, salesman_id: str | None = None,
                  item_nb: str | None = None, s=Depends(get_db)):
    # item_nb (Phase 9 Item x Customer matrix): "which customers buy this
    # item" - the underlying query already supported item_nb via
    # OrdersFilter; only this route parameter was missing.
    r = analytics.top_customers(
        s, _filter(date_from, date_to, None, item_nb, None, None, salesman_id),
        order_by, limit)
    return [RankedCustomerOut(**vars(x)) for x in r]


@router.get("/top-items", response_model=list[RankedItemOut])
def top_items(order_by: str = Query("quantity",
                                    pattern="^(quantity|order_frequency)$"),
             limit: int = Query(20, le=100), date_from: datetime | None = None,
             date_to: datetime | None = None, category: str | None = None,
             cust_nb: str | None = None,
             salesman_id: str | None = None, s=Depends(get_db)):
    r = analytics.top_items(
        s, _filter(date_from, date_to, cust_nb, None, category, None, salesman_id),
        order_by, limit)
    return [RankedItemOut(**vars(x)) for x in r]


@router.get("/orders-trend", response_model=OrdersTrendOut)
def orders_trend(date_from: datetime | None = None, date_to: datetime | None = None,
                 cust_nb: str | None = None, item_nb: str | None = None,
                 category: str | None = None, order_type: str | None = None,
                 salesman_id: str | None = None,
                 granularity: str = Query("month", pattern="^(day|month)$"),
                 s=Depends(get_db)):
    r = analytics.orders_trend(
        s, _filter(date_from, date_to, cust_nb, item_nb, category,
                  order_type, salesman_id), granularity)
    return OrdersTrendOut(
        points=[TrendPointOut(**vars(p)) for p in r.points],
        orders_excluded_missing_commit_date=r.orders_excluded_missing_commit_date)


@router.get("/customers/{cust_nb}/order-history",
           response_model=list[CustomerOrderHistoryRowOut])
def customer_order_history(cust_nb: str, s=Depends(get_db)):
    r = analytics.customer_order_history(s, cust_nb)
    return [CustomerOrderHistoryRowOut(**vars(x)) for x in r]


@router.get("/categories-summary", response_model=list[CategorySummaryOut])
def categories_summary(date_from: datetime | None = None,
                       date_to: datetime | None = None,
                       salesman_id: str | None = None, s=Depends(get_db)):
    r = analytics.categories_summary(
        s, _filter(date_from, date_to, None, None, None, None, salesman_id))
    return [CategorySummaryOut(**vars(x)) for x in r]


@router.get("/customers-summary", response_model=CustomersSummaryOut)
def customers_summary(s=Depends(get_db)):
    return CustomersSummaryOut(**vars(analytics.customers_summary(s)))


@router.get("/customers/{cust_nb}/summary", response_model=CustomerDetailSummaryOut)
def customer_summary(cust_nb: str, s=Depends(get_db)):
    r = analytics.customer_summary(s, cust_nb)
    if r is None:
        raise HTTPException(404, f"no such customer {cust_nb!r}")
    return CustomerDetailSummaryOut(**vars(r))


@router.get("/items/{item_nb}/summary", response_model=ItemDetailSummaryOut)
def item_summary(item_nb: str, s=Depends(get_db)):
    r = analytics.item_summary(s, item_nb)
    if r is None:
        raise HTTPException(404, f"no such item {item_nb!r}")
    return ItemDetailSummaryOut(**vars(r))


@router.get("/data-health", response_model=DataHealthOut)
def data_health(s=Depends(get_db)):
    return DataHealthOut(**vars(analytics.data_health(s)))
