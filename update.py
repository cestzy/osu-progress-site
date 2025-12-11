import os
import psycopg2
from dotenv import load_dotenv

# Ensure environment variables are loaded (like DATABASE_URL)
load_dotenv()
DATABASE_URL = os.environ.get("DATABASE_URL")

def check_column_exists(cur, table_name, column_name):
    """Check if a column exists in a table."""
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = %s AND column_name = %s;
    """, (table_name, column_name))
    return cur.fetchone() is not None

def check_table_exists(cur, table_name):
    """Check if a table exists."""
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_name = %s;
    """, (table_name,))
    return cur.fetchone() is not None

def migrate_v5():
    """Adds the is_fc column to score_history for strict FC tracking."""
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL not found in environment variables. Please check your .env file.")
        return

    print("🔧 Running v5 Migration: Adding is_fc column...")
    print("Connecting to Neon database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Check if table exists
        if not check_table_exists(cur, 'score_history'):
            print("⚠️  Warning: score_history table does not exist. It will be created on first app run.")
            conn.commit()
            cur.close()
            conn.close()
            print("✅ v5 Migration completed (table will be created by app)")
            return

        # Check if column already exists
        if check_column_exists(cur, 'score_history', 'is_fc'):
            print("✓ Column 'is_fc' already exists in score_history")
        else:
            print("Adding 'is_fc' column to score_history...")
            cur.execute("ALTER TABLE score_history ADD COLUMN is_fc BOOLEAN DEFAULT FALSE;")
            print("✓ Column 'is_fc' added successfully")

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

    print("🔧 Running v6 Migration: Adding mod columns and goal_contributions table...")
    print("Connecting to Neon database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Check if score_history table exists
        if not check_table_exists(cur, 'score_history'):
            print("⚠️  Warning: score_history table does not exist. It will be created on first app run.")
        else:
            # Add columns to score_history
            columns_to_add = [
                ('mod_combination', 'TEXT'),
                ('beatmap_id', 'BIGINT'),
                ('map_length', 'INT'),
                ('max_combo', 'INT')
            ]
            
            for col_name, col_type in columns_to_add:
                if check_column_exists(cur, 'score_history', col_name):
                    print(f"✓ Column '{col_name}' already exists in score_history")
                else:
                    print(f"Adding '{col_name}' column to score_history...")
                    cur.execute(f"ALTER TABLE score_history ADD COLUMN {col_name} {col_type};")
                    print(f"✓ Column '{col_name}' added successfully")

        # Create goal_contributions table
        if check_table_exists(cur, 'goal_contributions'):
            print("✓ Table 'goal_contributions' already exists")
        else:
            print("Creating goal_contributions table...")
            cur.execute("""
                CREATE TABLE goal_contributions (
                    id SERIAL PRIMARY KEY,
                    goal_id INT,
                    score_history_id INT,
                    user_id BIGINT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (goal_id) REFERENCES user_active_goals(id) ON DELETE CASCADE
                );
            """)
            print("✓ Table 'goal_contributions' created successfully")

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

    print("🔧 Running v7 Migration: Adding completed_at column...")
    print("Connecting to Neon database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Check if table exists
        if not check_table_exists(cur, 'user_active_goals'):
            print("⚠️  Warning: user_active_goals table does not exist. It will be created on first app run.")
            conn.commit()
            cur.close()
            conn.close()
            print("✅ v7 Migration completed (table will be created by app)")
            return

        # Add completed_at column
        if check_column_exists(cur, 'user_active_goals', 'completed_at'):
            print("✓ Column 'completed_at' already exists in user_active_goals")
        else:
            print("Adding 'completed_at' column to user_active_goals...")
            cur.execute("ALTER TABLE user_active_goals ADD COLUMN completed_at TIMESTAMP;")
            print("✓ Column 'completed_at' added successfully")

        # For existing completed goals, set completed_at to assigned_at if not already set
        cur.execute("""
            SELECT COUNT(*) 
            FROM user_active_goals 
            WHERE is_completed = TRUE AND completed_at IS NULL;
        """)
        count = cur.fetchone()[0]
        
        if count > 0:
            print(f"Updating {count} existing completed goals with completion timestamps...")
            cur.execute("""
                UPDATE user_active_goals 
                SET completed_at = assigned_at 
                WHERE is_completed = TRUE AND completed_at IS NULL;
            """)
            print(f"✓ Updated {count} completed goals")
        else:
            print("✓ No completed goals need updating")

        conn.commit()
        cur.close()
        conn.close()
        print("✅ v7 Database Schema Updated Successfully!")

    except psycopg2.Error as e:
        print(f"❌ PostgreSQL Error occurred: {e}")
        print("Check if your DATABASE_URL is correct and accessible.")
    except Exception as e:
        print(f"❌ General Error occurred: {e}")

def verify_schema():
    """Verify that all required columns and tables exist."""
    if not DATABASE_URL:
        print("❌ ERROR: DATABASE_URL not found in environment variables.")
        return False

    print("\n🔍 Verifying database schema...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # Check score_history columns
        score_history_columns = ['mod_combination', 'beatmap_id', 'map_length', 'max_combo', 'is_fc']
        print("\nChecking score_history table:")
        if check_table_exists(cur, 'score_history'):
            for col in score_history_columns:
                exists = check_column_exists(cur, 'score_history', col)
                status = "✓" if exists else "✗"
                print(f"  {status} {col}")
        else:
            print("  ⚠️  Table does not exist (will be created on first app run)")

        # Check user_active_goals columns
        print("\nChecking user_active_goals table:")
        if check_table_exists(cur, 'user_active_goals'):
            exists = check_column_exists(cur, 'user_active_goals', 'completed_at')
            status = "✓" if exists else "✗"
            print(f"  {status} completed_at")
        else:
            print("  ⚠️  Table does not exist (will be created on first app run)")

        # Check goal_contributions table
        print("\nChecking goal_contributions table:")
        exists = check_table_exists(cur, 'goal_contributions')
        status = "✓" if exists else "✗"
        print(f"  {status} goal_contributions table exists")

        cur.close()
        conn.close()
        print("\n✅ Schema verification completed!")
        return True

    except psycopg2.Error as e:
        print(f"❌ PostgreSQL Error occurred: {e}")
        return False
    except Exception as e:
        print(f"❌ General Error occurred: {e}")
        return False

def migrate_all():
    """Runs all migrations in order."""
    print("=" * 60)
    print("🚀 Running all database migrations...")
    print("=" * 60)
    print()
    
    migrate_v5()
    print()
    migrate_v6()
    print()
    migrate_v7()
    
    print("\n" + "=" * 60)
    print("✅ All migrations completed!")
    print("=" * 60)
    
    # Run verification
    verify_schema()


if __name__ == "__main__":
    migrate_all()