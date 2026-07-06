import os
import sys
from datetime import datetime
import database

def parse_iso_datetime(dt_str):
    """Parses ISO 8601 datetime strings, handling Z and offset suffixes."""
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1]
    # Remove milliseconds if present
    if "." in dt_str:
        dt_str = dt_str.split(".")[0]
    return datetime.fromisoformat(dt_str)

def get_hours_difference(t1, t2):
    """Returns absolute difference in hours between two datetime objects."""
    diff = t2 - t1
    return max(0.01, diff.total_seconds() / 3600.0)

def calculate_video_velocity(video, channel):
    """
    Computes view velocity (views per hour) normalized by channel baseline views.
    """
    snapshots = database.get_snapshots_for_video(video["video_id"])
    if not snapshots:
        return 0.0
        
    latest = snapshots[-1]
    first = snapshots[0]
    
    # Calculate views per hour
    hours_between = get_hours_difference(parse_iso_datetime(first["captured_at"]), parse_iso_datetime(latest["captured_at"]))
    
    if hours_between > 0.5:
        # We have multiple snapshots spaced out: calculate interval velocity
        views_diff = latest["view_count"] - first["view_count"]
        views_per_hour = max(0.0, views_diff / hours_between)
    else:
        # Fallback to absolute lifetime velocity
        published_at = parse_iso_datetime(video["published_at"])
        hours_since_pub = get_hours_difference(published_at, parse_iso_datetime(latest["captured_at"]))
        views_per_hour = latest["view_count"] / hours_since_pub

    # Normalize by channel baseline
    channel_avg = channel["avg_views_per_video"] if channel else None
    if not channel_avg or channel_avg < 100.0:
        # Baseline fallback if channel avg views is zero or too small
        channel_avg = 1000.0
        
    normalized_velocity = views_per_hour / channel_avg
    return normalized_velocity

def score_all_audio_groups(config):
    """
    Calculates velocity for all videos, groups them by audio_key,
    computes composite scores, and updates audio_groups table.
    """
    print("\n=== PHASE 4: SCORING ENGINE ===")
    
    # 1. Fetch config settings
    weights = config.get("scoring_weights", {
        "velocity": 0.4,
        "breadth_trend": 0.35,
        "recency": 0.15,
        "saturation_penalty": 0.10
    })
    sat_threshold = config.get("saturation_threshold_videos", 45)
    tracking_window_days = config.get("tracking_window_days", 10)
    
    # 2. Get active candidates
    candidates = database.get_active_candidates(tracking_window_days)
    if not candidates:
        print("[Scoring] No active candidates to score.")
        return
        
    # 3. Calculate velocity for each candidate video
    video_velocities = {}
    for c in candidates:
        channel = database.get_channel(c["channel_id"])
        velocity = calculate_video_velocity(c, channel)
        video_velocities[c["video_id"]] = velocity
        
    # 4. Score each group
    audio_groups = database.get_all_audio_groups()
    print(f"[Scoring] Scoring {len(audio_groups)} audio groups...")
    
    for g in audio_groups:
        key = g["audio_key"]
        videos = database.get_videos_in_group(key)
        if not videos:
            continue
            
        # A. Average velocity of videos in the group
        velocities = [video_velocities.get(v["video_id"], 0.0) for v in videos]
        avg_velocity = sum(velocities) / len(velocities) if velocities else 0.0
        
        # B. Distinct channel count (breadth)
        channels_in_group = set(v["channel_id"] for v in videos)
        breadth = len(channels_in_group)
        
        # C. Breadth trend (adoption acceleration)
        # breadth_last_cycle is stored in group row
        breadth_last = g.get("breadth_last_cycle", 0) or 0
        breadth_trend = max(0, breadth - breadth_last)
        
        # D. Age of the group
        earliest_pub_str = min(v["published_at"] for v in videos)
        earliest_pub = parse_iso_datetime(earliest_pub_str)
        age_hours = get_hours_difference(earliest_pub, datetime.utcnow())
        
        # E. Recency Bonus: 1.0 if new (<24h), decaying linearly to 0.0 by 5 days (120h)
        recency_bonus = max(0.0, 1.0 - (age_hours / 120.0))
        
        # F. Saturation Penalty: increases if breadth exceeds saturation threshold
        if breadth > sat_threshold:
            # Saturation penalty increases to 1.0 over next 50 videos
            saturation_penalty = min(1.0, (breadth - sat_threshold) / 50.0)
        else:
            saturation_penalty = 0.0
            
        # G. Calculate composite score
        # composite_score = (w1 * avg_velocity) + (w2 * breadth_trend) + (w3 * recency_bonus) - (w4 * saturation_penalty)
        score = (
            (weights.get("velocity", 0.4) * avg_velocity) +
            (weights.get("breadth_trend", 0.35) * breadth_trend) +
            (weights.get("recency", 0.15) * recency_bonus) -
            (weights.get("saturation_penalty", 0.10) * saturation_penalty)
        )
        # Bound score to prevent massive negative values
        score = max(-10.0, score)
        
        # E. Update the DB
        database.save_audio_group(
            audio_key=key,
            first_seen_at=g["first_seen_at"],
            composite_score=score,
            breadth=breadth,
            breadth_last_cycle=g["breadth_last_cycle"]
        )
        
    print("  ↳ Scoring complete.")
