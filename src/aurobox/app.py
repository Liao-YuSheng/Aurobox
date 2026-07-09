"""Flask app factory."""

import os
from flask import Flask, jsonify
from .models import db
from .config import load_config

def create_app(config=None):
    """Create and configure Flask app."""
    app = Flask(__name__)
    
    # 配置資料庫
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
    
    # 初始化資料庫
    db.init_app(app)
    
    # 建立表單 (只會建立 Door)
    with app.app_context():
        db.create_all()
    
    # 註冊 API 藍圖 (不再註冊 webhooks)
    from .api import api_bp
    
    app.register_blueprint(api_bp, url_prefix='/api')

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