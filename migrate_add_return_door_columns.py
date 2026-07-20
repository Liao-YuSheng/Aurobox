from app.db import engine
from sqlalchemy import text

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE packages ADD COLUMN IF NOT EXISTS returned_at TIMESTAMP"))
    conn.execute(text("ALTER TABLE packages ADD COLUMN IF NOT EXISTS return_door_opened_at TIMESTAMP"))
print("欄位新增完成")
