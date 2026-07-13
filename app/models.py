"""
資料表結構定義，對應《LINE模組_實作步驟.md》階段1規劃的兩張表
"""
import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class Package(Base):
    __tablename__ = "packages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit = Column(String(50), nullable=False)                  # 門牌
    line_user_id = Column(String(100), nullable=False)         # 收件人 LINE User ID
    status = Column(String(30), nullable=False, default="pending")
    # pending / later / pickup_now / delivering / arrived
    # / completed / returned_cancelled / returned_timeout
    door_id = Column(String(10), nullable=True)                # 分配的艙門編號
    arrived_at = Column(DateTime, nullable=True)                # 機器人抵達時間，逾時判斷用
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LineBinding(Base):
    __tablename__ = "line_binding"

    line_user_id = Column(String(100), primary_key=True)
    unit = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    bound_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")               # active / inactive
    solo_notify = Column(Boolean, nullable=False, default=False)   # 是否限本人接收包裹通知

class PackageRecipient(Base):
    __tablename__ = "package_recipients"

    package_id = Column(UUID(as_uuid=True), primary_key=True)
    unit = Column(String(50), nullable=False)                  # 門牌
    line_user_id = Column(String(100), primary_key=True)