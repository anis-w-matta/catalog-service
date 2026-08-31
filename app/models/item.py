from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Item(Base):
    __tablename__ = "item"

    item_number: Mapped[str] = mapped_column(String(30), primary_key=True)
    item_desc: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(100), index=True)
