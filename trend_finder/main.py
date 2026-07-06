import os
import sys
import argparse
import yaml

# Add current folder to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import database
import discovery
import tracker
import grouping
import scoring
import reporter

def load_config(config_path):
    """Loads configuration from YAML file."""
    if not os.path.exists(config_path):
        print(f"⚠️ Config file not found at {config_path}. Creating default config.")
        # Return default config dict
        return {
            "seed_keywords": ["edit", "slowed reverb"],
            "seed_channels": [],
            "regions": ["IN", "US"],
            "max_short_duration_seconds": 60,
            "tracking_window_days": 10,
            "cycle_hours": 6,
            "scoring_weights": {
                "velocity": 0.4,
                "breadth_trend": 0.35,
                "recency": 0.15,
                "saturation_penalty": 0.10
            },
            "saturation_threshold_videos": 45
        }
        
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    parser = argparse.ArgumentParser(description="YouTube Shorts Edit-Trend Finder")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--discover", action="store_true", help="Run discovery phase only")
    parser.add_argument("--track", action="store_true", help="Run metrics/duration tracking only")
    parser.add_argument("--group", action="store_true", help="Run audio grouping phase only")
    parser.add_argument("--score", action="store_true", help="Run scoring phase only")
    parser.add_argument("--report", action="store_true", help="Run report generation only")
    parser.add_argument("--all", action="store_true", help="Run the full pipeline (default)")
    
    args = parser.parse_args()
    
    # Load configuration
    config_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(config_dir, args.config)
    config = load_config(config_path)
    
    # Initialize SQLite database
    database.init_db()
    
    # Determine which phases to run
    run_all = args.all or not (args.discover or args.track or args.group or args.score or args.report)
    
    # Auth YouTube client if we are going to hit the API
    youtube = None
    if run_all or args.discover or args.track:
        try:
            youtube = discovery.get_youtube_client(config)
        except Exception as e:
            print(f"\n❌ YouTube Authentication failed: {e}")
            print("Please configure 'youtube_api_key' in config.yaml or set up OAuth credentials.")
            sys.exit(1)
            
    # Execute selected phases
    if run_all or args.discover:
        discovery.run_discovery_phase(youtube, config)
        
    if run_all or args.track:
        tracker.update_metrics_and_durations(youtube, config)
        
    if run_all or args.group:
        grouping.group_candidates_by_audio(config)
        
    if run_all or args.score:
        scoring.score_all_audio_groups(config)
        
    if run_all or args.report:
        reporter.generate_reports(config)
        
    print("\n[Trend Finder] Executed successfully.")

if __name__ == "__main__":
    main()
