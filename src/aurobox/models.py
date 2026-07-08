"""Database models for Aurobox package delivery system."""

from datetime import datetime
from enum import Enum
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class PackageStatus(str, Enum):
    """Package delivery status enum."""
    PENDING = "pending"  # 到貨通知剛送出，等待用戶回復
    LATER = "later"  # 用戶選擇稍後再取
    PICKUP_NOW = "pickup_now"  # 用戶選擇取貨
    DELIVERING = "delivering"  # 管理員已派工，機器人前往中
    ARRIVED = "arrived"  # 機器人已抵達，等待用戶取貨
    COMPLETED = "completed"  # 用戶已成功取貨
    RETURNED_CANCELLED = "returned_cancelled"  # 用戶臨時取消，機器人退回
    RETURNED_TIMEOUT = "returned_timeout"  # 逾時未取，系統退回


class DoorStatus(str, Enum):
    """Door status enum."""
    OPEN = "open"  # 開啟
    CLOSED = "closed"  # 關閉


class LoadingStatus(str, Enum):
    """Door loading status enum."""
    EMPTY = "empty"  # 空置
    LOCKED = "locked"  # 已鎖定（住戶剛下單）
    LOADED = "loaded"  # 已裝載（送貨中）


class Package(db.Model):
    """Package delivery record."""
    __tablename__ = "packages"

    id = db.Column(db.String(36), primary_key=True)
    phone_number = db.Column(db.String(20), nullable=False)  # 住戶電話
    address = db.Column(db.String(255), nullable=False)  # 住戶住址
    door_number = db.Column(db.String(10))  # 分配的艙門號 (H_01, H_02 等)
    status = db.Column(db.String(20), default=PackageStatus.PENDING)
    line_user_id = db.Column(db.String(255))  # LINE 用戶 ID
    pickup_qr_token = db.Column(db.String(64), unique=True, index=True)  # 掃碼取貨用 QR token
    
    # 時間戳記
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    arrived_at = db.Column(db.DateTime)  # 機器人抵達時間
    completed_at = db.Column(db.DateTime)  # 取貨完成時間
    
    # 備註
    notes = db.Column(db.Text)

    def __repr__(self):
        return f"<Package {self.id} - {self.address} - {self.status}>"


class Door(db.Model):
    """Door state record."""
    __tablename__ = "doors"

    id = db.Column(db.Integer, primary_key=True)
    sn = db.Column(db.String(50), nullable=False)  # Robot SN
    door_number = db.Column(db.String(10), nullable=False)  # H_01, H_02 等
    status = db.Column(db.String(20), default=DoorStatus.CLOSED)
    loading_status = db.Column(db.String(20), default=LoadingStatus.EMPTY)
    package_id = db.Column(db.String(36), db.ForeignKey('packages.id'))
    address = db.Column(db.String(255))  # 對應房號
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Door {self.door_number} - {self.status} - {self.loading_status}>"


class RobotStatus(db.Model):
    """Robot real-time status."""
    __tablename__ = "robot_status"

    id = db.Column(db.Integer, primary_key=True)
    sn = db.Column(db.String(50), unique=True, nullable=False)
    state = db.Column(db.String(20))  # Idle / Delivering / Charging / Error
    battery_level = db.Column(db.Float)  # 電池百分比 (0-100)
    current_location = db.Column(db.String(255))  # 當前位置名稱
    move_state = db.Column(db.String(20))  # 移動狀態
    run_state = db.Column(db.String(20))  # V2 工作狀態
    task_state = db.Column(db.String(40))  # 任務語意狀態（task/state/get）
    is_charging = db.Column(db.Integer)  # 1 / -1
    charge_stage = db.Column(db.String(50))  # 充電階段文字
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<RobotStatus {self.sn} - {self.state} - {self.battery_level}%>"


class DeliveryHistory(db.Model):
    """Delivery history record."""
    __tablename__ = "delivery_history"

    id = db.Column(db.Integer, primary_key=True)
    package_id = db.Column(db.String(36), db.ForeignKey('packages.id'))
    sn = db.Column(db.String(50))  # Robot SN
    action = db.Column(db.String(50))  # 動作名稱
    details = db.Column(db.JSON)  # 詳細信息
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<DeliveryHistory {self.package_id} - {self.action}>"
