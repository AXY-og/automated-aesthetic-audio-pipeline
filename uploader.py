"""
YouTube upload module for Xenia Pipeline.
Handles OAuth 2.0 authentication and resumable video uploads
using the YouTube Data API v3.
"""

import os
import sys
import json
import time
import random
import httplib2
from datetime import date

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import tempfile
from PIL import Image

def get_thumbnail_path(video_path):
    """
    Locates the generated styled thumbnail (PNG or JPG) based on the video path.
    Also handles suffixes like '_subbed' or '_test' by checking clean variations.
    """
    if not video_path:
        return None
    base, _ = os.path.splitext(video_path)
    paths = [base + ".png", base + ".jpg"]
    # Handle suffixes
    for suffix in ["_subbed", "_test"]:
        if base.endswith(suffix):
            clean_base = base[:-len(suffix)]
            paths.extend([clean_base + ".png", clean_base + ".jpg"])
    # Check if any path exists
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def prepare_4k_thumbnail(thumbnail_path):
    """
    Upscales a thumbnail image to 4K (3840x2160) and saves it as a JPEG
    compressed to be under 2MB so it fits YouTube's upload constraints.
    """
    if not thumbnail_path or not os.path.exists(thumbnail_path):
        return None
    try:
        img = Image.open(thumbnail_path)
        # Upscale to 4K
        img_4k = img.resize((3840, 2160), Image.Resampling.LANCZOS)
        
        # Save to a temporary JPEG file
        temp_jpeg = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_path = temp_jpeg.name
        temp_jpeg.close()
        
        # Save as JPEG with optimized quality to stay under 2MB
        img_4k.convert("RGB").save(temp_path, "JPEG", quality=90, optimize=True)
        
        # Verify file size is under 2MB (2 * 1024 * 1024 bytes)
        file_size = os.path.getsize(temp_path)
        if file_size > 2 * 1024 * 1024:
            print(f"  ⚠️ 4K thumbnail size is {file_size / (1024*1024):.2f} MB (exceeds 2MB). Re-compressing with quality=80...")
            img_4k.convert("RGB").save(temp_path, "JPEG", quality=80, optimize=True)
            file_size = os.path.getsize(temp_path)
            
        print(f"  ✅ Prepared 4K thumbnail: {file_size / (1024*1024):.2f} MB")
        return temp_path
    except Exception as e:
        print(f"  ⚠️ Failed to prepare 4K thumbnail: {e}")
        return None

def upload_thumbnail(youtube, video_id, thumbnail_path):
    """Uploads a custom thumbnail to YouTube for the specified video."""
    print(f"  Uploading custom thumbnail: {os.path.basename(thumbnail_path)}...")
    try:
        media = MediaFileUpload(
            thumbnail_path,
            mimetype="image/jpeg"
        )
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=media
        ).execute()
        print("  ✅ Custom thumbnail uploaded successfully!")
        return True
    except Exception as e:
        print(f"  ⚠️ Custom thumbnail upload failed: {e}")
        return False

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",          # playlists
    "https://www.googleapis.com/auth/youtube.force-ssl", # playlist items
    "https://www.googleapis.com/auth/drive.file",        # Google Drive upload
]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"

# Retry config for resumable uploads
MAX_RETRIES = 10
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
RETRIABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    BrokenPipeError,
    OSError,
    httplib2.HttpLib2Error,
)


def get_credentials():
    """
    Get authorized user credentials from file, refresh them if expired,
    or run OAuth flow if not present or missing scopes.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            
            # Check if all requested scopes are present in the credentials
            has_all_scopes = True
            for scope in SCOPES:
                if not creds.scopes or scope not in creds.scopes:
                    has_all_scopes = False
                    break
            if not has_all_scopes:
                print("Existing token is missing required scopes. Triggering re-authorization...")
                creds = None
        except Exception as e:
            print(f"Failed to load credentials from token file: {e}")
            creds = None

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

        print("\nOpening browser for Google OAuth authorization...")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)

        # Save token for future runs
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print("Token saved — you won't need to authenticate again.\n")

    return creds


def authenticate():
    """
    Authenticate with YouTube via OAuth 2.0.
    Returns an authorized YouTube API service object.
    """
    creds = get_credentials()
    return build("youtube", "v3", credentials=creds)


def upload_to_drive(file_path):
    """
    Uploads a file to a Google Drive folder named 'Xenia Thumbnails'.
    Creates the folder if it does not exist.
    """
    if not file_path or not os.path.exists(file_path):
        return None
        
    print(f"  Uploading {os.path.basename(file_path)} to Google Drive...")
    try:
        creds = get_credentials()
        drive_service = build("drive", "v3", credentials=creds)
        
        # 1. Find or create the target folder
        folder_name = "Xenia Thumbnails"
        query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        folders = results.get('files', [])
        
        if folders:
            folder_id = folders[0]['id']
        else:
            print(f"  Creating Google Drive folder '{folder_name}'...")
            folder_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = drive_service.files().create(body=folder_metadata, fields='id').execute()
            folder_id = folder.get('id')
            
        # 2. Determine mime type
        mime = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
        
        # 3. Upload file
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, mimetype=mime, resumable=True)
        uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        
        print(f"  ✅ Uploaded to Google Drive: {os.path.basename(file_path)} (ID: {uploaded_file.get('id')})")
        return uploaded_file.get('id')
    except Exception as e:
        print(f"  ⚠️ Google Drive upload failed: {e}")
        return None


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
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
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
                # Reset retry count after a successful chunk upload
                retry_count = 0
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS_CODES and retry_count < MAX_RETRIES:
                retry_count += 1
                sleep_time = (2 ** retry_count) + random.uniform(0, 1)
                print(f"\n  Retryable HTTP error ({e.resp.status}), attempt {retry_count}/{MAX_RETRIES}. Retrying in {sleep_time:.2f}s...")
                time.sleep(sleep_time)
            else:
                print(f"\n\n  Upload failed: {e}")
                return None
        except RETRIABLE_EXCEPTIONS as e:
            if retry_count < MAX_RETRIES:
                retry_count += 1
                sleep_time = (2 ** retry_count) + random.uniform(0, 1)
                print(f"\n  Network/Timeout error ({type(e).__name__}: {e}), attempt {retry_count}/{MAX_RETRIES}. Retrying in {sleep_time:.2f}s...")
                time.sleep(sleep_time)
            else:
                print(f"\n\n  Upload failed after maximum retries. Last error: {type(e).__name__}: {e}")
                return None

    # Final 100%
    _progress_bar(file_size, file_size)
    print()  # newline after progress bar

    video_id = response["id"]

    # ── Upload custom 4K thumbnail & Drive Backup ──
    try:
        thumb_file = get_thumbnail_path(video_path)
        if thumb_file:
            print(f"\n  Found styled thumbnail: {os.path.basename(thumb_file)}")
            k4_thumb = prepare_4k_thumbnail(thumb_file)
            if k4_thumb:
                upload_thumbnail(youtube, video_id, k4_thumb)
                try:
                    os.remove(k4_thumb)
                except Exception:
                    pass
            
            # Upload original styled thumbnail to Google Drive folder
            upload_to_drive(thumb_file)
        else:
            print("\n  ⚠️ No styled thumbnail found to upload as custom thumbnail.")
    except Exception as e:
        print(f"\n  ⚠️ Could not upload custom thumbnail/Drive backup: {e}")

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
            import re
            from thumbnail import strip_features
            
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            if base_name.lower().endswith("_subbed"):
                base_name = base_name[:-7]
            
            # Try to guess artist and song from filename
            guessed_artist = ""
            guessed_song = ""
            if " - " in base_name:
                parts = base_name.split(" - ", 1)
                guessed_artist = parts[0].strip()
                guessed_song = parts[1].strip()
            else:
                guessed_song = base_name

            # Clean common suffixes from guessed song/artist
            clean_regex = r'\s*[\(\[][^\]\)]*(official|video|lyric|lyrics|audio|slowed|reverb|8d|music|clip|prod|remix|hd|4k)[^\]\)]*[\)\]]'
            guessed_song = re.sub(clean_regex, '', guessed_song, flags=re.IGNORECASE).strip()
            guessed_artist = re.sub(clean_regex, '', guessed_artist, flags=re.IGNORECASE).strip()
            
            # Strip features from artist
            try:
                guessed_artist = strip_features(guessed_artist)
            except Exception:
                pass

            print(f"\nPreparing auto-generated metadata for: {base_name}")
            
            artist_name = input(f"Artist Name [{guessed_artist}]: ").strip()
            if not artist_name:
                artist_name = guessed_artist
            while not artist_name:
                artist_name = input("Artist Name (required): ").strip()
                
            song_name = input(f"Song Name [{guessed_song}]: ").strip()
            if not song_name:
                song_name = guessed_song
            while not song_name:
                song_name = input("Song Name (required): ").strip()

            # Parse effects if we can detect them in base_name (e.g. slowed, reverb, 8d)
            effects = []
            lower_base = base_name.lower()
            if "slow" in lower_base:
                effects.append("slow")
            if "reverb" in lower_base:
                effects.append("reverb")
            if "8d" in lower_base:
                effects.append("8d")

            metadata = phase_metadata(
                source_url=None,
                artist_name=artist_name,
                song_name=song_name,
                effects=effects if effects else None
            )
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
