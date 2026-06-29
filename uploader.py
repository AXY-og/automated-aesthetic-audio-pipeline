"""
YouTube upload module for Xenia Pipeline.
Handles OAuth 2.0 authentication and resumable video uploads
using the YouTube Data API v3.
"""

import os
import sys
import json
import httplib2
from datetime import date

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",          # playlists
    "https://www.googleapis.com/auth/youtube.force-ssl", # playlist items
]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"

# Retry config for resumable uploads
MAX_RETRIES = 5
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


def authenticate():
    """
    Authenticate with YouTube via OAuth 2.0.
    - Loads saved token from token.json if available.
    - Refreshes expired tokens automatically.
    - Opens browser for consent only on first run or when refresh token is invalid/revoked.
    Returns an authorized YouTube API service object.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        print("Refreshing expired token...")
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"Token refresh failed: {e}. Re-authenticating...")
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(CLIENT_SECRETS_FILE):
            print(f"\nError: '{CLIENT_SECRETS_FILE}' not found.")
            print("Download it from Google Cloud Console → APIs & Services → Credentials")
            print("(Create an OAuth 2.0 Client ID of type 'Desktop App')")
            sys.exit(1)

        print("\nOpening browser for YouTube authorization...")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

        # Save token for future runs
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("Token saved — you won't need to authenticate again.\n")

    return build("youtube", "v3", credentials=creds)


def _progress_bar(current, total, width=40):
    """Render a terminal progress bar."""
    fraction = current / total if total > 0 else 0
    filled = int(width * fraction)
    bar = "█" * filled + "░" * (width - filled)
    percent = fraction * 100
    sys.stdout.write(f"\r  Uploading: |{bar}| {percent:5.1f}%")
    sys.stdout.flush()


def sanitize_tags(tags):
    """
    Sanitize and truncate tags to fit within YouTube's 500-character limit.
    Removes forbidden characters like '<', '>', and ',' and estimates the final
    serialized length including quotes for tags with spaces and commas.
    """
    cleaned_tags = []
    seen = set()
    total_len = 0
    max_len = 450  # Keep a safe margin below 500 characters
    truncated = False

    for tag in tags:
        # Remove forbidden characters and strip whitespace
        cleaned = tag.replace("<", "").replace(">", "").replace(",", "").strip()
        if not cleaned:
            continue
            
        # Deduplicate case-insensitively
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)

        # Estimate serialized length:
        # YouTube wraps tags containing spaces in double quotes (+2 characters)
        tag_len = len(cleaned)
        if " " in cleaned:
            tag_len += 2
        
        # Add 1 for the comma separator if this is not the first tag
        addition = tag_len + (1 if cleaned_tags else 0)
        
        if total_len + addition <= max_len:
            cleaned_tags.append(cleaned)
            total_len += addition
        else:
            truncated = True
            break
            
    if truncated or len(cleaned_tags) < len(tags):
        print(f"  ⚠️  Tags sanitized & truncated to fit YouTube's 500-character limit (reduced from {len(tags)} to {len(cleaned_tags)} tags).")
        
    return cleaned_tags


def upload_video(youtube, video_path, metadata):
    """
    Upload a video to YouTube using resumable upload.
    
    Args:
        youtube:    Authorized YouTube API service object.
        video_path: Path to the .mp4 file.
        metadata:   Dict with keys: title, description, tags, category_id, privacy_status.
    
    Returns:
        The YouTube video ID on success, or None on failure.
    """
    today = date.today().isoformat()       # e.g. "2026-06-11"

    # Sanitize tags to prevent invalidTags error from YouTube API
    sanitized_tags = sanitize_tags(metadata.get("tags", []))

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": sanitized_tags,
            "categoryId": metadata["category_id"],
            "defaultLanguage": "en",           # title & description language
            "defaultAudioLanguage": "en",      # audio / video language
        },
        "status": {
            "privacyStatus": metadata["privacy_status"],
            "selfDeclaredMadeForKids": False,
        },
        "recordingDetails": {
            "recordingDate": today,
        },
    }

    # Scheduled publishing (requires privacy = private)
    if metadata.get("publish_at"):
        body["status"]["publishAt"] = metadata["publish_at"]
        body["status"]["privacyStatus"] = "private"

    file_size = os.path.getsize(video_path)
    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,  # 1 MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status,recordingDetails",
        body=body,
        media_body=media,
    )

    print(f"\n  File size: {file_size / (1024*1024):.1f} MB")
    print()

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                _progress_bar(status.resumable_progress, file_size)
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES and retry_count < MAX_RETRIES:
                retry_count += 1
                print(f"\n  Retryable error ({e.resp.status}), attempt {retry_count}/{MAX_RETRIES}...")
                continue
            else:
                print(f"\n\n  Upload failed: {e}")
                return None

    # Final 100%
    _progress_bar(file_size, file_size)
    print()  # newline after progress bar

    video_id = response["id"]

    # ── Add to playlist ──
    try:
        _add_to_playlist(youtube, video_id)
    except Exception as e:
        print(f"\n  ⚠️  Could not add to playlist: {e}")

    return video_id


PLAYLIST_TITLE = "slowed+reverbed+8d"


def _find_playlist(youtube, title):
    """Find a playlist by title on the authenticated channel. Returns playlist ID or None."""
    request = youtube.playlists().list(
        part="snippet",
        mine=True,
        maxResults=50,
    )
    while request:
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"].strip().lower() == title.strip().lower():
                return item["id"]
        request = youtube.playlists().list_next(request, response)
    return None


def _create_playlist(youtube, title):
    """Create a new playlist and return its ID."""
    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": "Slowed + reverbed + 8D audio edits.",
            },
            "status": {
                "privacyStatus": "public",
            },
        },
    ).execute()
    return response["id"]


def _add_to_playlist(youtube, video_id):
    """Add a video to the slowed+reverbed+8d playlist, creating it if needed."""
    playlist_id = _find_playlist(youtube, PLAYLIST_TITLE)

    if not playlist_id:
        print(f"\n  Creating playlist '{PLAYLIST_TITLE}'...")
        playlist_id = _create_playlist(youtube, PLAYLIST_TITLE)
        print(f"  ✅ Playlist created: {playlist_id}")

    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            },
        },
    ).execute()
    print(f"  ✅ Added to playlist '{PLAYLIST_TITLE}'")


def main():
    print("=======================================================")
    print("  YOUTUBE VIDEO UPLOADER")
    print("=======================================================")

    # 1. Select video
    output_dir = "output"
    if not os.path.exists(output_dir):
        print(f"❌ Error: Directory '{output_dir}' does not exist.")
        sys.exit(1)

    videos = [f for f in os.listdir(output_dir) if f.lower().endswith(".mp4")]
    videos.sort()

    if not videos:
        print(f"⚠️ No video files (.mp4) found in '{output_dir}/'.")
        sys.exit(0)

    print("\nAvailable videos in output/:")
    for idx, f in enumerate(videos, 1):
        print(f"  {idx}) {f}")

    while True:
        try:
            sel = input(f"Select a video file (1-{len(videos)}): ").strip()
            if not sel:
                continue
            sel_idx = int(sel) - 1
            if 0 <= sel_idx < len(videos):
                video_path = os.path.join(output_dir, videos[sel_idx])
                break
            print(f"Please enter a number between 1 and {len(videos)}.")
        except ValueError:
            print("Invalid input. Please enter a valid number.")

    print(f"\nSelected video: {os.path.basename(video_path)}")

    # 2. Prepare metadata
    print("\nHow would you like to prepare the upload metadata?")
    print("  1) Auto-generate (uses standard template with lyrics & links)")
    print("  2) Manual input (Title, Description, Tags)")

    while True:
        choice = input("Select option (1 or 2): ").strip()
        if choice in ("1", "2"):
            break
        print("Please enter 1 or 2.")

    metadata = {}
    if choice == "1":
        try:
            from pipeline import phase_metadata
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            if base_name.lower().endswith("_subbed"):
                base_name = base_name[:-7]
            print(f"\nPreparing auto-generated metadata for: {base_name}")
            metadata = phase_metadata(source_url=None)
        except Exception as e:
            print(f"⚠️ Error running auto-generator: {e}")
            print("Falling back to manual input...")
            choice = "2"

    if choice == "2":
        print("\n--- Manual Metadata Entry ---")
        title = input("Video Title: ").strip()
        while not title:
            title = input("Video Title (required): ").strip()

        print("\nEnter Description (press Enter twice when done):")
        desc_lines = []
        while True:
            line = input()
            if line == "" and desc_lines and desc_lines[-1] == "":
                desc_lines.pop()
                break
            desc_lines.append(line)
        description = "\n".join(desc_lines).strip()

        tags_raw = input("\nEnter Tags (comma separated): ").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        print("\nSelect Privacy Status:")
        print("  1) Public")
        print("  2) Unlisted")
        print("  3) Private")
        while True:
            p_choice = input("Enter 1, 2, or 3 [default 2]: ").strip()
            if not p_choice:
                privacy = "unlisted"
                break
            elif p_choice == "1":
                privacy = "public"
                break
            elif p_choice == "2":
                privacy = "unlisted"
                break
            elif p_choice == "3":
                privacy = "private"
                break
            print("Please enter 1, 2, or 3.")

        publish_at = None
        try:
            from pipeline import _prompt_schedule
            publish_at = _prompt_schedule()
        except ImportError:
            pass

        metadata = {
            "title": title,
            "description": description,
            "tags": tags,
            "category_id": "10",  # Music
            "privacy_status": privacy,
        }
        if publish_at:
            metadata["publish_at"] = publish_at
            metadata["privacy_status"] = "private"

    # 3. Authenticate and Upload
    print("\nAuthenticating with YouTube...")
    youtube = authenticate()

    print(f"\nUploading: {os.path.basename(video_path)}")
    video_id = upload_video(youtube, video_path, metadata)

    if video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
        print("\n" + "=" * 55)
        print(f"  ✅ Upload complete!")
        print(f"  🔗 {url}")
        print("=" * 55 + "\n")
    else:
        print("\n❌ Upload failed. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
