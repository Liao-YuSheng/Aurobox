#!/usr/bin/env python
"""Run the Aurobox Flask application."""

import os
import sys
import argparse
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_path))

from aurobox.app import create_app
from aurobox.config import load_config, require_config


def main():
    parser = argparse.ArgumentParser(description='Run Aurobox Flask server')
    parser.add_argument('--host', default='127.0.0.1', help='Server host')
    parser.add_argument('--port', type=int, default=5000, help='Server port')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    # 驗證配置
    config = load_config()
    require_config(config)
    
    # 創建應用
    app = create_app(config)
    
    # 運行伺服器
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug
    )


if __name__ == '__main__':
    main()
