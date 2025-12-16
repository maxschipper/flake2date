import requests
import json
import os
import sys
import argparse
from datetime import datetime, timezone, timedelta

# --- Configuration ---
FLAKE_LOCK_PATH = os.environ.get("NH_FLAKE", ".") + "/flake.lock"


def get_upstream_info(owner, repo, branch_hint=None, token=None):
    """
    Determines the correct branch and fetches the latest commit date from GitHub.
    Returns: (timestamp_int, datetime_obj_local) or (None, None) on error.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    # 1. Determine Branch
    branch = branch_hint
    if not branch:
        # Fetch default branch if not specified
        repo_info_url = f"https://api.github.com/repos/{owner}/{repo}"
        try:
            resp = requests.get(repo_info_url, headers=headers)
            resp.raise_for_status()
            branch = resp.json().get("default_branch")
        except requests.RequestException:
            # Silent fail or print if you want verbose logs
            return None, None

    # 2. Fetch Commit Data
    api_url = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
    try:
        resp = requests.get(api_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        date_str = data["commit"]["commit"]["committer"]["date"]
        # Parse UTC string
        dt_utc = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return int(dt_utc.timestamp()), dt_utc.astimezone()

    except requests.RequestException as e:
        print(f"    ⚠️  Error fetching GitHub data for {owner}/{repo}: {e}")
        return None, None


def check_input(name, node_data, token=None):
    """
    Checks a single flake input against upstream and prints the result.
    """
    # 1. Parse Downstream (Local) Time
    try:
        # Check if it's a GitHub node (has owner/repo)
        if node_data["locked"]["type"] != "github":
            return  # Skip non-github inputs silently

        downstream_ts = node_data["locked"]["lastModified"]
        downstream_dt = datetime.fromtimestamp(downstream_ts).astimezone()

        owner = node_data["locked"]["owner"]
        repo = node_data["locked"]["repo"]
        branch_hint = node_data.get("original", {}).get("ref")

    except KeyError:
        # Skip nodes that don't match standard structure
        return

    print(f"Checking {name} ({owner}/{repo})...")

    # 2. Fetch Upstream Time
    upstream_ts, upstream_dt = get_upstream_info(owner, repo, branch_hint, token)

    if upstream_ts is None:
        print("    ❌ Could not fetch upstream info.")
        return

    # 3. Compare
    if downstream_ts < upstream_ts:
        diff = timedelta(seconds=upstream_ts - downstream_ts)
        print("    🚨 UPDATE AVAILABLE")
        print(f"       Local:    {downstream_dt}")
        print(f"       Upstream: {upstream_dt}")
        print(f"       Lag:      {diff}")
    elif downstream_ts > upstream_ts:
        diff = timedelta(seconds=downstream_ts - upstream_ts)
        print(f"    ⚠️  Local ahead by {diff}")
    else:
        print("    ✅ Up to date")
    print("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description="Check Nix flake inputs against GitHub upstream."
    )

    # Create a mutually exclusive group: either specific input OR --all
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "flake_input",
        nargs="?",
        help="The name of the flake input to check (e.g., nixpkgs)",
    )
    group.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Check all direct inputs in the lock file",
    )

    args = parser.parse_args()

    # Load Lock File
    try:
        with open(FLAKE_LOCK_PATH, "r") as f:
            lock_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: flake.lock not found at {FLAKE_LOCK_PATH}")
        sys.exit(1)
    except json.JSONDecodeError:
        print("Error: flake.lock is not valid JSON")
        sys.exit(1)

    # Optional: Get GitHub Token from Env
    token = os.environ.get("GITHUB_TOKEN")
    if not token and args.all:
        print(
            "ℹ️  Tip: Set GITHUB_TOKEN to avoid rate limits when checking multiple inputs.\n"
        )

    if args.all:
        # Strategy: Iterate over the ROOT node's inputs (Direct Dependencies)
        root_node_name = lock_data.get("root", "root")

        try:
            # 'inputs' is a dict like: {"nixpkgs": "nixpkgs", "utils": "utils_2"}
            # The value is the key to look up in the "nodes" dictionary
            root_inputs = lock_data["nodes"][root_node_name]["inputs"]

            # If root_inputs is empty, the flake might be simple or malformed
            if not root_inputs:
                print("No direct inputs found in root node.")

            for input_name, node_key in root_inputs.items():
                # node_key is usually a string, but in complex flakes can be a list
                # We assume standard string keys for direct inputs
                if isinstance(node_key, str):
                    node_data = lock_data["nodes"][node_key]
                    check_input(input_name, node_data, token)

        except KeyError:
            print(
                "Could not find root inputs structure. Checking all valid GitHub nodes..."
            )
            for name, node in lock_data["nodes"].items():
                if name == root_node_name:
                    continue
                check_input(name, node, token)

    else:
        # Check single input
        if args.flake_input not in lock_data["nodes"]:
            print(f"Error: Input '{args.flake_input}' not found in {FLAKE_LOCK_PATH}")
            sys.exit(1)
        check_input(args.flake_input, lock_data["nodes"][args.flake_input], token)


if __name__ == "__main__":
    main()
