class CatalogError(Exception):
    code: str = "error"


class CustomerNotFound(CatalogError):
    code = "customer_not_found"

    def __init__(self, cust_nb: str | None):
        self.cust_nb = cust_nb
        super().__init__(f"No customer {cust_nb!r}")


class TargetOrderNotFound(CatalogError):
    code = "target_order_not_found"

    def __init__(self, order_nb: str | None):
        self.order_nb = order_nb
        super().__init__(f"No sales order {order_nb!r}")


class OrderAlreadyReturned(CatalogError):
    code = "order_already_returned"

    def __init__(self, order_nb: str):
        self.order_nb = order_nb
        super().__init__(f"Order {order_nb} has already been returned")


class UnresolvedLines(CatalogError):
    code = "unresolved_lines"


class CustomerNotAuthorized(CatalogError):
    """The acting salesman doesn't own this customer (Customer.salesman_id
    doesn't match), so they may not place an order for them - guards
    create_order() the same way CustomerNotFound guards customer identity:
    no operator action can override it, and it's checked before any
    OrderHeader/OrderDetail row is written."""
    code = "customer_not_authorized"

    def __init__(self, cust_nb: str | None):
        self.cust_nb = cust_nb
        super().__init__(f"Customer {cust_nb!r} is not assigned to the acting salesman")
