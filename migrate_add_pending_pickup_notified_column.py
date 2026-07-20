from app.db import engine
from sqlalchemy import text

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE packages ADD COLUMN IF NOT EXISTS pending_pickup_notified_at TIMESTAMP"))
print("欄位新增完成")