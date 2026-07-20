from app.db import engine
from sqlalchemy import text

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE packages ADD COLUMN IF NOT EXISTS redispatched_at TIMESTAMP"))
    conn.execute(text("ALTER TABLE packages ADD COLUMN IF NOT EXISTS redispatched_to UUID"))
print("欄位新增完成")
