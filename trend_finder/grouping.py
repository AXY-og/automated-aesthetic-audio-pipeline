import re
import os
import sys
import database

def levenshtein_distance(s1, s2):
    """Calculates Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
            
    return previous_row[-1]

def levenshtein_similarity(s1, s2):
    """Calculates similarity ratio between 0.0 and 1.0 using Levenshtein distance."""
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - (levenshtein_distance(s1, s2) / max_len)

def clean_full_title(title):
    """Strips hashtags and standard punctuation from a full title."""
    if not title:
        return ""
    title = title.lower()
    # Strip hashtags
    title = re.sub(r'#\S*', '', title)
    # Remove non-alphanumeric (except spaces)
    title = re.sub(r'[^\w\s]', '', title)
    return re.sub(r'\s+', ' ', title).strip()

def strip_boilerplate(text, config):
    """Strips all format, genre, and generic descriptors from a string."""
    if not text:
        return ""
    text = text.lower()
    
    # Remove parenthesized / bracketed text (usually contains format details like [official video])
    text = re.sub(r'[\(\[][^\)\]]*(official|video|lyric|lyrics|audio|slowed|reverb|8d|music|clip|prod|remix|hd|4k)[^\)\]]*[\)\]]', '', text)
    text = re.sub(r'[\(\[][^\)\]]*(edit|amv|sad|gym|car|sigma|motivation)[^\)\]]*[\)\]]', '', text)
    
    # Strip feat./ft. details
    text = re.sub(r'\s*(feat\.?|ft\.?|featuring)\s+.*', '', text)
    
    # Clean standard noise double quotes / brackets
    text = text.replace('"', '').replace('“', '').replace('”', '').replace('【', '').replace('】', '')
    
    # Strip hashtags
    text = re.sub(r'#\S*', '', text)
    
    # Strip blocklist phrases/words
    denylist = config.get("genre_denylist_phrases", []) + config.get("generic_word_blocklist", [])
    # Sort denylist by length descending to strip longer subphrases before individual words
    denylist = sorted(list(set(denylist)), key=len, reverse=True)
    
    for phrase in denylist:
        pattern = r'\b' + re.escape(phrase.lower()) + r'\b'
        text = re.sub(pattern, '', text)
        
    # Replace remaining separators with spaces
    text = re.sub(r'[\|/:\-_•\.]', ' ', text)
    
    # Remove non-alphanumeric (except spaces)
    text = re.sub(r'[^\w\s]', '', text)
    
    # Clean extra whitespaces
    return re.sub(r'\s+', ' ', text).strip()

def is_generic_only(title, config):
    """Checks if a title consists entirely of generic/blocklist words."""
    cleaned = clean_full_title(title)
    denylist = config.get("genre_denylist_phrases", []) + config.get("generic_word_blocklist", [])
    denylist = sorted(list(set(denylist)), key=len, reverse=True)
    
    for phrase in denylist:
        pattern = r'\b' + re.escape(phrase.lower()) + r'\b'
        cleaned = re.sub(pattern, '', cleaned)
        
    cleaned = re.sub(r'\s+', '', cleaned).strip()
    return len(cleaned) == 0

def extract_song_details(title, description, config):
    """
    Splits the title on all delimiters (| , - , : , •).
    Evaluates every segment, strips boilerplate, and keeps the best segment.
    """
    delimiters = ['|', '-', ':', '•']
    # Check if any delimiter exists
    has_delimiter = any(d in title for d in delimiters)
    
    # Create regex pattern for splitting on any delimiter
    delim_pattern = '[' + ''.join(re.escape(d) for d in delimiters) + ']'
    segments = re.split(delim_pattern, title)
    
    # We clean and evaluate each segment
    valid_segments = []
    
    label_only = config.get("label_only_words", ["song name", "song", "audio", "music", "track", "sound"])
    
    for seg in segments:
        # Strip boilerplate and check generic words
        seg_clean = strip_boilerplate(seg, config)
        if not seg_clean:
            continue
            
        # Check if it's pure hashtags
        if seg_clean.startswith('#'):
            continue
            
        # Check if it matches a label-only word
        if seg_clean.lower() in [w.lower() for w in label_only]:
            continue
            
        # If it is generic only
        if is_generic_only(seg, config):
            continue
            
        valid_segments.append(seg_clean)
        
    # Take the best one: longest non-generic text as default rule
    song_name = ""
    if valid_segments:
        song_name = max(valid_segments, key=len)
        
    return song_name, has_delimiter

def videos_match(v1, v2, config):
    """Applies stricter matching rules (a) and (b) to determine if two videos share the same audio."""
    sim_threshold_song = config.get("song_match_similarity_threshold", 0.85)
    sim_threshold_title = config.get("title_duplicate_similarity_threshold", 0.90)
    
    # Rule (a): Song-name strings match at >= 85%
    if v1["song_name"] and v2["song_name"]:
        if levenshtein_similarity(v1["song_name"], v2["song_name"]) >= sim_threshold_song:
            return True
            
    # Rule (b): Neither had a delimiter, and full titles match at >= 90%
    if not v1["has_delimiter"] and not v2["has_delimiter"]:
        if v1["cleaned_title"] and v2["cleaned_title"]:
            if levenshtein_similarity(v1["cleaned_title"], v2["cleaned_title"]) >= sim_threshold_title:
                return True
                
    return False

def group_candidates_by_audio(config):
    """
    Groups all tracked candidates by their extracted audio keys.
    Filters out regional/unresolved/generic-only candidates.
    """
    print("\n=== PHASE 3: AUDIO GROUPING ===")
    
    # Clear old audio groups to avoid stale entries
    database.clear_audio_groups()
    
    tracking_window_days = config.get("tracking_window_days", 10)
    # Query only candidates flagged as 'international'
    candidates = database.get_active_candidates(
        tracking_window_days, 
        international_only=config.get("international_only", True)
    )
    
    if not candidates:
        print("[Grouping] No active candidates to group.")
        return
        
    print(f"[Grouping] Processing {len(candidates)} active candidates...")
    
    parsed_candidates = []
    unresolved_count = 0
    
    for c in candidates:
        # Exclude if candidate is generic-only
        if is_generic_only(c["title"], config):
            database.update_candidate_language(c["video_id"], language_flag="unresolved", exclusion_reason="generic_only")
            database.update_candidate_audio_key(c["video_id"], None)
            print(f"  [Unresolved] Video {c['video_id']} (generic_only) - Title: '{c['title']}'")
            unresolved_count += 1
            continue
            
        song_name, has_delimiter = extract_song_details(c["title"], c["description"], config)
        cleaned_title = clean_full_title(c["title"])
        
        # Check if unresolved
        is_unresolved = False
        if has_delimiter and not song_name:
            is_unresolved = True
        elif not has_delimiter and not song_name and not cleaned_title:
            is_unresolved = True
            
        if is_unresolved:
            database.update_candidate_language(c["video_id"], language_flag="unresolved", exclusion_reason="no_clean_audio_key")
            database.update_candidate_audio_key(c["video_id"], None)
            print(f"  [Unresolved] Video {c['video_id']} (no_clean_audio_key) - Title: '{c['title']}'")
            unresolved_count += 1
            continue
            
        parsed_candidates.append({
            "video_id": c["video_id"],
            "title": c["title"],
            "first_seen_at": c["first_seen_at"],
            "song_name": song_name,
            "has_delimiter": has_delimiter,
            "cleaned_title": cleaned_title
        })
        
    print(f"  ↳ Filtered out {unresolved_count} unresolved candidates.")
    
    # 2. Cluster candidates
    groups = []
    
    for pc in parsed_candidates:
        matched_group = None
        for g in groups:
            representative = g["videos"][0]
            if videos_match(pc, representative, config):
                matched_group = g
                break
                
        if matched_group:
            matched_group["videos"].append(pc)
            if pc["first_seen_at"] < matched_group["first_seen_at"]:
                matched_group["first_seen_at"] = pc["first_seen_at"]
        else:
            # Canonical key is song_name if present, otherwise cleaned_title
            canonical_key = pc["song_name"] if pc["song_name"] else pc["cleaned_title"]
            groups.append({
                "canonical_key": canonical_key,
                "first_seen_at": pc["first_seen_at"],
                "videos": [pc]
            })
            
    # 3. Update candidates with canonical key and save groups in database
    for g in groups:
        key = g["canonical_key"]
        
        # Update all candidate rows in the group
        for pc in g["videos"]:
            database.update_candidate_audio_key(pc["video_id"], key, matched_segment=pc["song_name"])
            
        # For breadth last cycle, fetch existing group
        existing = database.get_audio_group(key)
        breadth_last = existing["breadth"] if existing else 0
        breadth = len(g["videos"])
        
        database.save_audio_group(
            audio_key=key,
            first_seen_at=g["first_seen_at"],
            composite_score=existing["composite_score"] if existing else 0.0,
            breadth=breadth,
            breadth_last_cycle=breadth_last
        )
        
    print(f"  ↳ Grouped candidates into {len(groups)} unique audio keys.")
