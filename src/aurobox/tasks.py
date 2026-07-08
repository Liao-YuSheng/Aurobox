"""Background task service for delivery management."""

from datetime import datetime, timedelta
from . import db
from .models import Package, PackageStatus, DeliveryHistory, RobotStatus
from .robot import FlashbotController
from .config import load_config
from .webhooks import send_returned_notification
import logging

logger = logging.getLogger(__name__)


class TaskService:
    """Service for background tasks."""
    
    def __init__(self):
        self.controller = FlashbotController(load_config())
    
    def poll_robot_status(self, sn: str):
        """
        Poll robot status and update DB.
        """
        try:
            status_summary = self.controller.get_status_summary(sn)
            door_state = self.controller.get_door_state(sn)
            
            # 更新機器人狀態
            robot = RobotStatus.query.filter_by(sn=sn).first()
            if not robot:
                robot = RobotStatus(sn=sn)
            
            robot.state = status_summary.get('state', 'Idle')
            robot.battery_level = status_summary.get('battery_level', 0)
            robot.current_location = status_summary.get('current_location', '')
            robot.move_state = status_summary.get('move_state', '')
            robot.run_state = status_summary.get('run_state', '')
            robot.task_state = status_summary.get('task_state', '')
            robot.is_charging = status_summary.get('is_charging')
            robot.charge_stage = status_summary.get('charge_stage', '')
            robot.updated_at = datetime.utcnow()
            
            db.session.add(robot)
            db.session.commit()
            
            logger.info(f"Robot {sn} status updated: {robot.state} - {robot.battery_level}%")
            return True
        
        except Exception as e:
            logger.error(f"Failed to poll robot status: {str(e)}")
            return False
    
    def check_pickup_timeout(self, timeout_minutes: int = 10):
        """
        Check for packages that have timed out waiting for pickup.
        Default timeout is 10 minutes after arrival.
        """
        timeout_threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        
        # Find arrived packages that haven't been picked up
        timed_out = Package.query.filter(
            Package.status == PackageStatus.ARRIVED,
            Package.arrived_at < timeout_threshold
        ).all()
        
        for package in timed_out:
            package.status = PackageStatus.RETURNED_TIMEOUT
            package.updated_at = datetime.utcnow()
            
            history = DeliveryHistory(
                package_id=package.id,
                action='auto_timeout',
                details={'timeout_at': datetime.utcnow().isoformat()}
            )
            
            db.session.add(history)
            
            # 發送通知
            send_returned_notification(package)
            
            logger.info(f"Package {package.id} marked as timed out")
        
        if timed_out:
            db.session.commit()
        
        return len(timed_out)
    
    def sync_door_states(self, sn: str):
        """
        Sync door states from robot API.
        """
        try:
            door_state = self.controller.get_door_state(sn)
            logger.info(f"Door states for {sn}: {door_state}")
            # TODO: Update door states in DB
            return True
        except Exception as e:
            logger.error(f"Failed to sync door states: {str(e)}")
            return False
    
    def handle_robot_returning(self, sn: str):
        """
        Handle robot returning to management room.
        Update all delivering packages to complete/returned status.
        """
        packages = Package.query.filter_by(status=PackageStatus.DELIVERING).all()
        
        for package in packages:
            if package.status not in [PackageStatus.COMPLETED, PackageStatus.RETURNED_TIMEOUT]:
                # 檢查是否超時
                if package.arrived_at:
                    elapsed = datetime.utcnow() - package.arrived_at
                    if elapsed > timedelta(minutes=10):
                        package.status = PackageStatus.RETURNED_TIMEOUT
                        send_returned_notification(package)
        
        db.session.commit()
        logger.info(f"Robot {sn} returned to management room")
