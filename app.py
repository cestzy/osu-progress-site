import os
import requests
import psycopg2
import json
import csv
import io
import traceback
from flask import Flask, redirect, request, session, url_for, render_template, make_response, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")

# --- CONFIGURATION ---
CLIENT_ID = os.environ.get("OSU_CLIENT_ID")
CLIENT_SECRET = os.environ.get("OSU_CLIENT_SECRET")
# NOTE: Update this to your Render/Vercel URL callback when deploying
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://127.0.0.1:5000/callback") 
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- DATABASE HELPERS ---
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    """Initializes the database tables if they don't exist. (V6: Final Schema)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Create Tables
        cur.execute("""
            CREATE TABLE IF NOT EXISTS osu_users (
                user_id BIGINT PRIMARY KEY, 
                username TEXT, 
                global_rank INT
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_mastery (
                user_id BIGINT PRIMARY KEY, 
                nm_rating FLOAT DEFAULT 0, 
                hd_rating FLOAT DEFAULT 0, 
                hr_rating FLOAT DEFAULT 0, 
                dt_rating FLOAT DEFAULT 0, 
                fl_rating FLOAT DEFAULT 0
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_active_goals (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                title TEXT, 
                current_progress INT, 
                target_progress INT, 
                criteria JSONB, 
                display_order INT, 
                is_completed BOOLEAN DEFAULT FALSE,
                is_locked BOOLEAN DEFAULT FALSE, 
                is_paused BOOLEAN DEFAULT FALSE,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            );
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS score_history (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT, 
                osu_score_id BIGINT, 
                beatmap_name TEXT, 
                mods TEXT, 
                mod_combination TEXT,
                stars FLOAT, 
                effective_stars FLOAT, 
                accuracy FLOAT, 
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_fc BOOLEAN DEFAULT FALSE,
                is_pfc BOOLEAN DEFAULT FALSE,
                beatmap_id BIGINT,
                map_length INT,
                max_combo INT
            );
        """)
        
        # Table to track which scores contributed to which goals
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
        
        # Add columns if they don't exist (for existing databases)
        try:
            cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS mod_combination TEXT;")
            cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS beatmap_id BIGINT;")
            cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS map_length INT;")
            cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS max_combo INT;")
            cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS is_fc BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE score_history ADD COLUMN IF NOT EXISTS is_pfc BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE user_active_goals ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;")
        except:
            pass  # Columns might already exist
        
        conn.commit()
        cur.close()
        conn.close()
        print(">>> Database initialized successfully.")
    except Exception as e:
        print(f">>> Database initialization failed: {e}")

def save_user_to_db(user_data):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Update/Insert User
    sql = """
    INSERT INTO osu_users (user_id, username, global_rank)
    VALUES (%s, %s, %s)
    ON CONFLICT (user_id) 
    DO UPDATE SET username = EXCLUDED.username, global_rank = EXCLUDED.global_rank;
    """
    rank = user_data['statistics'].get('global_rank') or 0
    cur.execute(sql, (user_data['id'], user_data['username'], rank))
    
    # 2. Ensure Mastery Row Exists
    cur.execute("INSERT INTO user_mastery (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING;", (user_data['id'],))
    
    conn.commit()
    cur.close()
    conn.close()

def calculate_effective_stars(stars, acc, max_combo, map_max_combo):
    if map_max_combo and map_max_combo > 0:
        combo_ratio = max_combo / map_max_combo
    else:
        combo_ratio = 1.0
    return stars * (acc ** 3) * combo_ratio

# --- MAIN ROUTES ---

@app.route('/')
def home():
    if 'user_id' not in session:
        return render_template('login.html')

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. Fetch User Info and refresh rank from API
        token = session.get('token')
        if token:
            try:
                headers = {'Authorization': f'Bearer {token}'}
                user_response = requests.get('https://osu.ppy.sh/api/v2/me/osu', headers=headers)
                if user_response.status_code == 200:
                    user_data = user_response.json()
                    current_rank = user_data['statistics'].get('global_rank') or 0
                    # Update rank in database
                    cur.execute("UPDATE osu_users SET global_rank = %s WHERE user_id = %s", (current_rank, session['user_id']))
                    conn.commit()
                else:
                    # Fallback to database rank
                    cur.execute("SELECT username, global_rank FROM osu_users WHERE user_id = %s", (session['user_id'],))
                    user_row = cur.fetchone()
                    current_rank = user_row[1] if user_row and user_row[1] else 0
            except:
                # Fallback to database rank
                cur.execute("SELECT username, global_rank FROM osu_users WHERE user_id = %s", (session['user_id'],))
                user_row = cur.fetchone()
                current_rank = user_row[1] if user_row and user_row[1] else 0
        else:
            cur.execute("SELECT username, global_rank FROM osu_users WHERE user_id = %s", (session['user_id'],))
            user_row = cur.fetchone()
            current_rank = user_row[1] if user_row and user_row[1] else 0

        # SAFETY CHECK: If user is in session (cookies) but not in DB, force logout
        cur.execute("SELECT username FROM osu_users WHERE user_id = %s", (session['user_id'],))
        user_row = cur.fetchone()
        if not user_row:
            cur.close()
            conn.close()
            session.clear()
            return redirect('/')

        # 2. Fetch Mastery Stats
        cur.execute("SELECT nm_rating, hd_rating, hr_rating, dt_rating, fl_rating FROM user_mastery WHERE user_id = %s", (session['user_id'],))
        stats = cur.fetchone()
        if not stats: stats = (0, 0, 0, 0, 0)

        # 3. Fetch Active Goals
        cur.execute("""
            SELECT id, title, current_progress, target_progress, criteria, is_locked, is_paused
            FROM user_active_goals 
            WHERE user_id = %s AND is_completed = FALSE
            ORDER BY display_order ASC, assigned_at DESC
        """, (session['user_id'],))
        active_rows = cur.fetchall()
        
        formatted_goals = []
        for row in active_rows:
            # FIX: Handle NULL/None values for current_progress
            current_prog = row[2] if row[2] is not None else 0
            
            formatted_goals.append({
                "id": row[0],
                "title": row[1],
                "current_count": current_prog, 
                "count_needed": row[3],
                "criteria": row[4],
                "is_locked": row[5],
                "is_paused": row[6],
                "type": row[4].get('type', 'count').upper()
            })

        # 4. Fetch Star Counts (V6: Strict FCs only)
        cur.execute("""
            SELECT FLOOR(stars) as star_int, COUNT(*) 
            FROM score_history 
            WHERE user_id = %s AND is_fc = TRUE
            GROUP BY star_int 
            ORDER BY star_int
        """, (session['user_id'],))
        hist_rows = cur.fetchall()
        star_data = {int(r[0]): r[1] for r in hist_rows}

        # 5. Fetch persistent feed (last 100 scores)
        cur.execute("""
            SELECT beatmap_name, mod_combination, stars, is_fc, timestamp
            FROM score_history 
            WHERE user_id = %s 
            ORDER BY timestamp DESC 
            LIMIT 100
        """, (session['user_id'],))
        persistent_feed = []
        for row in cur.fetchall():
            persistent_feed.append({
                'title': row[0],
                'mod_combination': row[1] or 'NM',
                'stars': round(row[2], 2),
                'is_fc': row[3],
                'timestamp': row[4].isoformat() if row[4] else ''
            })

        # 6. Fetch Completed Goals
        cur.execute("""
            SELECT id, title, current_progress, target_progress, criteria, COALESCE(completed_at, assigned_at) as completed_at
            FROM user_active_goals 
            WHERE user_id = %s AND is_completed = TRUE
            ORDER BY COALESCE(completed_at, assigned_at) DESC
        """, (session['user_id'],))
        completed_rows = cur.fetchall()
        
        completed_goals = []
        for row in completed_rows:
            completed_goals.append({
                "id": row[0],
                "title": row[1],
                "current_count": row[2] if row[2] is not None else 0,
                "count_needed": row[3],
                "criteria": row[4],
                "completed_at": row[5],  # Using assigned_at as completion time for now
                "type": row[4].get('type', 'count').upper() if row[4] else 'COUNT'
            })

        cur.close()
        conn.close()

        user_obj = {
            'username': session['username'],
            'avatar_url': f"https://a.ppy.sh/{session['user_id']}",
            'id': session['user_id']
        }

        return render_template('index.html', 
                               user=user_obj, 
                               rank=current_rank,
                               goals=formatted_goals,
                               completed_goals=completed_goals,
                               stats=stats,
                               star_data=star_data,
                               persistent_feed=persistent_feed)
    except Exception as e:
        # Debugging: Print error to console for Render Logs
        print(f"Error in home route: {e}")
        traceback.print_exc()
        return f"App Error: {e}", 500

# --- GOAL MANAGEMENT ROUTES ---

@app.route('/add_goal', methods=['POST'])
def add_goal():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    
    try:
        # 1. Safe conversions
        try:
            count = int(data.get('count_needed', 1))
        except (ValueError, TypeError):
            count = 1

        try:
            min_stars = float(data.get('target_stars', 0))
        except (ValueError, TypeError):
            min_stars = 0.0
        
        goal_type = data.get('type', 'count')
        use_acc = data.get('use_accuracy', False)
        
        try:
            acc_needed = float(data.get('accuracy_needed', 0)) if use_acc else 0
        except (ValueError, TypeError):
            acc_needed = 0.0

        # V6: New Mod Field - now always uses mod combination from checkboxes
        use_mod_combo = data.get('use_mod_combo', True)  # Default to True since we always use checkboxes now
        mod_combination = data.get('mod_combination', 'NM')  # Default to NM if not provided
        beatmap_id = data.get('beatmap_id', None)
        beatmap_name = data.get('beatmap_name', None)
        use_length = data.get('use_length', False)
        use_combo = data.get('use_combo', False)
        use_stars = data.get('use_stars', False)  # Check if stars checkbox is enabled
        
        try:
            map_length = int(data.get('map_length', 0)) if use_length else 0
        except (ValueError, TypeError):
            map_length = 0
            
        try:
            min_combo = int(data.get('min_combo', 0)) if use_combo else 0
        except (ValueError, TypeError):
            min_combo = 0

        # 2. Build Criteria JSON
        criteria = {
            "type": goal_type,
            "min_stars": min_stars if use_stars else 0,  # Only enforce if checkbox is checked
            "mod": 'Any',  # Not used anymore, always use mod_combination
            "mod_combination": mod_combination if mod_combination else 'NM',  # Always set, default to NM
            "use_acc": use_acc,
            "acc_needed": acc_needed,
            "beatmap_id": int(beatmap_id) if beatmap_id else None,
            "beatmap_name": beatmap_name,
            "use_length": use_length,
            "map_length": map_length,
            "use_combo": use_combo,
            "min_combo": min_combo,
            "streak": False 
        }

        # 3. Generate Title
        title = data.get('title')
        if not title:
            if beatmap_name:
                title = f"FC {beatmap_name}"
            else:
                title = f"{min_stars}★+ {goal_type.upper()}"

        conn = get_db_connection()
        cur = conn.cursor()
        
        # 4. Get max order
        cur.execute("SELECT MAX(display_order) FROM user_active_goals WHERE user_id = %s", (session['user_id'],))
        row = cur.fetchone()
        max_res = row[0] if row else None
        new_order = (max_res + 1) if max_res is not None else 0

        # 5. Insert Goal (Ensuring start at 0)
        cur.execute("""
            INSERT INTO user_active_goals (
                user_id, title, current_progress, target_progress, criteria, display_order, is_locked, is_paused
            )
            VALUES (%s, %s, 0, %s, %s, %s, FALSE, FALSE)
        """, (session['user_id'], title, count, json.dumps(criteria), new_order))
        
        conn.commit()
        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"ERROR adding goal: {e}")
        if 'conn' in locals(): conn.rollback()
        return jsonify({'error': 'Internal Error', 'details': str(e)}), 500
        
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@app.route('/update_goal_status', methods=['POST'])
def update_goal_status():
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    goal_id = data.get('goal_id')
    action = data.get('action') # 'delete', 'lock', 'unlock', 'pause', 'unpause'
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if action == 'delete':
        cur.execute("DELETE FROM user_active_goals WHERE id = %s AND user_id = %s", (goal_id, session['user_id']))
    elif action == 'lock':
        cur.execute("UPDATE user_active_goals SET is_locked = TRUE WHERE id = %s AND user_id = %s", (goal_id, session['user_id']))
    elif action == 'unlock':
        cur.execute("UPDATE user_active_goals SET is_locked = FALSE WHERE id = %s AND user_id = %s", (goal_id, session['user_id']))
    elif action == 'pause':
        cur.execute("UPDATE user_active_goals SET is_paused = TRUE WHERE id = %s AND user_id = %s", (goal_id, session['user_id']))
    elif action == 'unpause':
        cur.execute("UPDATE user_active_goals SET is_paused = FALSE WHERE id = %s AND user_id = %s", (goal_id, session['user_id']))

    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'status': 'success'})

@app.route('/check_scores', methods=['POST'])
def check_scores():
    # V6: Returns rich JSON payload for live updates
    result = process_session_logic()
    return jsonify(result)

@app.route('/get_goal_maps', methods=['POST'])
def get_goal_maps():
    """Returns list of maps that contributed to a goal"""
    if 'user_id' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    goal_id = data.get('goal_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT sh.beatmap_name, sh.stars, sh.mod_combination, sh.timestamp, sh.is_fc
        FROM goal_contributions gc
        JOIN score_history sh ON gc.score_history_id = sh.id
        WHERE gc.goal_id = %s AND gc.user_id = %s
        ORDER BY sh.timestamp DESC
    """, (goal_id, session['user_id']))
    
    maps = []
    for row in cur.fetchall():
        maps.append({
            'name': row[0],
            'stars': round(row[1], 2),
            'mods': row[2] or 'NM',
            'timestamp': row[3].isoformat() if row[3] else '',
            'is_fc': row[4]
        })
    
    cur.close()
    conn.close()
    return jsonify({'maps': maps})

# --- DATA MANAGEMENT ---

@app.route('/settings')
def settings():
    if 'user_id' not in session: return redirect('/')
    return render_template('settings.html', username=session.get('username'), user_id=session.get('user_id'))

@app.route('/delete_account', methods=['POST', 'GET'])
def delete_account():
    if 'user_id' not in session: return jsonify({'status': 'error'}), 401
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM osu_users WHERE user_id = %s", (session['user_id'],))
    
    conn.commit()
    cur.close()
    conn.close()
    
    session.clear()
    return jsonify({'status': 'success'})

@app.route('/reorder_goals', methods=['POST'])
def reorder_goals():
    if 'user_id' not in session: return jsonify({"status": "error"})
    
    data = request.json
    new_order_ids = data.get('order', [])
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    for index, goal_id in enumerate(new_order_ids):
        cur.execute("UPDATE user_active_goals SET display_order = %s WHERE id = %s AND user_id = %s", 
                    (index, goal_id, session['user_id']))
        
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({"status": "success"})

@app.route('/export_data')
def export_data():
    if 'user_id' not in session: return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT beatmap_name, mod_combination, mods, stars, effective_stars, accuracy, is_fc, timestamp 
        FROM score_history WHERE user_id = %s ORDER BY timestamp DESC
    """, (session['user_id'],))
    rows = cur.fetchall()
    
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Map Name', 'Mod Combination', 'Mod Group', 'Stars', 'Effective Stars', 'Accuracy', 'Is FC', 'Date'])
    # Format rows for CSV
    formatted_rows = []
    for row in rows:
        formatted_rows.append([
            row[0],  # beatmap_name
            row[1] or 'NM',  # mod_combination
            row[2] or 'NM',  # mods (mod_group)
            row[3],  # stars
            row[4],  # effective_stars
            f"{row[5]*100:.2f}%" if row[5] else "0%",  # accuracy as percentage
            'Yes' if row[6] else 'No',  # is_fc
            row[7].strftime('%Y-%m-%d %H:%M:%S') if row[7] else ''  # timestamp
        ])
    cw.writerows(formatted_rows)
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=osu_tracker_export.csv"
    output.headers["Content-type"] = "text/csv"
    
    cur.close()
    conn.close()
    return output

@app.route('/reset_history')
def reset_history():
    if 'user_id' not in session: return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    user_id = session['user_id']
    # Delete goal contributions first (due to foreign key)
    cur.execute("""
        DELETE FROM goal_contributions 
        WHERE user_id = %s
    """, (user_id,))
    cur.execute("DELETE FROM score_history WHERE user_id = %s", (user_id,))
    cur.execute("""
        UPDATE user_mastery 
        SET nm_rating=0, hd_rating=0, hr_rating=0, dt_rating=0, fl_rating=0 
        WHERE user_id = %s
    """, (user_id,))
    cur.execute("""
        UPDATE user_active_goals 
        SET current_progress = 0, is_completed = FALSE, completed_at = NULL 
        WHERE user_id = %s
    """, (user_id,))
    
    conn.commit()
    cur.close()
    conn.close()
    return redirect('/settings')

# --- SESSION ENGINE (V6 Logic) ---

def process_session_logic():
    if 'user_id' not in session: return {"status": "error", "message": "Not logged in"}
    token = session.get('token') 
    if not token: return {"status": "error", "message": "Token expired"}

    headers = {'Authorization': f'Bearer {token}'}
    try:
        # V6: Limit to 20 plays for efficiency
        response = requests.get(f'https://osu.ppy.sh/api/v2/users/{session["user_id"]}/scores/recent?include_fails=0&limit=20', headers=headers)
        
        if response.status_code != 200: return {"status": "error", "message": "API Error"}
            
        recent_scores = response.json()
        new_feed_items = []
        updates_made = False
        
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id, current_progress, target_progress, criteria, is_paused FROM user_active_goals WHERE user_id = %s AND is_completed = FALSE", (session['user_id'],))
        active_goals = cur.fetchall()

        for score in reversed(recent_scores):
            osu_score_id = score['id']
            
            # V6: Duplication check
            cur.execute("SELECT id FROM score_history WHERE osu_score_id = %s", (osu_score_id,)) 
            if cur.fetchone(): continue

            updates_made = True
            
            beatmap = score['beatmap']
            beatmapset = score['beatmapset']
            stars = beatmap['difficulty_rating']
            acc = score['accuracy']
            raw_mods = score['mods']
            
            # Convert mods array to string combination (e.g., ["HD", "DT"] -> "HDDT")
            # Sort mods alphabetically for consistent matching (HDDT, HRHD, etc.)
            if isinstance(raw_mods, list):
                # Filter out empty strings and sort
                mod_list = sorted([m for m in raw_mods if m])
                mod_combination = ''.join(mod_list) if mod_list else 'NM'
            else:
                mod_combination = raw_mods if raw_mods else 'NM'
            if not mod_combination or mod_combination == '[]': mod_combination = 'NM'
            
            # Get map_max_combo first (needed for strict FC check)
            map_max_combo = beatmap.get('max_combo', 0)
            if map_max_combo == 0: map_max_combo = score['max_combo']
            
            # Get map length and beatmap_id
            map_length = beatmap.get('total_length', 0)  # in seconds
            beatmap_id = beatmap.get('id', 0)
            
            # Get score rank and statistics
            score_rank = score.get('rank', '')
            statistics = score.get('statistics', {})
            miss_count = statistics.get('miss_count', 0)
            count_100 = statistics.get('count_100', 0)
            count_50 = statistics.get('count_50', 0)
            count_300 = statistics.get('count_300', 0)
            
            # PFC (Perfect Full Combo) Logic:
            # PFC = max_combo exactly matches map_max_combo AND no misses
            # Can be S rank or SS rank, can have 100s/50s (missing slider ends)
            # The key is: combo matches exactly, meaning no slider breaks occurred
            is_pfc = (miss_count == 0 and 
                     map_max_combo > 0 and 
                     score['max_combo'] == map_max_combo)
            
            # FC (Full Combo) Logic (Community Definition):
            # True FC = no misses, no slider breaks, only missing slider ends
            # FC scores that lost combo only via dropped slider ends (100s/50s) are widely 
            # considered by the community to be full combos, even if max_combo < map_max_combo.
            # This differs from the game client and website's display.
            #
            # The challenge: Distinguishing dropped slider ends from slider breaks
            # - Dropped slider ends: combo is close to map max (e.g., 370/371, 1130/1159) = FC
            # - Slider break: combo is significantly lower = NOT FC
            #
            # Algorithm:
            # 1. F rank or has misses = NOT FC
            # 2. If combo matches map max exactly = PFC (and FC)
            # 3. If combo is close to map max (within threshold) = FC (dropped slider ends)
            # 4. If combo is significantly lower = NOT FC (slider break)
            #
            # Threshold: Use percentage-based approach with reasonable limits
            # Examples: 370/371 (0.27% off) = FC, 1130/1159 (2.5% off) = FC
            # Use 3% or 30 combo, whichever is smaller, to account for larger maps
            
            is_fc = False
            if score_rank == 'F':
                # F rank = Failed, never FC
                is_fc = False
            elif miss_count > 0:
                # Has misses = NOT FC
                is_fc = False
            elif map_max_combo > 0:
                combo_diff = map_max_combo - score['max_combo']
                
                if combo_diff == 0:
                    # Combo matches exactly = PFC (and FC)
                    is_fc = True
                else:
                    # Calculate threshold: 3% of map max combo, or 30 combo, whichever is smaller
                    # This handles cases like 370/371 (1 off) and 1130/1159 (29 off)
                    percentage_threshold = int(map_max_combo * 0.03)
                    absolute_threshold = 30
                    threshold = min(percentage_threshold, absolute_threshold)
                    
                    if combo_diff <= threshold:
                        # Combo is close to map max (likely dropped slider ends) = FC
                        is_fc = True
                    else:
                        # Combo is significantly lower (likely slider break) = NOT FC
                        is_fc = False
            else:
                # Can't determine (map_max_combo is 0 or invalid)
                is_fc = False

            mod_group = "NM"
            if "DT" in raw_mods or "NC" in raw_mods: mod_group = "DT"
            elif "HR" in raw_mods: mod_group = "HR"
            elif "HD" in raw_mods: mod_group = "HD"
            elif "FL" in raw_mods: mod_group = "FL"

            eff_stars = calculate_effective_stars(stars, acc, score['max_combo'], map_max_combo)

            # Track goal contributions for this score
            goal_contributions_for_score = []

            # CHECK GOALS
            for goal in active_goals:
                g_id, g_current, g_target, g_criteria, g_is_paused = goal
                
                if g_current is None: g_current = 0

                if g_is_paused: continue
                
                # Star Check (must be >= required)
                # Note: min_stars defaults to 0, so if not set (use_stars was false), any star rating passes
                min_stars_req = g_criteria.get('min_stars', 0)
                if min_stars_req > 0 and stars < min_stars_req: continue 
                
                # Mod Check - support both single mod and mod combination
                # Priority: mod_combination > mod
                req_mod_combination = g_criteria.get('mod_combination', None)
                req_mod = g_criteria.get('mod', 'Any')
                
                if req_mod_combination and req_mod_combination != 'Any' and req_mod_combination:
                    # Check if mod combination matches exactly (case-sensitive)
                    if mod_combination != req_mod_combination: continue
                elif req_mod != 'Any' and req_mod:
                    # Single mod check - must match mod_group
                    if req_mod != mod_group: continue

                # Map-specific goal check (must match exactly)
                req_beatmap_id = g_criteria.get('beatmap_id', None)
                if req_beatmap_id is not None:
                    if beatmap_id != int(req_beatmap_id): continue
                
                # Map length check (must be >= required)
                if g_criteria.get('use_length', False):
                    req_length = int(g_criteria.get('map_length', 0))
                    if map_length < req_length: continue
                
                # Combo check (must be >= required)
                if g_criteria.get('use_combo', False):
                    req_combo = int(g_criteria.get('min_combo', 0))
                    if score['max_combo'] < req_combo: continue

                # Accuracy Check (must be >= required)
                if g_criteria.get('use_acc', False):
                    required_acc = float(g_criteria.get('acc_needed', 0))
                    if (acc * 100) < required_acc: continue
                

                req_type = g_criteria.get('type', 'count')
                success = False
                
                if req_type == 'pass':
                    success = (score['rank'] != 'F')
                elif req_type == 'fc':
                    success = is_fc # Use osu! FC logic (SS rank or combo matches map max)
                elif req_type == 'ss':
                    if score['rank'] in ['X', 'XH']:
                        success = True
                elif req_type == 'count':
                     success = True

                if success:
                    new_prog = g_current + 1
                    completed = (new_prog >= g_target)
                    if completed:
                        # Set completed_at timestamp when goal is completed
                        cur.execute("""
                            UPDATE user_active_goals 
                            SET current_progress = %s, is_completed = %s, completed_at = CURRENT_TIMESTAMP 
                            WHERE id = %s AND completed_at IS NULL
                        """, (new_prog, completed, g_id))
                        # If already completed, just update progress
                        if cur.rowcount == 0:
                            cur.execute("UPDATE user_active_goals SET current_progress = %s WHERE id = %s", (new_prog, g_id))
                    else:
                        cur.execute("UPDATE user_active_goals SET current_progress = %s, is_completed = %s WHERE id = %s", (new_prog, completed, g_id))
                    
                    # Track which score contributed to this goal
                    goal_contributions_for_score.append(g_id)
                else:
                    if g_criteria.get('streak', False):
                        cur.execute("UPDATE user_active_goals SET current_progress = 0 WHERE id = %s", (g_id,))

            # Save History (includes is_fc, is_pfc, mod_combination, beatmap_id, map_length, max_combo)
            cur.execute("""
                INSERT INTO score_history (user_id, osu_score_id, beatmap_name, mods, mod_combination, stars, effective_stars, accuracy, is_fc, is_pfc, beatmap_id, map_length, max_combo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (session['user_id'], osu_score_id, beatmapset['title'], mod_group, mod_combination, stars, eff_stars, acc, is_fc, is_pfc, beatmap_id, map_length, score['max_combo']))
            
            score_history_id = cur.fetchone()[0]
            
            # Track goal contributions
            for g_id in goal_contributions_for_score:
                cur.execute("""
                    INSERT INTO goal_contributions (goal_id, score_history_id, user_id)
                    VALUES (%s, %s, %s)
                """, (g_id, score_history_id, session['user_id']))
            
            col_name = f"{mod_group.lower()}_rating"
            cur.execute(f"UPDATE user_mastery SET {col_name} = ({col_name} * 0.95) + ({eff_stars} * 0.05) WHERE user_id = %s", (session['user_id'],))
            
            # V6: Prepare feed item with mod combination
            new_feed_items.append({
                'title': beatmapset['title'], 
                'stars': round(stars, 2), 
                'rank': score['rank'], 
                'mods': mod_group, 
                'mod_combination': mod_combination,
                'is_fc': is_fc,
                'timestamp': score.get('created_at', '')
            })


        conn.commit()
        
        # V6: Fetch necessary data for live frontend update
        cur.execute("SELECT nm_rating, hd_rating, hr_rating, dt_rating, fl_rating FROM user_mastery WHERE user_id = %s", (session['user_id'],))
        new_stats = cur.fetchone()

        cur.execute("SELECT id, current_progress, target_progress FROM user_active_goals WHERE user_id = %s AND is_completed = FALSE", (session['user_id'],))
        goal_states = [{'id': r[0], 'current': r[1] if r[1] is not None else 0, 'target': r[2]} for r in cur.fetchall()]

        cur.execute("""SELECT FLOOR(stars) as star_int, COUNT(*) FROM score_history WHERE user_id = %s AND is_fc = TRUE GROUP BY star_int ORDER BY star_int""", (session['user_id'],))
        fc_counts = {int(r[0]): r[1] for r in cur.fetchall()}

        # Fetch persistent feed (last 100 scores) - BEFORE closing connection
        cur.execute("""
            SELECT beatmap_name, mod_combination, stars, is_fc, timestamp
            FROM score_history 
            WHERE user_id = %s 
            ORDER BY timestamp DESC 
            LIMIT 100
        """, (session['user_id'],))
        persistent_feed = []
        for row in cur.fetchall():
            persistent_feed.append({
                'title': row[0],
                'mod_combination': row[1] or 'NM',
                'stars': round(row[2], 2),
                'is_fc': row[3]
            })
        
        cur.close()
        conn.close()
        
        # V6: Return rich JSON payload
        return { 
            "status": "success", 
            "updated": updates_made, 
            "feed": new_feed_items,
            "persistent_feed": persistent_feed,
            "stats": list(new_stats) if new_stats else [0,0,0,0,0],
            "goals": goal_states,
            "fc_counts": fc_counts
        }
        
    except Exception as e:
        print(f"Session Error: {e}")
        return {"status": "error", "message": str(e)}

# --- AUTH ROUTES ---

@app.route('/login')
def login():
    osu_auth_url = f"https://osu.ppy.sh/oauth/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=public identify"
    return redirect(osu_auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code: return "Error: No code"

    token_url = "https://osu.ppy.sh/oauth/token"
    data = { 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET, 'code': code, 'grant_type': 'authorization_code', 'redirect_uri': REDIRECT_URI }
    response = requests.post(token_url, data=data)
    tokens = response.json()
    access_token = tokens.get('access_token')

    headers = {'Authorization': f'Bearer {access_token}'}
    me_response = requests.get('https://osu.ppy.sh/api/v2/me/osu', headers=headers)
    user_data = me_response.json()

    save_user_to_db(user_data)

    session['user_id'] = user_data['id']
    session['username'] = user_data['username']
    session['rank'] = user_data['statistics'].get('global_rank')
    session['token'] = access_token
    
    return redirect('/')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# --- INITIALIZE DB ON START ---
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)