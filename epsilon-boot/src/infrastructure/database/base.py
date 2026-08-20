"""
SQLAlchemy ORM 基类模块

提供所有数据库模型的声明基类，支持 SQLAlchemy 2.0 的类型注解风格。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    SQLAlchemy ORM 声明基类

    所有数据库模型应继承此基类，以获得 SQLAlchemy 2.0 的映射能力。
    支持 Mapped 和 mapped_column 类型注解风格。

    示例:
        from sqlalchemy.orm import Mapped, mapped_column
        from sqlalchemy import String

        class User(Base):
            __tablename__ = "users"

            id: Mapped[int] = mapped_column(primary_key=True)
            name: Mapped[str] = mapped_column(String(100))
    """

    pass
