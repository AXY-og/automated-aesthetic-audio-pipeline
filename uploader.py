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
    - Opens browser for consent only on first run.
    Returns an authorized YouTube API service object.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing expired token...")
            creds.refresh(Request())
        else:
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

    body = {
        "snippet": {
            "title": metadata["title"],
            "description": metadata["description"],
            "tags": metadata["tags"],
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
