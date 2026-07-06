import os
import sys
from datetime import datetime, timedelta
from googleapiclient.discovery import build

# Add parent dir to path so we can import from uploader
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

def get_youtube_client(config):
    """Returns an authorized YouTube client using API key or OAuth fallback."""
    api_key = config.get("youtube_api_key")
    if api_key:
        print("[Auth] Using YouTube Developer API Key.")
        return build("youtube", "v3", developerKey=api_key)
    
    # Fallback to OAuth uploader.py authentication
    try:
        import uploader
        print("[Auth] No API Key found. Reusing OAuth authentication from uploader module...")
        return uploader.authenticate()
    except Exception as e:
        print(f"[Auth] Failed to load OAuth authentication: {e}")
        raise ValueError("No valid YouTube API Key or OAuth credentials found.")

def run_keyword_search(youtube, keyword, config, max_results=25):
    """
    Search for recent videos matching a seed keyword.
    Uses videoDuration=short (under 4 minutes).
    """
    print(f"[Discovery] Searching keyword: '{keyword}'...")
    # Calculate publishedAfter using config.yaml lookback window
    lookback_days = config.get("lookback_window_days", 7)
    published_after = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    order = config.get("discovery_order", "viewCount")
    
    try:
        request = youtube.search().list(
            part="snippet",
            q=keyword,
            type="video",
            videoDuration="short",
            order=order,
            publishedAfter=published_after,
            maxResults=max_results
        )
        response = request.execute()
        
        count = 0
        for item in response.get("items", []):
            video_id = item["id"]["videoId"]
            channel_id = item["snippet"]["channelId"]
            title = item["snippet"]["title"]
            description = item["snippet"]["description"]
            published_at = item["snippet"]["publishedAt"]
            
            database.save_candidate(
                video_id=video_id,
                channel_id=channel_id,
                title=title,
                description=description,
                published_at=published_at,
                discovered_via="keyword"
            )
            count += 1
            
        print(f"  ↳ Discovered {count} candidates via keyword '{keyword}'.")
    except Exception as e:
        print(f"  ⚠️ Error searching keyword '{keyword}': {e}")

def run_trending_chart(youtube, region_code, max_results=25):
    """
    Search for trending popular videos in a region.
    """
    print(f"[Discovery] Pulling trending popular videos for region: {region_code}...")
    try:
        # We fetch the mostPopular chart.
        request = youtube.videos().list(
            part="snippet",
            chart="mostPopular",
            regionCode=region_code,
            maxResults=max_results
        )
        response = request.execute()
        
        count = 0
        for item in response.get("items", []):
            video_id = item["id"]
            channel_id = item["snippet"]["channelId"]
            title = item["snippet"]["title"]
            description = item["snippet"]["description"]
            published_at = item["snippet"]["publishedAt"]
            
            # Since this is chart discovery, we save it
            database.save_candidate(
                video_id=video_id,
                channel_id=channel_id,
                title=title,
                description=description,
                published_at=published_at,
                discovered_via="chart"
            )
            count += 1
            
        print(f"  ↳ Discovered {count} candidates via trending chart in {region_code}.")
    except Exception as e:
        print(f"  ⚠️ Error pulling trending chart for region {region_code}: {e}")

def sync_channel_metadata(youtube, channel_id):
    """
    Resolves the channel uploads playlist, subscriber count, and avg views.
    Caches it in database.
    """
    cached = database.get_channel(channel_id)
    if cached:
        # Check if checked in the last week
        last_checked = datetime.fromisoformat(cached["last_checked_at"])
        if datetime.utcnow() - last_checked < timedelta(days=7):
            return cached["uploads_playlist_id"]
            
    print(f"[Discovery] Fetching/updating metadata for channel ID: {channel_id}...")
    try:
        request = youtube.channels().list(
            part="snippet,contentDetails,statistics",
            id=channel_id
        )
        response = request.execute()
        
        if not response.get("items"):
            print(f"  ⚠️ Channel {channel_id} not found on YouTube.")
            return None
            
        item = response["items"][0]
        title = item["snippet"]["title"]
        uploads_playlist_id = item["contentDetails"]["relatedPlaylists"]["uploads"]
        
        sub_count = int(item["statistics"].get("subscriberCount", 0))
        video_count = int(item["statistics"].get("videoCount", 0))
        view_count = int(item["statistics"].get("viewCount", 0))
        
        avg_views = view_count / max(1, video_count)
        
        database.save_channel(
            channel_id=channel_id,
            title=title,
            subscriber_count=sub_count,
            avg_views=avg_views,
            uploads_playlist_id=uploads_playlist_id
        )
        return uploads_playlist_id
    except Exception as e:
        print(f"  ⚠️ Error syncing channel {channel_id}: {e}")
        return None

def monitor_seed_channels(youtube, seed_channels, max_results=10):
    """
    Check the uploads playlist of seed channels to find recent videos.
    """
    print(f"[Discovery] Monitoring {len(seed_channels)} seed channels...")
    total_count = 0
    for channel_id in seed_channels:
        playlist_id = sync_channel_metadata(youtube, channel_id)
        if not playlist_id:
            continue
            
        try:
            request = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=max_results
            )
            response = request.execute()
            
            count = 0
            for item in response.get("items", []):
                video_id = item["snippet"]["resourceId"]["videoId"]
                title = item["snippet"]["title"]
                description = item["snippet"]["description"]
                published_at = item["snippet"]["publishedAt"]
                
                database.save_candidate(
                    video_id=video_id,
                    channel_id=channel_id,
                    title=title,
                    description=description,
                    published_at=published_at,
                    discovered_via="seed_channel"
                )
                count += 1
                total_count += 1
                
        except Exception as e:
            print(f"  ⚠️ Error checking playlist {playlist_id} for channel {channel_id}: {e}")
            
    print(f"  ↳ Discovered {total_count} candidates across seed channels.")

def run_discovery_phase(youtube, config):
    """Runs all discovery strategies."""
    print("\n=== PHASE 1: DISCOVERY ===")
    
    # A. Search keywords
    for keyword in config.get("seed_keywords", []):
        run_keyword_search(youtube, keyword, config)
        
    # B. Popular charts
    for region in config.get("regions", []):
        run_trending_chart(youtube, region)
        
    # C. Seed channels
    monitor_seed_channels(youtube, config.get("seed_channels", []))
