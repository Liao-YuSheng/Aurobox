"""
資料表結構定義，對應《LINE模組_實作步驟.md》階段1規劃的兩張表
"""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def now_taipei() -> datetime:
    """
    回傳台灣當地時間（naive datetime，不帶tzinfo）。
    這幾張表的DateTime欄位都是不帶timezone的plain DateTime，
    所以這裡刻意strip掉tzinfo，直接存「數字看起來就是台灣時間」的值，
    存進去、讀出來都不用再另外做時區轉換。
    """
    return datetime.now(TAIPEI_TZ).replace(tzinfo=None)


class Package(Base):
    __tablename__ = "packages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    unit = Column(String(50), nullable=False)                  # 門牌
    line_user_id = Column(String(100), nullable=False)         # 收件人 LINE User ID
    status = Column(String(30), nullable=False, default="pending")
    # pending / pickup_now / delivering / arrived
    # / completed / returned_timeout / voided / rejected_at_door
    door_id = Column(String(10), nullable=True)                # 分配的艙門編號
    arrived_at = Column(DateTime, nullable=True)                # 機器人抵達時間，逾時判斷用
    door_closed_at = Column(DateTime, nullable=True)            # 拒收後管理員取出包裹、按關門的時間
    created_at = Column(DateTime, default=now_taipei)
    updated_at = Column(DateTime, default=now_taipei, onupdate=now_taipei)


class LineBinding(Base):
    __tablename__ = "line_binding"

    line_user_id = Column(String(100), primary_key=True)
    unit = Column(String(50), nullable=False)
    name = Column(String(100), nullable=False)
    bound_at = Column(DateTime, default=now_taipei)
    status = Column(String(20), default="active")               # active / inactive
    solo_notify = Column(Boolean, nullable=False, default=True)   # 是否限本人接收包裹通知

class PackageRecipient(Base):
    __tablename__ = "package_recipients"

    package_id = Column(UUID(as_uuid=True), primary_key=True)
    unit = Column(String(50), nullable=False)                  # 門牌
    line_user_id = Column(String(100), primary_key=True)


class TaskLog(Base):
    """
    任務事件紀錄，給每日報表用。
    在此之前，系統裡的事件只用print()印到console，服務重啟或console關掉就消失了，
    沒辦法回頭查歷史。這張表把關鍵事件（建立包裹、分配艙門、派工、抵達、取貨、逾時退回等）
    真正存進資料庫，才能查「某一天發生過什麼事」。
    """
    __tablename__ = "task_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_id = Column(UUID(as_uuid=True), nullable=True)     # 有些事件（例如機器人連線失敗）不一定對應到特定包裹
    event_type = Column(String(50), nullable=False)
    # created / rejected / rejected_at_door / door_assigned / door_assign_failed / dispatched / dispatch_failed
    # / arrived / pickup_opened / pickup_open_failed / completed / complete_failed
    # / returned_timeout / returned / cancel_task_failed / returned_and_opened / return_failed
    # / door_closed / close_door_failed
    level = Column(String(10), nullable=False, default="info")  # info / warning / error
    detail = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=now_taipei)