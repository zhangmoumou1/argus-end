from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON

from app.models import Base


class MenuPermission(Base):
    __tablename__ = "argus_menu_permission"

    role = Column(Integer, primary_key=True, autoincrement=False)
    paths = Column(JSON, nullable=False)
    update_user = Column(Integer, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
