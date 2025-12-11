import os
import psycopg2
from dotenv import load_dotenv

# Ensure environment variables are loaded (like DATABASE_URL)
load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

def migrate_v5():
    """Adds the is_fc column to score_history for strict FC tracking."""
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL not found in environment variables. Please check your .env file.")
        return

    print("Connecting to Neon database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # SQL command to safely add the is_fc column if it does not exist
        print("Adding 'is_fc' column to score_history...")
        cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS is_fc BOOLEAN DEFAULT FALSE;")

        conn.commit()
        cur.close()
        conn.close()
        print("✅ v5 Database Schema Updated Successfully!")

    except psycopg2.Error as e:
        print(f"❌ PostgreSQL Error occurred: {e}")
        print("Check if your DATABASE_URL is correct and accessible.")
    except Exception as e:
        print(f"❌ General Error occurred: {e}")


if __name__ == "__main__":
    migrate_v5()