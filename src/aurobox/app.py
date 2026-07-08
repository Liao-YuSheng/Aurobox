"""Flask app factory."""

import os
from flask import Flask, jsonify
from sqlalchemy import text
from .models import db
from .config import load_config


def _ensure_robot_status_columns(app: Flask) -> None:
    """Add newly introduced robot_status columns for existing SQLite DB files."""
    required_columns = {
        "run_state": "TEXT",
        "task_state": "TEXT",
        "is_charging": "INTEGER",
        "charge_stage": "TEXT",
    }

    with app.app_context():
        table_exists = db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='robot_status'")
        ).fetchone()
        if not table_exists:
            return

        rows = db.session.execute(text("PRAGMA table_info(robot_status)")).fetchall()
        existing = {row[1] for row in rows}

        for column, col_type in required_columns.items():
            if column not in existing:
                db.session.execute(text(f"ALTER TABLE robot_status ADD COLUMN {column} {col_type}"))
        db.session.commit()


def _ensure_package_columns(app: Flask) -> None:
    """Add newly introduced package columns for existing SQLite DB files."""
    required_columns = {
        "pickup_qr_token": "TEXT",
    }

    with app.app_context():
        table_exists = db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='packages'")
        ).fetchone()
        if not table_exists:
            return

        rows = db.session.execute(text("PRAGMA table_info(packages)")).fetchall()
        existing = {row[1] for row in rows}

        for column, col_type in required_columns.items():
            if column not in existing:
                db.session.execute(text(f"ALTER TABLE packages ADD COLUMN {column} {col_type}"))
        db.session.commit()


def create_app(config=None):
    """Create and configure Flask app."""
    app = Flask(__name__)
    
    # 配置数据库
    db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'instance')
    os.makedirs(db_path, exist_ok=True)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}/aurobox.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['JSON_AS_ASCII'] = False
    
    # 加载环境配置
    app_config = config or load_config()
    app.config['PUDU_API_KEY'] = app_config.get('APP_KEY')
    app.config['PUDU_API_SECRET'] = app_config.get('APP_SECRET')
    app.config['SHOP_ID'] = app_config.get('SHOP_ID')
    app.config['ROBOT_SN'] = app_config.get('DEFAULT_SN')
    
    # Line Bot 配置（需要从环境变量中获取）
    app.config['LINE_CHANNEL_ACCESS_TOKEN'] = os.getenv('LINE_CHANNEL_ACCESS_TOKEN', '')
    app.config['LINE_CHANNEL_SECRET'] = os.getenv('LINE_CHANNEL_SECRET', '')
    
    # 初始化数据库
    db.init_app(app)
    
    # 创建表
    with app.app_context():
        db.create_all()
        _ensure_robot_status_columns(app)
        _ensure_package_columns(app)
    
    # 注册蓝图
    from .api import api_bp
    from .webhooks import webhook_bp
    
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(webhook_bp, url_prefix='/webhooks')

    @app.get('/')
    def index():
        return jsonify({
            'service': 'aurobox',
            'status': 'ok',
            'endpoints': {
                'healthz': '/healthz',
                'dashboard_events': '/api/dashboard/events'
            }
        })

    @app.get('/healthz')
    def healthz():
        return jsonify({'status': 'ok'})

    @app.get('/favicon.ico')
    def favicon():
        return '', 204
    
    return app
