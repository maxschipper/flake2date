import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

# --- Configuration ---
FLAKE_LOCK_PATH = os.environ.get("NH_FLAKE", ".") + "/flake.lock"


def get_token_from_gh_cli():
    """Attempts to retrieve the GitHub token from the 'gh' CLI tool."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def get_upstream_info(owner, repo, branch_hint=None, token=None):
    """Fetches commit timestamp from GitHub."""
    headers = {}
    if token:
        headers["Authorization"] = f"token {token}"

    branch = branch_hint
    if not branch:
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}", headers=headers
            )
            resp.raise_for_status()
            branch = resp.json().get("default_branch")
        except requests.RequestException:
            return None, None

    try:
        api_url = f"https://api.github.com/repos/{owner}/{repo}/branches/{branch}"
        resp = requests.get(api_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        date_str = data["commit"]["commit"]["committer"]["date"]
        dt_utc = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return int(dt_utc.timestamp()), dt_utc.astimezone()
    except requests.RequestException:
        return None, None


def check_input(name, node_data, token=None, only_outdated=False):
    """
    Checks a single flake input.
    If only_outdated is True, it prints nothing unless an update is found.
    """
    try:
        if node_data["locked"]["type"] != "github":
            return

        downstream_ts = node_data["locked"]["lastModified"]
        downstream_dt = datetime.fromtimestamp(downstream_ts).astimezone()
        owner = node_data["locked"]["owner"]
        repo = node_data["locked"]["repo"]
        branch_hint = node_data.get("original", {}).get("ref")
    except KeyError:
        return

    # Fetch Upstream
    upstream_ts, upstream_dt = get_upstream_info(owner, repo, branch_hint, token)

    if upstream_ts is None:
        if not only_outdated:
            print(f"Checking {name} ({owner}/{repo})...")
            print(f"    ❌ Could not fetch upstream info.")
        return

    # Logic: Should we print?
    is_outdated = downstream_ts < upstream_ts

    # If we only want outdated ones, and this is NOT outdated, return silently.
    if only_outdated and not is_outdated:
        return

    print(f"Checking {name} ({owner}/{repo})...")

    if is_outdated:
        diff = timedelta(seconds=upstream_ts - downstream_ts)
        print(f"    🚨 UPDATE AVAILABLE")
        print(f"       Local:    {downstream_dt}")
        print(f"       Upstream: {upstream_dt}")
        print(f"       Lag:      {diff}")
    elif downstream_ts > upstream_ts:
        diff = timedelta(seconds=downstream_ts - upstream_ts)
        print(f"    ⚠️  Local ahead by {diff}")
    else:
        print(f"    ✅ Up to date")

    print("-" * 40)


def main():
    parser = argparse.ArgumentParser(
        description="Check Nix flake inputs against GitHub upstream."
    )

    # Mutually Exclusive Group
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "flake_input", nargs="?", help="The name of the flake input to check"
    )
    group.add_argument(
        "-a", "--all", action="store_true", help="Check all inputs and show all results"
    )
    group.add_argument(
        "-A",
        "--all-outdated",
        action="store_true",
        help="Check all inputs but ONLY show outdated ones",
    )

    args = parser.parse_args()

    # Determine execution mode
    check_all = args.all or args.all_outdated
    only_outdated = args.all_outdated

    # Load Lock File
    try:
        with open(FLAKE_LOCK_PATH, "r") as f:
            lock_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error reading lock file: {e}")
        sys.exit(1)

    # Auth Token
    token = os.environ.get("GITHUB_TOKEN") or get_token_from_gh_cli()
    if not token and check_all:
        # Print tip only if we are showing everything.
        # In 'only_outdated' mode, we generally want less noise unless necessary.
        if not only_outdated:
            print(
                "ℹ️  Tip: Set GITHUB_TOKEN or use 'gh auth login' to avoid rate limits.\n"
            )

    # Execution Loop
    if check_all:
        root_node_name = lock_data.get("root", "root")
        root_inputs = (
            lock_data.get("nodes", {}).get(root_node_name, {}).get("inputs", {})
        )

        # If we can't find specific root inputs, fall back to checking every node
        target_nodes = (
            root_inputs.items() if root_inputs else lock_data["nodes"].items()
        )

        if not target_nodes and not only_outdated:
            print("No inputs found to check.")

        for name, node_key in target_nodes:
            # Handle case where keys map to node names
            actual_node_name = node_key if isinstance(node_key, str) else name
            if actual_node_name in lock_data["nodes"]:
                check_input(
                    name, lock_data["nodes"][actual_node_name], token, only_outdated
                )
    else:
        # Check single specific input (Never hide output here)
        if args.flake_input not in lock_data["nodes"]:
            print(f"Error: Input '{args.flake_input}' not found.")
            sys.exit(1)
        check_input(
            args.flake_input,
            lock_data["nodes"][args.flake_input],
            token,
            only_outdated=False,
        )


if __name__ == "__main__":
    main()
