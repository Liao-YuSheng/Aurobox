"""Database models for local Flashbot hardware management."""

from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import event
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def _utc_now_naive() -> datetime:
    """Return current UTC time without tzinfo for existing naive DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class DoorStatus(str, Enum):
    """Door status enum."""
    EMPTY = "empty"        # 空置
    ASSIGNED = "assigned"  # 已分配 (尚未放貨)
    FULL = "full"          # 已裝載包裹

class Door(db.Model):
    """Door management record."""
    __tablename__ = "doors"
    __table_args__ = (
        db.UniqueConstraint("sn", "door_number", name="uq_doors_sn_door_number"),
        db.CheckConstraint(
            "door_number IN ('H_01', 'H_02', 'H_03', 'H_04')",
            name="ck_doors_allowed_numbers",
        ),
        db.CheckConstraint(
            "status IN ('empty', 'assigned', 'full')",
            name="ck_doors_allowed_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sn = db.Column(db.String(50), nullable=False)
    door_number = db.Column(db.String(10), nullable=False)  # 例如: H_01
    status = db.Column(db.String(20), nullable=False, default=DoorStatus.EMPTY.value)
    package_id = db.Column(db.String(100), nullable=True)   # 中央大腦指派的包裹 ID
    task_id = db.Column(db.String(100), nullable=True)   # 當前機器人的任務 ID
    
    updated_at = db.Column(db.DateTime, default=_utc_now_naive, onupdate=_utc_now_naive)

    def __repr__(self):
        return f"<Door {self.door_number} - {self.status} - Pkg: {self.package_id}>"

class RobotState(db.Model):
    """記憶機器人當前位置與狀態的資料表"""
    __tablename__ = "robot_state"

    id = db.Column(db.Integer, primary_key=True)
    sn = db.Column(db.String(50), unique=True, nullable=False)
    last_point = db.Column(db.String(100), nullable=True, default="管理室") 
    
    updated_at = db.Column(db.DateTime, default=_utc_now_naive, onupdate=_utc_now_naive)

    def __repr__(self):
        return f"<RobotState {self.sn} - Last Point: {self.last_point}>"

@event.listens_for(Door, "before_delete")
def _prevent_door_delete(mapper, connection, target):
    raise ValueError("Door records are immutable and cannot be deleted.")