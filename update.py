import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

def migrate_v5():
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1. Add 'is_fc' to score_history for strict tracking
        print("Adding 'is_fc' to score_history...")
        cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS is_fc BOOLEAN DEFAULT FALSE;")

        conn.commit()
        cur.close()
        conn.close()
        print("✅ v5 Database Schema Updated!")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    migrate_v5()