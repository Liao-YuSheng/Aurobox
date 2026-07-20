from app.db import engine
from sqlalchemy import text

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE packages ADD COLUMN IF NOT EXISTS case_closed_at TIMESTAMP"))
print("欄位新增完成")