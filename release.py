"""Publish downloaded nightly installers directly to GitHub Releases."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


REPOSITORY = "ZL-Audio/ZLNightlyRelease"
UNSYNCED = "unsynced"


def github_api(endpoint, method="GET", payload=None):
    command = ["gh", "api", "--method", method, endpoint]
    if payload is not None:
        command.extend(["--input", "-"])
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def github_list(endpoint):
    items = []
    page = 1
    while True:
        batch = github_api(f"{endpoint}?per_page=100&page={page}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def collect_installers(artifact_directory):
    """Find downloaded installers; the build jobs enforce artifact availability."""
    return sorted(
        path for path in Path(artifact_directory).rglob("*")
        if path.is_file() and path.suffix in {".msi", ".zip", ".dmg"}
    )


def publish_release(tag, artifact_directory):
    installers = collect_installers(artifact_directory)
    endpoint = f"repos/{REPOSITORY}/releases"

    # Include drafts left by failed attempts. Deleting a release preserves its tag.
    for release in github_list(endpoint):
        if release["tag_name"] == tag:
            github_api(f"{endpoint}/{release['id']}", method="DELETE")

    # Each build gets a new release ID so an older Gitee sync cannot acknowledge it.
    release = github_api(endpoint, method="POST", payload={
        "tag_name": tag,
        "target_commitish": "main",
        "name": tag,
        "body": "",
        "draft": True,
        "prerelease": True,
    })
    release_id = release["id"]
    subprocess.run([
        "gh", "release", "upload", tag,
        *[str(path) for path in installers],
        "--repo", REPOSITORY,
    ], check=True)

    # GitHub creates the tag on publication if it does not already exist.
    github_api(f"{endpoint}/{release_id}", method="PATCH", payload={
        "draft": False,
        "prerelease": True,
        "body": UNSYNCED,
    })
    print(f"Published {tag} with {len(installers)} installers; queued for Gitee sync.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--artifacts", required=True)
    args = parser.parse_args()
    try:
        if not os.getenv("GH_TOKEN"):
            raise ValueError("NIGHTLY_RELEASE_TOKEN is not configured for this workflow.")
        publish_release(args.tag, args.artifacts)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Nightly publication failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
