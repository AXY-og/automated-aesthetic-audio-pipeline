import os
import sys
import json
import csv
from datetime import datetime
import database

def generate_reports(config):
    """
    Generates a ranked Markdown report and a JSON summary of the top trending audio groups.
    """
    print("\n=== PHASE 5: REPORT GENERATION ===")
    
    # 1. Fetch scored audio groups and keep only those that have active candidate videos
    all_groups = database.get_all_audio_groups()
    if not all_groups:
        print("[Reporter] No audio groups available to generate report.")
        return
        
    valid_groups_with_videos = []
    for g in all_groups:
        videos = database.get_videos_in_group(g["audio_key"])
        if videos:
            valid_groups_with_videos.append((g, videos))
            
    if not valid_groups_with_videos:
        print("[Reporter] No active candidates found for any audio group.")
        return

    # Filter/rank: prefer breadth >= 2 first
    trending_groups = [item for item in valid_groups_with_videos if item[0]["breadth"] >= 2]
    if not trending_groups:
        trending_groups = valid_groups_with_videos
        
    # Limit to top 20
    top_groups = trending_groups[:20]
    
    # Prepare directories
    reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%SZ")
    md_filename = f"report_{timestamp}.md"
    json_filename = f"report_{timestamp}.json"
    
    md_path = os.path.join(reports_dir, md_filename)
    json_path = os.path.join(reports_dir, json_filename)
    
    latest_md_path = os.path.join(reports_dir, "latest_report.md")
    latest_json_path = os.path.join(reports_dir, "latest_report.json")
    
    # 2. Build report data structure
    report_data = []
    
    for idx, (g, videos) in enumerate(top_groups, 1):
        key = g["audio_key"]
        
        # Sort videos in group by their view count descending.
        # To get view count, we need their latest snapshot.
        video_details = []
        for v in videos:
            snapshots = database.get_snapshots_for_video(v["video_id"])
            views = snapshots[-1]["view_count"] if snapshots else 0
            video_details.append({
                "video_id": v["video_id"],
                "title": v["title"],
                "views": views,
                "published_at": v["published_at"]
            })
            
        video_details.sort(key=lambda x: x["views"], reverse=True)
        
        # Calculate age in hours
        earliest_pub_str = min(v["published_at"] for v in videos)
        if earliest_pub_str.endswith("Z"):
            earliest_pub_str = earliest_pub_str[:-1]
        if "." in earliest_pub_str:
            earliest_pub_str = earliest_pub_str.split(".")[0]
        age_hours = (datetime.utcnow() - datetime.fromisoformat(earliest_pub_str)).total_seconds() / 3600.0
        
        group_info = {
            "rank": idx,
            "audio_key": key,
            "composite_score": round(g["composite_score"], 4),
            "breadth": g["breadth"],
            "breadth_trend": g["breadth"] - (g["breadth_last_cycle"] or 0),
            "age_hours": round(age_hours, 1),
            "examples": video_details[:5]  # top 5 videos
        }
        report_data.append(group_info)
        
    # 3. Generate Markdown Report
    md_lines = [
        "# 📈 YouTube Shorts Edit-Audio Trend Report",
        f"Generated on (UTC): `{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}`",
        "",
        "This ranked list displays rising audio/song trends based on view velocity, channel adoption breadth, and age.",
        "",
        "| Rank | Audio Key | Score | Breadth (Channels) | Breadth Trend | Est. Age (Hrs) |",
        "|---|---|---|---|---|---|",
    ]
    
    for item in report_data:
        md_lines.append(
            f"| **#{item['rank']}** | `{item['audio_key']}` | {item['composite_score']} | {item['breadth']} | +{item['breadth_trend']} | {item['age_hours']}h |"
        )
        
    md_lines.append("\n## Detailed Trend Breakdowns\n")
    
    for item in report_data:
        md_lines.append(f"### #{item['rank']}. {item['audio_key']}")
        md_lines.append(f"- **Composite Score**: `{item['composite_score']}`")
        md_lines.append(f"- **Breadth**: {item['breadth']} unique channels")
        md_lines.append(f"- **Growth Trend**: +{item['breadth_trend']} channels this cycle")
        md_lines.append(f"- **First Discovered Age**: {item['age_hours']} hours ago")
        md_lines.append("\n**Top Example Videos:**")
        
        for v in item["examples"]:
            url = f"https://www.youtube.com/watch?v={v['video_id']}"
            md_lines.append(f"  - [{v['title']}]({url}) — **{v['views']:,} views**")
        md_lines.append("\n---\n")
        
    # Write to timestamped file
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
        
    # Write to latest_report.md
    with open(latest_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
        
    # 4. Generate JSON Report
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
        
    with open(latest_json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
        
    print(f"  ✅ Reports generated successfully:")
    print(f"     ↳ Markdown: {md_path}")
    print(f"     ↳ JSON:     {json_path}")
    print(f"     ↳ Symlinked: {latest_md_path}")
