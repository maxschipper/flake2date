import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import requests

# import time

# --- Configuration ---
# Get the path to the flake.lock file from the environment variable
# Use a fallback path if the environment variable is not set
FLAKE_LOCK_PATH: str = os.environ.get("NH_FLAKE", ".") + "/flake.lock"
GITHUB_API_URL: str
DOWNSTREAM_HUMAN_TIME: str
UPSTREAM_HUMAN_TIME: str

# Initialize the parser
parser = argparse.ArgumentParser(
    description="Check if a Nix flake input is up to date."
)

# Add the argument (the name of the input)
parser.add_argument(
    "flake_input", help="The name of the flake input to check (e.g., nixpkgs)"
)

# Parse the arguments
args = parser.parse_args()

# Assign the argument value to your variable
CHECK_FLAKE = args.flake_input


CHECK_FLAKE: str = input("check flake: ")


# --- 2. Get DOWNSTREAM TIME from flake.lock ---
try:
    print(f"\nChecking flake.lock file at: {FLAKE_LOCK_PATH}")
    with open(FLAKE_LOCK_PATH, "r") as f:
        lock_data = json.load(f)

    if CHECK_FLAKE not in lock_data["nodes"]:
        print(f"Error: Node '{CHECK_FLAKE}' not found in lock file.")
        exit(1)

    node = lock_data["nodes"][CHECK_FLAKE]

    # The value is an integer Unix timestamp (e.g., 1698379200)
    DOWNSTREAM_TIME = node["locked"]["lastModified"]
    # Optionally convert the timestamp back to a human-readable format for verification
    downstream_dt = datetime.fromtimestamp(DOWNSTREAM_TIME)
    DOWNSTREAM_HUMAN_TIME = downstream_dt.isoformat()
    print(f"Local Last Modified: {DOWNSTREAM_HUMAN_TIME}")

    owner = node["locked"]["owner"]
    repo = node["locked"]["repo"]

    # Check if 'original' has a 'ref' (this is the branch, e.g., 'nixos-unstable')
    # Use .get() because 'original' might not exist or might not have 'ref'
    branch = node.get("original", {}).get("ref")

    if branch:
        print(f"Tracking explicit branch: '{branch}'")
    else:
        # If no ref is specified, we must find the repo's default branch (main/master)
        print("No explicit branch found. Querying GitHub for default branch...")
        repo_info_url = f"https://api.github.com/repos/{owner}/{repo}"

        # We need a separate request just to find if it is 'main' or 'master'
        meta_response = requests.get(repo_info_url)
        meta_response.raise_for_status()
        branch = meta_response.json().get("default_branch")
        print(f"Detected default branch: '{branch}'")

    # 4. Construct the Final API URL
    GITHUB_API_URL = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"

    # GITHUB_API_URL = f"https://api.github.com/repos/{lock_data['nodes'][CHECK_FLAKE]['locked']['owner']}/{lock_data['nodes'][CHECK_FLAKE]['locked']['repo']}/branches/{lock_data['nodes'][CHECK_FLAKE]['original']['ref']}"
    # GITHUB_API_URL = f"https://api.github.com/repos/{lock_data['nodes'][CHECK_FLAKE]['locked']['owner']}/{lock_data['nodes'][CHECK_FLAKE]['locked']['repo']}/branches/main"


except FileNotFoundError:
    print(
        f"Error: flake.lock not found at {FLAKE_LOCK_PATH}. Check if NH_FLAKE is set correctly."
    )
    exit(1)
except json.JSONDecodeError as e:
    print(f"Error: Invalid JSON in {FLAKE_LOCK_PATH}: {e}")
    exit(1)
except KeyError as e:
    print(f"Error parsing flake.lock: Missing key {e}")
    print(f"Ensure the '{CHECK_FLAKE}' node exists in the lock file.")
    exit(1)
except requests.exceptions.RequestException as e:
    print(f"Error fetching repo metadata: {e}")
    exit(1)


# --- 1. Get UPSTREAM TIME from GitHub API ---

try:
    print(f"Checking GitHub API at: {GITHUB_API_URL}")
    response = requests.get(GITHUB_API_URL)
    response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
    github_data = response.json()

    # # The date is a string in ISO 8601 format (e.g., "2023-10-27T12:00:00Z")
    # # Your bash script's `jq` uses the date from `.commit.commit.committer.date`
    # date_str = github_data["commit"]["commit"]["committer"]["date"]

    # # Convert the ISO 8601 string to a datetime object
    # # The 'Z' at the end of the date string signifies UTC (Zulu time)
    # upstream_dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    # # 2. Force it to be UTC aware
    # upstream_dt = upstream_dt.replace(tzinfo=timezone.utc)

    date_str = github_data["commit"]["commit"]["committer"]["date"]

    # 1. Parse the GitHub string as UTC (because it ends in Z)
    upstream_utc = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )

    # 2. Get the Unix timestamp (integers always compare correctly regardless of timezone)
    UPSTREAM_TIME = int(upstream_utc.timestamp())

    # 3. Convert to LOCAL time for the print statement so it matches the one above
    upstream_local = upstream_utc.astimezone()

    UPSTREAM_HUMAN_TIME = upstream_local.isoformat()
    print(f"Upstream Last Commit:   {UPSTREAM_HUMAN_TIME}")


except requests.exceptions.RequestException as e:
    print(f"Error fetching data from GitHub: {e}")
    exit(1)
except KeyError as e:
    print(f"Error parsing GitHub response: Missing key {e}")
    exit(1)


# --- 3. Compare Times and Output Result ---
print("\n--- Comparison ---")
if DOWNSTREAM_TIME < UPSTREAM_TIME:
    print(f"🚨 {CHECK_FLAKE} has new commits")
    print(f"Local time:    {DOWNSTREAM_HUMAN_TIME}")
    print(f"Upstream time: {UPSTREAM_HUMAN_TIME}")
    seconds_diff = UPSTREAM_TIME - DOWNSTREAM_TIME
    time_diff = timedelta(seconds=seconds_diff)
    print(f"Time since last update: {time_diff}")
else:
    print(f"✅ lock for {CHECK_FLAKE} is up to date")
