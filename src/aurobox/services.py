"""Business logic and database services."""
from flask import current_app
from . import db
from .models import Door, DoorStatus, RobotState
from .robot import FlashbotController
from .config import load_config
from .utils import build_custom_call_payload

def get_controller():
    """Get FlashbotController instance."""
    return FlashbotController(load_config())

def set_robot_target_point(sn: str, point: str):
    """輔助函式：將機器人被指派的最新點位寫入資料庫"""
    state = RobotState.query.filter_by(sn=sn).first()
    if not state:
        state = RobotState(sn=sn, last_point=point)
        db.session.add(state)
    else:
        state.last_point = point
    db.session.commit()

def check_and_return_home_if_empty():
    """檢查是否所有艙門都為 EMPTY，若是則命令機器人返回管理室"""
    sn = current_app.config.get('ROBOT_SN')
    
    # 尋找還有貨(不等於 EMPTY)的門
    non_empty_doors = Door.query.filter(
        Door.sn == sn, 
        Door.status != DoorStatus.EMPTY
    ).count()
    
    if non_empty_doors == 0:
        controller = get_controller()
        home_point = current_app.config.get('HOME_POINT_NAME')
        payload = build_custom_call_payload(sn=sn, point=home_point)
        controller.custom_call2(payload=payload)
        set_robot_target_point(sn, home_point)
        return True
    return False