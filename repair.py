import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")

def repair_db():
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1. Fix NULL progress
        print("Fixing NULL progress values...")
        cur.execute("UPDATE user_active_goals SET current_progress = 0 WHERE current_progress IS NULL;")
        
        # 2. Fix NULL target (just in case)
        print("Fixing NULL target values...")
        cur.execute("UPDATE user_active_goals SET target_progress = 1 WHERE target_progress IS NULL;")

        conn.commit()
        cur.close()
        conn.close()
        print("✅ Database successfully repaired! Try logging in now.")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    repair_db()