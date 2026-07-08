"""API routes for package management and dashboard."""

from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timedelta
from . import db
from .models import Package, Door, RobotStatus, DeliveryHistory, PackageStatus, DoorStatus, LoadingStatus
from .robot import FlashbotController
from .config import load_config
import uuid

api_bp = Blueprint('api', __name__)


def get_controller():
    """Get FlashbotController instance."""
    return FlashbotController(load_config())


# ============== Package Management APIs ==============

@api_bp.route('/packages', methods=['POST'])
def create_package():
    """
    Create a new package delivery record.
    POST /api/packages
    """
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    # 驗證必需字段
    required = ['phone_number', 'address']
    if not all(k in data for k in required):
        return jsonify({'error': f'Missing required fields: {required}'}), 400
    
    try:
        package = Package(
            id=str(uuid.uuid4()),
            phone_number=data['phone_number'],
            address=data['address'],
            status=PackageStatus.PENDING,
            notes=data.get('notes', '')
        )
        
        db.session.add(package)
        db.session.commit()
        
        return jsonify({
            'id': package.id,
            'status': package.status,
            'created_at': package.created_at.isoformat()
        }), 201
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@api_bp.route('/packages/<package_id>', methods=['GET'])
def get_package(package_id):
    """Get package details."""
    package = Package.query.get(package_id)
    
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    return jsonify({
        'id': package.id,
        'phone_number': package.phone_number,
        'address': package.address,
        'door_number': package.door_number,
        'status': package.status,
        'created_at': package.created_at.isoformat(),
        'updated_at': package.updated_at.isoformat(),
        'arrived_at': package.arrived_at.isoformat() if package.arrived_at else None,
        'completed_at': package.completed_at.isoformat() if package.completed_at else None,
    })


@api_bp.route('/packages/<package_id>/response', methods=['POST'])
def package_response(package_id):
    """
    Handle user response to package notification.
    User can choose to pickup now or pickup later.
    """
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    data = request.get_json()
    action = data.get('action')  # 'pickup_now' or 'later'
    
    if action == 'pickup_now':
        package.status = PackageStatus.PICKUP_NOW
    elif action == 'later':
        package.status = PackageStatus.LATER
    else:
        return jsonify({'error': 'Invalid action'}), 400
    
    package.updated_at = datetime.utcnow()
    db.session.commit()
    
    # 記錄歷史
    history = DeliveryHistory(
        package_id=package_id,
        action=f'user_response_{action}',
        details={'action': action}
    )
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success', 'package_status': package.status})


@api_bp.route('/packages/<package_id>/stored', methods=['POST'])
def package_stored(package_id):
    """
    Manager confirms package is loaded into door.
    Update door allocation and status.
    """
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    data = request.get_json()
    door_number = data.get('door_number')
    
    if not door_number:
        return jsonify({'error': 'door_number is required'}), 400
    
    package.door_number = door_number
    package.status = PackageStatus.DELIVERING
    package.updated_at = datetime.utcnow()
    
    # 更新門的狀態
    door = Door.query.filter_by(door_number=door_number, sn=current_app.config.get('ROBOT_SN')).first()
    if not door:
        door = Door(
            sn=current_app.config.get('ROBOT_SN'),
            door_number=door_number,
            package_id=package_id,
            address=package.address,
            loading_status=LoadingStatus.LOADED
        )
        db.session.add(door)
    else:
        door.package_id = package_id
        door.loading_status = LoadingStatus.LOADED
        door.address = package.address
    
    db.session.commit()
    
    # 記錄歷史
    history = DeliveryHistory(
        package_id=package_id,
        action='package_stored',
        details={'door_number': door_number}
    )
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success', 'door_number': door_number})


@api_bp.route('/packages/<package_id>/departed', methods=['POST'])
def package_departed(package_id):
    """Manager confirms robot departure."""
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    package.status = PackageStatus.DELIVERING
    package.updated_at = datetime.utcnow()
    
    history = DeliveryHistory(
        package_id=package_id,
        action='robot_departed',
        sn=current_app.config.get('ROBOT_SN')
    )
    
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success'})


@api_bp.route('/packages/<package_id>/arrived', methods=['POST'])
def package_arrived(package_id):
    """Robot arrived at destination."""
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    package.status = PackageStatus.ARRIVED
    package.arrived_at = datetime.utcnow()
    package.updated_at = datetime.utcnow()
    
    history = DeliveryHistory(
        package_id=package_id,
        action='robot_arrived',
        sn=current_app.config.get('ROBOT_SN')
    )
    
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success', 'arrived_at': package.arrived_at.isoformat()})


@api_bp.route('/packages/<package_id>/cancel', methods=['POST'])
def package_cancel(package_id):
    """User cancel or timeout."""
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    data = request.get_json()
    reason = data.get('reason', 'timeout')  # 'cancelled' or 'timeout'
    
    if reason == 'cancelled':
        package.status = PackageStatus.RETURNED_CANCELLED
    else:
        package.status = PackageStatus.RETURNED_TIMEOUT
    
    package.updated_at = datetime.utcnow()
    
    history = DeliveryHistory(
        package_id=package_id,
        action=f'package_cancel_{reason}',
        details={'reason': reason}
    )
    
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success', 'new_status': package.status})


@api_bp.route('/packages/<package_id>/pickup-complete', methods=['POST'])
def package_pickup_complete(package_id):
    """User scanned QR code and picked up package."""
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    package.status = PackageStatus.COMPLETED
    package.completed_at = datetime.utcnow()
    package.updated_at = datetime.utcnow()
    
    history = DeliveryHistory(
        package_id=package_id,
        action='qrcode_scanned',
    )
    
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success', 'completed_at': package.completed_at.isoformat()})


@api_bp.route('/packages/<package_id>/complete', methods=['POST'])
def package_complete(package_id):
    """User confirmed pickup and package delivery completed."""
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    package.status = PackageStatus.COMPLETED
    package.completed_at = datetime.utcnow()
    package.updated_at = datetime.utcnow()
    
    # 清空對應的艙門
    if package.door_number:
        door = Door.query.filter_by(door_number=package.door_number).first()
        if door:
            door.loading_status = LoadingStatus.EMPTY
            door.package_id = None
            door.address = None
    
    history = DeliveryHistory(
        package_id=package_id,
        action='delivery_completed',
    )
    
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success'})


@api_bp.route('/packages/<package_id>/returned', methods=['POST'])
def package_returned(package_id):
    """Robot returned to management room."""
    package = Package.query.get(package_id)
    if not package:
        return jsonify({'error': 'Package not found'}), 404
    
    history = DeliveryHistory(
        package_id=package_id,
        action='robot_returned',
        sn=current_app.config.get('ROBOT_SN')
    )
    
    db.session.add(history)
    db.session.commit()
    
    return jsonify({'status': 'success'})


# ============== Dashboard APIs ==============

@api_bp.route('/dashboard/events', methods=['GET'])
def dashboard_events():
    """
    Get real-time dashboard events.
    包括機器人狀態、艙門狀態、任務隊列等。
    """
    try:
        controller = get_controller()
        robot_status = controller.get_status()
        door_states = controller.get_door_state()
        
        # 更新或創建機器人狀態記錄
        sn = current_app.config.get('ROBOT_SN')
        robot = RobotStatus.query.filter_by(sn=sn).first()
        
        if not robot:
            robot = RobotStatus(sn=sn)
        
        data = robot_status.get('data', {})
        robot.state = data.get('state', 'idle')
        robot.battery_level = data.get('battery_level', 0)
        robot.current_location = data.get('current_location', '')
        robot.move_state = data.get('move_state', '')
        robot.updated_at = datetime.utcnow()
        
        db.session.add(robot)
        db.session.commit()
        
        # 獲取任務隊列
        pending_orders = Package.query.filter_by(status=PackageStatus.PICKUP_NOW).all()
        delivering_orders = Package.query.filter_by(status=PackageStatus.DELIVERING).all()
        later_orders = Package.query.filter_by(status=PackageStatus.LATER).all()
        
        # 獲取今日歷史記錄
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        history_orders = Package.query.filter(
            Package.status.in_([PackageStatus.COMPLETED, PackageStatus.RETURNED_CANCELLED, PackageStatus.RETURNED_TIMEOUT]),
            Package.completed_at >= today_start
        ).all()
        
        # 獲取艙門狀態
        doors = Door.query.filter_by(sn=sn).all()
        
        return jsonify({
            'robot_status': {
                'state': robot.state,
                'battery_level': robot.battery_level,
                'current_location': robot.current_location,
                'move_state': robot.move_state,
                'updated_at': robot.updated_at.isoformat()
            },
            'task_queue': {
                'pending_orders': len(pending_orders),
                'delivering_orders': len(delivering_orders),
                'later_orders': len(later_orders),
                'history_count': len(history_orders)
            },
            'door_states': [{
                'door_number': door.door_number,
                'status': door.status,
                'loading_status': door.loading_status,
                'address': door.address,
                'package_id': door.package_id
            } for door in doors],
            'pending_orders': [{
                'id': p.id,
                'address': p.address,
                'door_number': p.door_number,
                'status': p.status
            } for p in pending_orders],
            'delivering_orders': [{
                'id': p.id,
                'address': p.address,
                'door_number': p.door_number,
                'status': p.status,
                'arrived_at': p.arrived_at.isoformat() if p.arrived_at else None
            } for p in delivering_orders]
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
