"""Flask app factory."""

import os
from flask import Flask, jsonify
from .models import db, Door, DoorStatus
from .services import FlashbotController
from .config import load_config
from .api import api_bp
# from .tasks import _push_dashboard_status_loop
import threading

def ensure_default_doors(app: Flask) -> None:
    """Ensure default doors exist and reset them to empty at startup."""
    sn = app.config.get('ROBOT_SN')
    if not sn:
        return

    # 讀取設定檔中的 DOOR_MODE
    mode = app.config.get('DOOR_MODE', '4_DOORS')
    if mode == '3_DOORS':
        active_door_numbers = ("H_01", "H_03", "H_04")
    else:
        active_door_numbers = ("H_01", "H_02", "H_03", "H_04")

    existing_numbers = {
        row[0]
        for row in db.session.query(Door.door_number).filter_by(sn=sn).all()
    }

    missing_numbers = [
        door_number for door_number in active_door_numbers if door_number not in existing_numbers
    ]

    for door_number in missing_numbers:
        db.session.add(
            Door(
                sn=sn,
                door_number=door_number,
                status=DoorStatus.EMPTY.value,
                package_id=None,
                task_id=None
            )
        )
    
    # 重置目前的邏輯門狀態
    doors = Door.query.filter_by(sn=sn).all()
    
    for door in doors:
        door.status = DoorStatus.EMPTY.value
        door.package_id = None
        door.task_id = None

    if db.session.new or db.session.dirty:
        db.session.commit()
    
def create_app(config=None, reset_db=True):
    """Create and configure Flask app."""
    app = Flask(__name__)
    app_config = config or load_config()
    
    # 配置資料庫
    db_url = app_config.get('DATABASE_URL')
    if db_url:
        # 如果有設定 PostgreSQL，就用它
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    else:
        # 否則退回 SQLite (相容舊版開發環境)
        db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'instance')
        os.makedirs(db_path, exist_ok=True)
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}/aurobox.db'
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JSON_AS_ASCII'] = False
    
    # 載入環境配置
    app_config = config or load_config()
    app.config['PUDU_API_KEY'] = app_config.get('APP_KEY')
    app.config['PUDU_API_SECRET'] = app_config.get('APP_SECRET')
    app.config['SHOP_ID'] = app_config.get('SHOP_ID')
    app.config['ROBOT_SN'] = app_config.get('DEFAULT_SN')
    app.config['DEFAULT_MAP_NAME'] = app_config.get('DEFAULT_MAP_NAME')
    app.config['HOME_POINT_NAME'] = app_config.get('HOME_POINT_NAME')
    app.config['CHARGE_POINT_NAME'] = app_config.get('CHARGE_POINT_NAME')
    app.config['CENTRAL_API_BASE_URL'] = app_config.get('CENTRAL_API_BASE_URL')

    app.pudu_controller = FlashbotController(app_config)
    app.home_point = app_config.get('HOME_POINT_NAME')
    app.charge_point = app_config.get('CHARGE_POINT_NAME')

    # 初始化資料庫
    db.init_app(app)
    
    # 建立表單 (只會建立 Door)
    with app.app_context():
        db.create_all()
        if reset_db:
            ensure_default_doors(app)
    
    # 註冊 API 藍圖 (不再註冊 webhooks)
    app.register_blueprint(api_bp, url_prefix='/api')
    '''
    push_thread = threading.Thread(
        target=_push_dashboard_status_loop,
        args=(app,), 
        daemon=True # 設定 daemon=True，這樣 Flask 關閉時執行緒也會跟著乾淨關閉
    )
    push_thread.start()
    '''
    @app.get('/')
    def index():
        return jsonify({
            'service': 'aurobox-flashbot-hardware',
            'status': 'ok',
            'endpoints': {
                'healthz': '/healthz',
                'dashboard_status': '/api/dashboard/status'
            }
        })

    @app.get('/healthz')
    def healthz():
        return jsonify({'status': 'ok'})

    @app.get('/favicon.ico')
    def favicon():
        return '', 204
    
    return app