import time
import tempfile
import os
import sys
import requests
import subprocess

# --- Configuration ---
GITHUB_TOKEN = os.getenv("INPUT_GITHUB_TOKEN")
GITEE_TOKEN = os.getenv("INPUT_GITEE_TOKEN")
GITEE_OWNER = os.getenv("INPUT_GITEE_OWNER")
GITEE_REPO = os.getenv("INPUT_GITEE_REPO")
GITEE_USERNAME = os.getenv("INPUT_GITEE_USERNAME") # For git push authentication
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY")

# API endpoints
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}"
GITEE_API_URL = f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}"
UNSYNCED = "unsynced"
API_TIMEOUT = (15, 60)


# --- Helper Functions ---

def redact(value):
    text = str(value)
    for token in (GITHUB_TOKEN, GITEE_TOKEN):
        if token:
            text = text.replace(token, "***")
    return text


def run_command(command):
    """Runs a shell command, logs output, and raises an exception on failure."""
    print(f"Executing: {redact(' '.join(command))}", flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error executing command: {redact(' '.join(command))}", file=sys.stderr, flush=True)
        print(f"STDOUT: {redact(result.stdout)}", file=sys.stderr, flush=True)
        print(f"STDERR: {redact(result.stderr)}", file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    print(redact(result.stdout), flush=True)
    return result

def sync_code_and_tags():
    """Configures git and force-pushes all branches and tags to Gitee."""
    print("--- Starting Code and Tag Synchronization ---", flush=True)
    if not GITEE_USERNAME:
        raise ValueError("Error: INPUT_GITEE_USERNAME environment variable is not set. It is required for git push authentication.")
    # Include tags published after the workflow's initial checkout.
    run_command(["git", "fetch", "origin", "--tags", "--force"])
    run_command(["git", "config", "--global", "user.name", "GitHub Actions"])
    run_command(["git", "config", "--global", "user.email", "actions@github.com"])
    gitee_repo_url = f"https://{GITEE_USERNAME}:{GITEE_TOKEN}@gitee.com/{GITEE_OWNER}/{GITEE_REPO}.git"
    try:
        run_command(["git", "remote", "get-url", "gitee"])
        run_command(["git", "remote", "set-url", "gitee", gitee_repo_url])
        print("Updated existing 'gitee' remote.", flush=True)
    except subprocess.CalledProcessError:
        print("Remote 'gitee' not found, adding it.", flush=True)
        run_command(["git", "remote", "add", "gitee", gitee_repo_url])
    print("\nPushing all branches to Gitee...", flush=True)
    run_command(["git", "push", "--all", "gitee", "-f"])
    print("All branches successfully pushed.", flush=True)
    print("\nPushing all tags to Gitee...", flush=True)
    run_command(["git", "push", "--tags", "gitee", "-f"])
    print("All tags successfully pushed.", flush=True)
    print("--- Code and Tag Synchronization Complete ---\n", flush=True)

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def get_all_pages(url, headers=None, params=None):
    items = []
    page = 1
    while True:
        page_params = dict(params or {}, per_page=100, page=page)
        response = requests.get(url, headers=headers, params=page_params, timeout=API_TIMEOUT)
        response.raise_for_status()
        batch = response.json()
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def get_github_releases():
    """Fetch every release page, including releases older than 24 hours."""
    print("Fetching releases from GitHub...", flush=True)
    return get_all_pages(f"{GITHUB_API_URL}/releases", headers=github_headers())


def get_github_release(release_id):
    """Read a particular build, never a replacement that happens to share its tag."""
    url = f"{GITHUB_API_URL}/releases/{release_id}"
    response = requests.get(url, headers=github_headers(), timeout=API_TIMEOUT)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    release = response.json()
    release["assets"] = get_all_pages(f"{url}/assets", headers=github_headers())
    return release


def is_pending(release):
    return (not release.get("draft", False)
            and bool(release.get("published_at"))
            and release.get("body") == UNSYNCED)


def release_fingerprint(release):
    return (release.get("updated_at"), sorted(
        (asset["id"], asset["name"], asset["size"], asset.get("updated_at"), asset.get("digest"))
        for asset in release["assets"]
    ))


def clear_synced_marker(release):
    release_id = release["id"]
    current = get_github_release(release_id)
    if current is None:
        print("  - Source release was replaced; leaving the new build's marker untouched.", flush=True)
        return False
    if not is_pending(current) or release_fingerprint(current) != release_fingerprint(release):
        print("  - Source release was edited during sync; leaving its body untouched.", flush=True)
        return False
    response = requests.patch(
        f"{GITHUB_API_URL}/releases/{release_id}",
        headers=github_headers(),
        json={"body": ""},
        timeout=API_TIMEOUT,
    )
    response.raise_for_status()
    print(f"  - Cleared unsynced marker for {release['tag_name']}.", flush=True)
    return True

def get_gitee_releases():
    """Fetches all releases from the Gitee repository."""
    print("Fetching releases from Gitee...", flush=True)
    return get_all_pages(f"{GITEE_API_URL}/releases", params={"access_token": GITEE_TOKEN})

def delete_gitee_release(release_id):
    """Deletes an existing release on Gitee by its ID."""
    print(f"  - Deleting existing Gitee release with ID: {release_id}...", flush=True)
    url = f"{GITEE_API_URL}/releases/{release_id}"
    params = {"access_token": GITEE_TOKEN}
    response = requests.delete(url, params=params, timeout=API_TIMEOUT)
    response.raise_for_status()
    print(f"  - Successfully deleted Gitee release ID: {release_id}.", flush=True)

def create_gitee_release(release_data):
    """Creates a new release on Gitee."""
    tag_name = release_data['tag_name']
    print(f"  - Creating Gitee release for tag '{tag_name}'...", flush=True)
    url = f"{GITEE_API_URL}/releases"
    payload = {
        "access_token": GITEE_TOKEN,
        "tag_name": tag_name,
        "name": release_data.get('name') or tag_name,
        "body": f"Release for {tag_name}",
        "prerelease": release_data['prerelease'],
        "target_commitish": release_data['target_commitish']
    }
    response = requests.post(url, json=payload, timeout=API_TIMEOUT)
    response.raise_for_status()
    if response.status_code != 201:
        raise RuntimeError(f"Unexpected status creating Gitee release: {response.status_code}")
    print(f"  - Successfully created Gitee release '{payload['name']}'.", flush=True)
    return response.json()

def upload_gitee_asset(gitee_release_id, asset):
    """Downloads an asset from GitHub (Fail Fast) and uploads it to Gitee (With Retries)."""
    asset_name = asset['name']
    asset_url = asset['url']
    if os.path.basename(asset_name) != asset_name:
        raise ValueError(f"Invalid asset filename: {asset_name}")
    
    print(f"    - Handling asset: {asset_name}", flush=True)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        local_file_path = os.path.join(temp_dir, asset_name)

        print(f"      - Downloading {asset_name} from GitHub to local disk...", flush=True)
        download_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/octet-stream"}
        
        with requests.get(asset_url, headers=download_headers, stream=True, timeout=(15, 120)) as download_response:
            download_response.raise_for_status()
            with open(local_file_path, 'wb') as f:
                for chunk in download_response.iter_content(chunk_size=8192):
                    f.write(chunk)
        if os.path.getsize(local_file_path) != asset['size']:
            raise RuntimeError(f"Downloaded size does not match GitHub asset: {asset_name}")
        print("      - Download complete.", flush=True)

        print(f"      - Uploading {asset_name} to Gitee...", flush=True)
        upload_url = f"{GITEE_API_URL}/releases/{gitee_release_id}/attach_files"
        UPLOAD_RETRIES = 10
        
        CONNECT_TIMEOUT = "15"
        MAX_TIME = "1200"
        
        # Using curl instead of requests for better stability across firewalls
        curl_command = [
            "curl", "-X", "POST",
            f"{upload_url}?access_token={GITEE_TOKEN}",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "-F", f"file=@{local_file_path};type={asset['content_type']};filename={asset_name}",
            "--connect-timeout", CONNECT_TIMEOUT,
            "--max-time", MAX_TIME,
            "-s", "-w", "\n%{http_code}"
        ]

        for attempt in range(1, UPLOAD_RETRIES + 1):
            try:
                result = subprocess.run(curl_command, capture_output=True, text=True)
                
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    http_code = lines[-1] if lines else ""
                    body = '\n'.join(lines[:-1]) if len(lines) > 1 else ""
                    
                    if http_code == "201":
                        print(f"      - Successfully uploaded {asset_name}.", flush=True)
                        break
                    else:
                        print(f"      - Upload attempt {attempt} failed. Status: {http_code}, Body: {body}", file=sys.stderr, flush=True)
                        if attempt == UPLOAD_RETRIES:
                            raise Exception(f"Upload failed with status {http_code}: {body}")
                else:
                    print(f"      - Upload attempt {attempt} encountered a network error: curl exit code {result.returncode}, stderr: {result.stderr}", file=sys.stderr, flush=True)
                    if attempt == UPLOAD_RETRIES:
                        raise Exception(f"Upload failed with network error. curl exit code: {result.returncode}")
                        
            except Exception as e:
                print(f"      - Upload attempt {attempt} encountered an error: {e}", file=sys.stderr, flush=True)
                if attempt == UPLOAD_RETRIES:
                    raise 
            
            if attempt < UPLOAD_RETRIES:
                sleep_time = 3 * attempt + 3
                print(f"      - Retrying in {sleep_time} seconds...", flush=True)
                time.sleep(sleep_time)


# --- Main Logic ---

def sync_release(release, gitee_release_map):
    # The listing can become stale while earlier releases are being transferred.
    release = get_github_release(release["id"])
    if release is None or not is_pending(release):
        print("Skipping a release that was replaced or is no longer marked unsynced.", flush=True)
        return

    tag_name = release["tag_name"]
    assets = release["assets"]
    if (not assets or len({asset["name"] for asset in assets}) != len(assets)
            or any(asset["state"] != "uploaded" or asset["size"] <= 0 for asset in assets)):
        raise ValueError(f"Release {tag_name} has missing, duplicate, or incomplete assets.")

    print(f"\nProcessing unsynced release: {tag_name}", flush=True)
    if tag_name in gitee_release_map:
        delete_gitee_release(gitee_release_map[tag_name])
    new_gitee_release = create_gitee_release(release)
    gitee_release_id = new_gitee_release["id"]
    gitee_release_map[tag_name] = gitee_release_id
    print(f"  - Uploading {len(assets)} asset(s)...", flush=True)
    for asset in assets:
        upload_gitee_asset(gitee_release_id, asset)

    # An exception at any earlier step leaves the marker available for a retry.
    clear_synced_marker(release)


def main():
    """Main synchronization function."""
    try:
        # Validate that all required environment variables are set
        if not all([GITHUB_TOKEN, GITEE_TOKEN, GITEE_OWNER, GITEE_REPO, GITEE_USERNAME, GITHUB_REPO]):
            raise ValueError("One or more required environment variables are not set.")

        pending_releases = [release for release in get_github_releases() if is_pending(release)]
        if not pending_releases:
            print("No published releases have a body exactly equal to 'unsynced'.", flush=True)
            return 0

        # Mirror code and tags once, then acknowledge each release independently.
        sync_code_and_tags()
        gitee_releases = get_gitee_releases()
        gitee_release_map = {release['tag_name']: release['id'] for release in gitee_releases}
        failures = []
        for release in reversed(pending_releases):
            try:
                sync_release(release, gitee_release_map)
            except Exception as error:
                failures.append(release["tag_name"])
                print(f"Sync failed for {release['tag_name']}: {redact(error)}", file=sys.stderr, flush=True)

        if failures:
            print(f"Failed releases remain pending: {', '.join(failures)}", file=sys.stderr, flush=True)
            return 1
        print("\n--- Synchronization Complete ---", flush=True)
        return 0
    except Exception as error:
        print(f"\nSynchronization failed: {redact(error)}", file=sys.stderr, flush=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
