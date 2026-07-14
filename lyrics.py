import requests
import re
import subprocess
import tempfile, os

def get_lyrics(artist, track, duration=None):
    headers = {
        "User-Agent": "xenia-pipeline (https://github.com/AXY-og/automated-aesthetic-audio-pipeline)"
    }
    params = {"artist_name": artist, "track_name": track}
    if duration:
        params["duration"] = duration
    try:
        r = requests.get("https://lrclib.net/api/get", params=params, headers=headers, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"⚠️ Error fetching lyrics from LRCLIB: {e}")
        return None

def lrc_to_srt(lrc_text, speed_factor=1.0):
    lines = lrc_text.strip().split("\n")
    pattern = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\](.*)')
    parsed = []
    for line in lines:
        m = pattern.match(line)
        if m:
            mins, secs, text = int(m[1]), float(m[2]), m[3].strip()
            total_ms = int((mins * 60 + secs) * 1000)
            # Adjust timestamp based on speed factor
            adjusted_ms = int(total_ms / speed_factor)
            parsed.append((adjusted_ms, text))

    srt_blocks = []
    for i, (start_ms, text) in enumerate(parsed):
        end_ms = parsed[i + 1][0] - 50 if i + 1 < len(parsed) else start_ms + 3000
        def fmt(ms):
            h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000)
            s, ms = divmod(ms, 1000)
            return f"{h:02}:{m:02}:{s:02},{ms:03}"
        srt_blocks.append(f"{i+1}\n{fmt(start_ms)} --> {fmt(end_ms)}\n{text}")

    return "\n\n".join(srt_blocks)

def get_video_dimensions(video_path):
    """Get the width and height of a video file using ffprobe."""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            video_path
        ], capture_output=True, text=True, check=True)
        w, h = result.stdout.strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1920, 1080

def get_encoding_profile(width):
    if width >= 3840:
        return {"bitrate": "60M", "maxrate": "75M", "bufsize": "150M"}
    elif width >= 2560:
        return {"bitrate": "35M", "maxrate": "45M", "bufsize": "90M"}
    else:
        return {"bitrate": "20M", "maxrate": "25M", "bufsize": "50M"}

def burn_subtitles_from_lrc(video_path, lrc_text, output_path, speed_factor=1.0):
    # Check if FFmpeg has subtitles filter support
    try:
        res = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
        if "subtitles" not in res.stdout:
            print("\n❌ Error: Your FFmpeg installation does not support the 'subtitles' filter.")
            print("This usually happens when FFmpeg is installed without 'libass' support.\n")
            print("To fix this on macOS using Homebrew, please run:")
            print("  brew uninstall ffmpeg")
            print("  brew tap homebrew-ffmpeg/ffmpeg")
            print("  brew install homebrew-ffmpeg/ffmpeg/ffmpeg")
            print()
            return False
    except Exception as e:
        print(f"❌ Error checking FFmpeg installation: {e}")
        return False

    srt_content = lrc_to_srt(lrc_text, speed_factor=speed_factor)

    # write SRT to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write(srt_content)
        srt_path = f.name

    try:
        # Prepare the subtitles filter string with correct quoting/escaping
        safe_path = srt_path.replace("\\", "/").replace(":", "\\:")
        filter_str = f"subtitles='{safe_path}':force_style='FontName=Helvetica,FontSize=18,PrimaryColour=&H0000ffff,OutlineColour=&H00000000,Outline=1,Alignment=2,Italic=1,MarginV=45'"
        
        w, h = get_video_dimensions(video_path)
        profile = get_encoding_profile(w)

        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vf", filter_str,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-b:v", profile["bitrate"],
            "-maxrate", profile["maxrate"],
            "-bufsize", profile["bufsize"],
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            "-colorspace", "bt709",
            "-color_trc", "bt709",
            "-color_primaries", "bt709",
            output_path
        ], check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ FFmpeg failed with exit code {e.returncode}")
        return False
    finally:
        if os.path.exists(srt_path):
            os.remove(srt_path)  # clean up after FFmpeg is done


def main():
    artist = input("Artist: ")
    title = input("title track: ")

    output_dir = "output"
    files = []
    if os.path.exists(output_dir):
        # Get all .mp4 files that aren't already subbed
        files = [f for f in os.listdir(output_dir) if f.lower().endswith(".mp4") and not f.lower().endswith("_subbed.mp4")]
        files.sort()

        path = None
        if not files:
            print(f"\n⚠️ No video files (.mp4) found in '{output_dir}/'.")
            raw_path = input("Enter exact name of video file manually: ").strip()
            if raw_path:
                path = raw_path
        else:
            print("\nAvailable video files in output/:")
            for idx, f in enumerate(files, 1):
                print(f"  {idx}) {f}")
            
            while True:
                try:
                    sel = input(f"Select a video file (1-{len(files)}): ").strip()
                    sel_idx = int(sel) - 1
                    if 0 <= sel_idx < len(files):
                        path = files[sel_idx]
                        break
                    else:
                        print(f"Please enter a number between 1 and {len(files)}.")
                except ValueError:
                    print("Invalid input. Please enter a valid number.")

        if path:
            result = get_lyrics(artist, title)
            if result and "syncedLyrics" in result:
                print("\nFetched lyrics:")
                print(result["syncedLyrics"])
                
                try:
                    speed_raw = input("\nEnter playback speed factor (e.g. 0.85 for slowed, Enter for 0.85): ").strip()
                    speed_factor = float(speed_raw) if speed_raw else 0.85
                except ValueError:
                    print("Invalid float entered. Defaulting to 0.85.")
                    speed_factor = 0.85

                # Build clean output path without double extensions
                if path.lower().endswith(".mp4"):
                    out_name = path[:-4] + "_subbed.mp4"
                else:
                    out_name = path + "_subbed.mp4"

                in_video_path = os.path.join(output_dir, path)
                out_video_path = os.path.join(output_dir, out_name)

                burn_subtitles_from_lrc(in_video_path, result["syncedLyrics"], out_video_path, speed_factor=speed_factor)
            else:
                print("Could not fetch synced lyrics for the song.")
        else:
            print("No video file selected. Exiting.")


if __name__ == "__main__":
    main()
