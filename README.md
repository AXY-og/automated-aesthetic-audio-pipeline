# 🎧 Automated Aesthetic Audio Pipeline

An end-to-end pipeline for creating **slowed + reverbed + 8D audio** videos and uploading them to YouTube — fully automated.

## ✨ Features

- **YouTube Audio Download** — Paste a YouTube URL to download audio in lossless WAV quality via `yt-dlp`
- **Interactive Image Cropper** — Tkinter-based GUI with drag, resize, zoom, rotation, and edge-snapping for perfect 1:1 crops
- **Audio Effects Chain** — Apply slow, reverb, and 8D audio effects with customizable parameters
- **Audio Preview** — Listen to the processed audio before combining with the image
- **Auto Pillarboxing** — All images are scaled and padded to 1920×1080 with black bars
- **Smart Metadata Generation** — Auto-generates title, description (with lyrics lookup), tags, and hashtags
- **Scheduled Uploads** — Schedule YouTube uploads at custom dates/times in New York (ET) or IST timezones
- **Playlist Management** — Automatically adds videos to a `slowed+reverbed+8d` playlist on your channel
- **YouTube Upload** — Resumable uploads with progress bar, language settings, and recording date

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) installed and on your PATH
- A [Google Cloud project](https://console.cloud.google.com/) with YouTube Data API v3 enabled

### Setup

```bash
# Clone the repo
git clone https://github.com/AXY-og/automated-aesthetic-audio-pipeline.git
cd automated-aesthetic-audio-pipeline

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Google API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services** → **Credentials**
2. Create an **OAuth 2.0 Client ID** (type: Desktop App)
3. Download the JSON and save it as `client_secrets.json` in the project root
4. Under **OAuth consent screen**, add your Google account as a **Test user**

### Usage

```bash
python pipeline.py
```

The pipeline will walk you through:

1. **YouTube URL** — paste a link to download the source audio (or use a local file in `input/`)
2. **Image Cropper** — interactive GUI to crop your cover image to 1:1
3. **Effects** — choose which effects to apply (slow, reverb, 8D) and tweak parameters
4. **Audio Preview** — listen to the result before combining
5. **Upload** — enter song/artist info, review auto-generated metadata, optionally schedule, and upload

## 📁 Project Structure

```
├── pipeline.py        # Main entry point — orchestrates all phases
├── fx.py              # Audio effects engine + video generation
├── cropper.py         # Interactive 1:1 image cropper (Tkinter GUI)
├── uploader.py        # YouTube OAuth + upload + playlist management
├── requirements.txt   # Python dependencies
├── input/             # Place your audio/image files here
└── output/            # Generated videos appear here
```

## 🎛️ Effects

| Effect  | Parameter   | Default | Description                     |
|---------|-------------|---------|--------------------------------|
| Slow    | Speed       | 0.85    | Playback speed (0.0–1.0)      |
| Reverb  | Room size   | 0.75    | Size of the reverb space       |
| Reverb  | Damping     | 0.50    | High-frequency absorption      |
| Reverb  | Wet level   | 0.35    | Reverb signal level            |
| Reverb  | Dry level   | 0.70    | Original signal level          |
| 8D      | Pan speed   | 0.10 Hz | How fast the audio pans L↔R   |

## 📦 Dependencies

- `pedalboard` — Audio effects processing
- `yt-dlp` — YouTube audio downloading
- `Pillow` — Image manipulation for the cropper
- `google-api-python-client` — YouTube Data API
- `google-auth-oauthlib` — OAuth 2.0 authentication
- `tkinter` — GUI for image cropping (usually bundled with Python)
- `ffmpeg` — Media processing (system dependency)

## 📄 License

This project is for personal/educational use. Respect copyright — always credit the original artists.
