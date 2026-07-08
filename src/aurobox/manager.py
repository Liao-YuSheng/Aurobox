"""Manager operations for package delivery workflow."""

from datetime import datetime
from . import db
from .models import Package, Door, PackageStatus, DoorStatus, LoadingStatus, DeliveryHistory
from .robot import FlashbotController
from .config import load_config
from .webhooks import send_delivery_notification, send_departed_notification
import logging

logger = logging.getLogger(__name__)


class ManagerService:
    """Service for manager operations on dashboard."""
    
    def __init__(self):
        self.controller = FlashbotController(load_config())
    
    def register_package(self, phone_number: str, address: str) -> Package:
        """
        Manager registers a new package arrival.
        Corresponds to "管理員收到郵差送來的包裹" in flow.
        """
        import uuid
        
        package = Package(
            id=str(uuid.uuid4()),
            phone_number=phone_number,
            address=address,
            status=PackageStatus.PENDING,
            created_at=datetime.utcnow()
        )
        
        db.session.add(package)
        db.session.commit()
        
        logger.info(f"Package {package.id} registered: {address}")
        return package
    
    def allocate_door(self, package_id: str, sn: str = None) -> str:
        """
        Allocate available door for package.
        Returns door number or raises exception if no doors available.
        """
        sn = sn or self.controller.default_sn
        
        # Find empty door
        available_door = Door.query.filter_by(
            sn=sn,
            loading_status=LoadingStatus.EMPTY
        ).first()
        
        if not available_door:
            raise ValueError("No available doors")
        
        package = Package.query.get(package_id)
        if not package:
            raise ValueError(f"Package {package_id} not found")
        
        package.door_number = available_door.door_number
        available_door.package_id = package_id
        available_door.loading_status = LoadingStatus.LOCKED
        available_door.address = package.address
        
        db.session.commit()
        
        logger.info(f"Door {available_door.door_number} allocated to package {package_id}")
        return available_door.door_number
    
    def call_robot_to_management(self, sn: str = None, map_name: str = 'map1') -> dict:
        """
        Call robot to management room to load package.
        Corresponds to step 2 in workflow.
        """
        sn = sn or self.controller.default_sn
        
        try:
            result = self.controller.custom_call(
                sn=sn,
                shop_id=None,
                map_name=map_name,
                point='management',  # 管理室位置
                call_device_name='dashboard',
                priority=2
            )
            
            logger.info(f"Robot {sn} called to management room")
            return result
        except Exception as e:
            logger.error(f"Failed to call robot: {str(e)}")
            raise
    
    def confirm_door_open(self, sn: str, door_number: str) -> dict:
        """
        Open door for manager to load package.
        Manager presses button after confirming robot arrival.
        """
        try:
            result = self.controller.control_doors(sn, door_number, operation=True)
            
            door = Door.query.filter_by(door_number=door_number, sn=sn).first()
            if door:
                door.status = DoorStatus.OPEN
                db.session.commit()
            
            logger.info(f"Door {door_number} opened for loading")
            return result
        except Exception as e:
            logger.error(f"Failed to open door: {str(e)}")
            raise
    
    def confirm_package_loaded(self, package_id: str, sn: str = None, map_name: str = 'map1') -> dict:
        """
        Confirm package is loaded and send robot.
        Manager presses '確認出發' button.
        Corresponds to step 3 in workflow.
        """
        sn = sn or self.controller.default_sn
        
        package = Package.query.get(package_id)
        if not package:
            raise ValueError(f"Package {package_id} not found")
        
        if not package.door_number:
            raise ValueError(f"Package {package_id} has no door allocated")
        
        try:
            # Close door
            self.controller.control_doors(sn, package.door_number, operation=False)
            
            door = Door.query.filter_by(door_number=package.door_number).first()
            if door:
                door.status = DoorStatus.CLOSED
                door.loading_status = LoadingStatus.LOADED
            
            # Send robot to delivery address
            result = self.controller.custom_call(
                sn=sn,
                shop_id=None,
                map_name=map_name,
                point=package.address,
                call_device_name='dashboard',
                priority=1
            )
            
            package.status = PackageStatus.DELIVERING
            package.updated_at = datetime.utcnow()
            
            history = DeliveryHistory(
                package_id=package_id,
                action='manager_confirmed_departure',
                sn=sn,
                details={'door_number': package.door_number}
            )
            
            db.session.add(history)
            db.session.commit()
            
            # Send LINE notification
            send_departed_notification(package)
            
            logger.info(f"Package {package_id} confirmed and robot sent to {package.address}")
            return result
        
        except Exception as e:
            logger.error(f"Failed to confirm package loaded: {str(e)}")
            raise
    
    def force_reset_all_doors(self, sn: str = None) -> bool:
        """
        Manager action: 一鍵開啟所有艙門來檢查。
        防呆機制 - Open all doors for checking/verification.
        """
        sn = sn or self.controller.default_sn
        
        doors = Door.query.filter_by(sn=sn).all()
        
        try:
            for door in doors:
                self.controller.control_doors(sn, door.door_number, operation=True)
                door.status = DoorStatus.OPEN
            
            db.session.commit()
            logger.info(f"All doors forced open for checking")
            return True
        
        except Exception as e:
            logger.error(f"Failed to force open doors: {str(e)}")
            return False
    
    def correct_door_state(self, package_id: str, door_number: str) -> bool:
        """
        Manager corrects door state after manual inspection.
        """
        try:
            # Close door
            sn = self.controller.default_sn
            self.controller.control_doors(sn, door_number, operation=False)
            
            door = Door.query.filter_by(door_number=door_number).first()
            if door:
                door.status = DoorStatus.CLOSED
                door.loading_status = LoadingStatus.EMPTY
                door.package_id = None
                door.address = None
            
            package = Package.query.get(package_id)
            if package:
                package.status = PackageStatus.RETURNED_CANCELLED
                package.updated_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"Door {door_number} corrected after inspection")
            return True
        
        except Exception as e:
            logger.error(f"Failed to correct door state: {str(e)}")
            return False
    
    def get_task_queue(self) -> dict:
        """Get current task queue for dashboard."""
        pending = Package.query.filter_by(status=PackageStatus.PICKUP_NOW).all()
        delivering = Package.query.filter_by(status=PackageStatus.DELIVERING).all()
        later = Package.query.filter_by(status=PackageStatus.LATER).all()
        
        return {
            'pending_orders': [
                {
                    'id': p.id,
                    'address': p.address,
                    'phone': p.phone_number,
                    'status': p.status
                } for p in pending
            ],
            'delivering_orders': [
                {
                    'id': p.id,
                    'address': p.address,
                    'door': p.door_number,
                    'status': p.status,
                    'arrived_at': p.arrived_at.isoformat() if p.arrived_at else None
                } for p in delivering
            ],
            'later_orders': [
                {
                    'id': p.id,
                    'address': p.address,
                    'status': p.status
                } for p in later
            ]
        }
