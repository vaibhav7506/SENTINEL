from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Migration metadata; domain tables are introduced in Phase 3."""
