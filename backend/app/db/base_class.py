"""Defines `Base`, the parent class that EVERY database model inherits from."""
import re
from typing import Any

from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """The shared parent of every table in this application."""

    id: Any
    __name__: str

    @declared_attr.directive
    def __tablename__(cls) -> str:
        """Build the table name from the class name."""
        name = cls.__name__

        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

        if snake.endswith("y"):
            return snake[:-1] + "ies"
        elif snake.endswith("s"):
            return snake
        else:
            return snake + "s"
