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

def migrate_v6():
    """Adds mod_combination, beatmap_id, map_length, max_combo columns to score_history and goal_contributions table."""
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL not found in environment variables. Please check your .env file.")
        return

    print("Connecting to Neon database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        print("Adding columns to score_history...")
        cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS mod_combination TEXT;")
        cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS beatmap_id BIGINT;")
        cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS map_length INT;")
        cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS max_combo INT;")

        print("Creating goal_contributions table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS goal_contributions (
                id SERIAL PRIMARY KEY,
                goal_id INT,
                score_history_id INT,
                user_id BIGINT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (goal_id) REFERENCES user_active_goals(id) ON DELETE CASCADE
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("✅ v6 Database Schema Updated Successfully!")

    except psycopg2.Error as e:
        print(f"❌ PostgreSQL Error occurred: {e}")
        print("Check if your DATABASE_URL is correct and accessible.")
    except Exception as e:
        print(f"❌ General Error occurred: {e}")

def migrate_v7():
    """Adds completed_at column to user_active_goals for tracking completion timestamps."""
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL not found in environment variables. Please check your .env file.")
        return

    print("Connecting to Neon database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        print("Adding 'completed_at' column to user_active_goals...")
        cur.execute("ALTER TABLE user_active_goals ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;")

        # For existing completed goals, set completed_at to assigned_at if not already set
        print("Updating existing completed goals...")
        cur.execute("""
            UPDATE user_active_goals 
            SET completed_at = assigned_at 
            WHERE is_completed = TRUE AND completed_at IS NULL;
        """)

        conn.commit()
        cur.close()
        conn.close()
        print("✅ v7 Database Schema Updated Successfully!")

    except psycopg2.Error as e:
        print(f"❌ PostgreSQL Error occurred: {e}")
        print("Check if your DATABASE_URL is correct and accessible.")
    except Exception as e:
        print(f"❌ General Error occurred: {e}")

def migrate_all():
    """Runs all migrations in order."""
    print("🚀 Running all database migrations...\n")
    migrate_v5()
    print()
    migrate_v6()
    print()
    migrate_v7()
    print("\n✅ All migrations completed!")


if __name__ == "__main__":
    migrate_all()