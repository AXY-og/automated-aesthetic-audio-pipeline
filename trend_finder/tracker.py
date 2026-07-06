import re
import os
import sys
import sqlite3
from datetime import datetime, timedelta
import database
import discovery

def parse_iso8601_duration(duration_str):
    """Parses ISO 8601 duration string (e.g. PT1M23S) to total seconds."""
    pattern = re.compile(r'P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?')
    match = pattern.match(duration_str)
    if not match:
        return 0
    hours = int(match.group(1)) if match.group(1) else 0
    minutes = int(match.group(2)) if match.group(2) else 0
    seconds = int(match.group(3)) if match.group(3) else 0
    return hours * 3600 + minutes * 60 + seconds

def check_language_exclusion(title, description, snippet_langs, config):
    """
    Checks if a video's metadata flags it as Indian Regional content.
    Returns (language_flag, exclusion_reason) where language_flag is 'international' or 'indian_regional'.
    """
    if not config.get("international_only", True):
        return "international", None
        
    title_lower = title.lower() if title else ""
    desc_lower = description.lower() if description else ""
    
    # 1. Script check (Devanagari, Gurmukhi, Tamil, Telugu, Bengali)
    script_patterns = {
        "devanagari": r'[\u0900-\u097F]',
        "gurmukhi": r'[\u0A00-\u0A7F]',
        "tamil": r'[\u0B80-\u0BFF]',
        "telugu": r'[\u0C00-\u0C7F]',
        "bengali": r'[\u0980-\u09FF]',
    }
    
    excluded_scripts = config.get("excluded_scripts", ["devanagari", "gurmukhi", "tamil", "telugu", "bengali"])
    for script in excluded_scripts:
        pattern = script_patterns.get(script)
        if pattern:
            if re.search(pattern, title or "") or re.search(pattern, description or ""):
                return "indian_regional", f"script_match ({script})"
                
    # 2. Metadata check
    excluded_langs = config.get("excluded_language_codes", ["hi", "pa", "ta", "te", "bn", "mr", "gu", "kn", "ml", "ur"])
    for lang in snippet_langs:
        if lang and lang.lower()[:2] in excluded_langs:
            return "indian_regional", f"metadata_lang ({lang})"
            
    # 3. Genre/template check
    genre_phrases = config.get("genre_denylist_phrases", [
        "whatsapp status", "lyrics status", "aesthetic status", "shayari", "attitude status", "trending status"
    ])
    for phrase in genre_phrases:
        if phrase in title_lower:
            return "indian_regional", f"genre_phrase_match ({phrase})"
            
    # 4. Romanized-keyword check (requires 2+ matches)
    romanized_words = config.get("romanized_keyword_denylist", [])
    min_matches = config.get("min_romanized_matches_for_exclusion", 2)
    if romanized_words:
        matches = 0
        for word in romanized_words:
            pattern = r'\b' + re.escape(word.lower()) + r'\b'
            if re.search(pattern, title_lower):
                matches += 1
                if matches >= min_matches:
                    return "indian_regional", f"romanized_keywords_match (matches={matches})"
                    
    return "international", None

def chunk_list(lst, n):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        url_chunk = lst[i:i + n]
        yield url_chunk

def update_metrics_and_durations(youtube, config):
    """
    Fetches details and stats for all candidates that need to be tracked.
    Confirms Short status (deletes non-Shorts), syncs channel baseline,
    and captures stats snapshots.
    """
    print("\n=== PHASE 1.5 & 2: METRICS & DURATIONS TRACKING ===")
    
    def parse_iso_datetime(dt_str):
        """Parses ISO 8601 datetime strings, handling Z and offset suffixes."""
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1]
        if "." in dt_str:
            dt_str = dt_str.split(".")[0]
        return datetime.fromisoformat(dt_str)
    
    # 1. Gather all candidates that need updates.
    # This includes:
    # - New unconfirmed candidates (duration_seconds IS NULL)
    # - Active candidates tracked within the tracking window
    unconfirmed = database.get_unconfirmed_candidates()
    tracking_window_days = config.get("tracking_window_days", 10)
    active = database.get_active_candidates(tracking_window_days)
    
    # Combine unique video IDs
    video_map = {}
    for v in unconfirmed:
        video_map[v["video_id"]] = v
    for v in active:
        video_map[v["video_id"]] = v
        
    video_ids = list(video_map.keys())
    if not video_ids:
        print("[Tracker] No videos to update.")
        return
        
    print(f"[Tracker] Refreshing stats/details for {len(video_ids)} videos...")
    
    # Batch requests in chunks of 50
    max_duration = config.get("max_short_duration_seconds", 60)
    
    for chunk in chunk_list(video_ids, 50):
        ids_str = ",".join(chunk)
        try:
            request = youtube.videos().list(
                part="id,snippet,contentDetails,statistics",
                id=ids_str
            )
            response = request.execute()
            
            # Map response items by ID
            items = {item["id"]: item for item in response.get("items", [])}
            
            # Check if any candidate was deleted from YouTube
            for vid in chunk:
                if vid not in items:
                    print(f"  ↳ Video {vid} not found on YouTube (deleted/private). Removing from DB...")
                    with database.get_connection() as conn:
                        conn.execute("DELETE FROM candidates WHERE video_id = ?", (vid,))
                        conn.execute("DELETE FROM snapshots WHERE video_id = ?", (vid,))
                        conn.commit()
                    continue
                    
                item = items[vid]
                duration_str = item["contentDetails"].get("duration", "PT0S")
                duration_secs = parse_iso8601_duration(duration_str)
                
                # Check view floor
                stats = item.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                view_floor = config.get("qualifying_view_floor", 1000000)
                
                # Check publishedAt lookback window
                published_at_str = item["snippet"].get("publishedAt", "")
                if published_at_str:
                    pub_dt = parse_iso_datetime(published_at_str)
                    lookback_days = config.get("lookback_window_days", 7)
                    lookback_cutoff = datetime.utcnow() - timedelta(days=lookback_days)
                else:
                    pub_dt = None
                    lookback_cutoff = None
                
                is_qualified = True
                fail_reason = ""
                if duration_secs > max_duration:
                    is_qualified = False
                    fail_reason = f"duration {duration_secs}s > {max_duration}s"
                elif view_count < view_floor:
                    is_qualified = False
                    fail_reason = f"views {view_count} < {view_floor}"
                elif pub_dt and pub_dt < lookback_cutoff:
                    is_qualified = False
                    fail_reason = f"published {published_at_str} older than {lookback_days} days"
                    
                if not is_qualified:
                    print(f"  ↳ Video {vid} failed validation ({fail_reason}). Discarding...")
                    with database.get_connection() as conn:
                        conn.execute("DELETE FROM candidates WHERE video_id = ?", (vid,))
                        conn.execute("DELETE FROM snapshots WHERE video_id = ?", (vid,))
                        conn.commit()
                    continue
                
                # Ensure the channel stats are cached
                channel_id = item["snippet"]["channelId"]
                discovery.sync_channel_metadata(youtube, channel_id)
                
                # Save duration in DB
                database.update_candidate_details(video_id=vid, duration_seconds=duration_secs)
                
                # Perform language check and save language details
                snippet_langs = []
                snippet = item.get("snippet", {})
                if "defaultLanguage" in snippet:
                    snippet_langs.append(snippet["defaultLanguage"])
                if "defaultAudioLanguage" in snippet:
                    snippet_langs.append(snippet["defaultAudioLanguage"])
                    
                lang_flag, reason = check_language_exclusion(
                    title=snippet.get("title", ""),
                    description=snippet.get("description", ""),
                    snippet_langs=snippet_langs,
                    config=config
                )
                
                if lang_flag == "indian_regional":
                    print(f"  ↳ Video {vid} flagged as Indian Regional ({reason}). Excluding...")
                    
                database.update_candidate_language(video_id=vid, language_flag=lang_flag, exclusion_reason=reason)
                
                # Save statistics snapshot
                stats = item.get("statistics", {})
                view_count = int(stats.get("viewCount", 0))
                like_count = int(stats.get("likeCount", 0))
                comment_count = int(stats.get("commentCount", 0))
                
                database.save_snapshot(
                    video_id=vid,
                    view_count=view_count,
                    like_count=like_count,
                    comment_count=comment_count
                )
                
        except Exception as e:
            print(f"  ⚠️ Error batch-updating statistics chunk: {e}")
            
    # Purge old candidates beyond tracking window to bound database size
    database.purge_old_data(tracking_window_days)
