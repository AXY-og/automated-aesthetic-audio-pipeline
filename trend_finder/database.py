import sqlite3
import os
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")

def get_connection():
    """Returns a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes database tables if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Channels table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            title TEXT,
            subscriber_count INTEGER,
            avg_views_per_video REAL,
            uploads_playlist_id TEXT,
            last_checked_at TEXT
        )
        """)
        
        # Candidates table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            video_id TEXT PRIMARY KEY,
            channel_id TEXT,
            title TEXT,
            description TEXT,
            published_at TEXT,
            duration_seconds INTEGER,
            discovered_via TEXT,
            audio_key TEXT,
            first_seen_at TEXT,
            language_flag TEXT DEFAULT 'unresolved',
            exclusion_reason TEXT,
            matched_segment TEXT
        )
        """)
        
        # Migration: Add columns to candidates if they don't exist
        cursor.execute("PRAGMA table_info(candidates)")
        columns = [row[1] for row in cursor.fetchall()]
        if "language_flag" not in columns:
            cursor.execute("ALTER TABLE candidates ADD COLUMN language_flag TEXT DEFAULT 'unresolved'")
        if "exclusion_reason" not in columns:
            cursor.execute("ALTER TABLE candidates ADD COLUMN exclusion_reason TEXT")
        if "matched_segment" not in columns:
            cursor.execute("ALTER TABLE candidates ADD COLUMN matched_segment TEXT")
        
        # Snapshots table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT,
            captured_at TEXT,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER
        )
        """)
        
        # Audio groups table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audio_groups (
            audio_key TEXT PRIMARY KEY,
            first_seen_at TEXT,
            last_scored_at TEXT,
            composite_score REAL,
            breadth INTEGER,
            breadth_last_cycle INTEGER
        )
        """)
        
        conn.commit()
    print("[Database] Initialized successfully.")

def save_channel(channel_id, title, subscriber_count, avg_views, uploads_playlist_id):
    """Inserts or updates a channel in the database."""
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("""
        INSERT INTO channels (channel_id, title, subscriber_count, avg_views_per_video, uploads_playlist_id, last_checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title=excluded.title,
            subscriber_count=excluded.subscriber_count,
            avg_views_per_video=excluded.avg_views_per_video,
            uploads_playlist_id=excluded.uploads_playlist_id,
            last_checked_at=excluded.last_checked_at
        """, (channel_id, title, subscriber_count, avg_views, uploads_playlist_id, now))
        conn.commit()

def get_channel(channel_id):
    """Fetches a cached channel by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,)).fetchone()
        return dict(row) if row else None

def save_candidate(video_id, channel_id, title, description, published_at, discovered_via, duration_seconds=None, audio_key=None):
    """Inserts a discovered candidate if it does not already exist."""
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("""
        INSERT INTO candidates (video_id, channel_id, title, description, published_at, duration_seconds, discovered_via, audio_key, first_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            duration_seconds=COALESCE(excluded.duration_seconds, duration_seconds),
            audio_key=COALESCE(excluded.audio_key, audio_key)
        """, (video_id, channel_id, title, description, published_at, duration_seconds, discovered_via, audio_key, now))
        conn.commit()

def update_candidate_details(video_id, duration_seconds, audio_key=None):
    """Updates confirmed duration and/or audio key on a candidate."""
    with get_connection() as conn:
        if audio_key is not None:
            conn.execute("""
            UPDATE candidates 
            SET duration_seconds = ?, audio_key = ? 
            WHERE video_id = ?
            """, (duration_seconds, audio_key, video_id))
        else:
            conn.execute("""
            UPDATE candidates 
            SET duration_seconds = ? 
            WHERE video_id = ?
            """, (duration_seconds, video_id))
        conn.commit()

def update_candidate_audio_key(video_id, audio_key, matched_segment=None):
    """Updates or clears the audio key and matched segment on a candidate."""
    with get_connection() as conn:
        conn.execute("""
        UPDATE candidates 
        SET audio_key = ?, matched_segment = ? 
        WHERE video_id = ?
        """, (audio_key, matched_segment, video_id))
        conn.commit()

def clear_audio_groups():
    """Deletes all rows from the audio_groups table to prevent stale groups."""
    with get_connection() as conn:
        conn.execute("DELETE FROM audio_groups")
        conn.commit()

def update_candidate_language(video_id, language_flag, exclusion_reason=None):
    """Updates candidate language flag and exclusion reason."""
    with get_connection() as conn:
        conn.execute("""
        UPDATE candidates 
        SET language_flag = ?, exclusion_reason = ? 
        WHERE video_id = ?
        """, (language_flag, exclusion_reason, video_id))
        conn.commit()

def save_snapshot(video_id, view_count, like_count, comment_count):
    """Adds a metrics snapshot row."""
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("""
        INSERT INTO snapshots (video_id, captured_at, view_count, like_count, comment_count)
        VALUES (?, ?, ?, ?, ?)
        """, (video_id, now, view_count, like_count, comment_count))
        conn.commit()

def get_active_candidates(window_days, international_only=False):
    """Returns candidates discovered/seen within the tracking window."""
    query = """
    SELECT * FROM candidates 
    WHERE datetime(first_seen_at) >= datetime('now', ?)
    """
    if international_only:
        query += " AND language_flag = 'international'"
        
    with get_connection() as conn:
        rows = conn.execute(query, (f"-{window_days} days",)).fetchall()
        return [dict(r) for r in rows]

def get_unconfirmed_candidates():
    """Returns candidate videos whose duration has not been confirmed yet."""
    with get_connection() as conn:
        rows = conn.execute("""
        SELECT * FROM candidates 
        WHERE duration_seconds IS NULL
        """).fetchall()
        return [dict(r) for r in rows]

def get_snapshots_for_video(video_id):
    """Returns all snapshots for a video ordered by captured_at."""
    with get_connection() as conn:
        rows = conn.execute("""
        SELECT * FROM snapshots 
        WHERE video_id = ? 
        ORDER BY captured_at ASC
        """, (video_id,)).fetchall()
        return [dict(r) for r in rows]

def save_audio_group(audio_key, first_seen_at, composite_score, breadth, breadth_last_cycle):
    """Inserts or updates an audio group."""
    now = datetime.utcnow().isoformat()
    with get_connection() as conn:
        conn.execute("""
        INSERT INTO audio_groups (audio_key, first_seen_at, last_scored_at, composite_score, breadth, breadth_last_cycle)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(audio_key) DO UPDATE SET
            last_scored_at=excluded.last_scored_at,
            composite_score=excluded.composite_score,
            breadth=excluded.breadth,
            breadth_last_cycle=excluded.breadth_last_cycle
        """, (audio_key, first_seen_at, now, composite_score, breadth, breadth_last_cycle))
        conn.commit()

def get_audio_group(audio_key):
    """Fetches an audio group by key."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM audio_groups WHERE audio_key = ?", (audio_key,)).fetchone()
        return dict(row) if row else None

def get_all_audio_groups():
    """Fetches all audio groups."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM audio_groups ORDER BY composite_score DESC").fetchall()
        return [dict(r) for r in rows]

def get_videos_in_group(audio_key):
    """Fetches all candidates tagged with this audio_key."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM candidates WHERE audio_key = ?", (audio_key,)).fetchall()
        return [dict(r) for r in rows]

def purge_old_data(window_days):
    """Deletes candidates and snapshots older than tracking window to bound storage."""
    cutoff = f"-{window_days} days"
    with get_connection() as conn:
        # Delete old snapshots
        conn.execute("""
        DELETE FROM snapshots 
        WHERE datetime(captured_at) < datetime('now', ?)
        """, (cutoff,))
        # Delete old candidates
        conn.execute("""
        DELETE FROM candidates 
        WHERE datetime(first_seen_at) < datetime('now', ?)
        """, (cutoff,))
        conn.commit()
    print("[Database] Purged old tracking data successfully.")
