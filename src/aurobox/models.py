"""Database models for local Flashbot hardware management."""

from datetime import datetime
from enum import Enum
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class DoorStatus(str, Enum):
    """Door status enum."""
    EMPTY = "empty"        # 空置
    ASSIGNED = "assigned"  # 已分配 (尚未放貨)
    FULL = "full"          # 已裝載包裹

class Door(db.Model):
    """Door management record."""
    __tablename__ = "doors"

    id = db.Column(db.Integer, primary_key=True)
    sn = db.Column(db.String(50), nullable=False)
    door_number = db.Column(db.String(10), nullable=False)  # 例如: H_01
    status = db.Column(db.String(20), default=DoorStatus.EMPTY)
    package_id = db.Column(db.String(100), nullable=True)   # 中央大腦指派的包裹 ID
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Door {self.door_number} - {self.status} - Pkg: {self.package_id}>"