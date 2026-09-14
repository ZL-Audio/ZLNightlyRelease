import time
import tempfile
import os
import sys
import requests
import json
import subprocess
from datetime import datetime, timedelta, timezone

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


# --- Helper Functions ---

def run_command(command):
    """Runs a shell command, logs output, and raises an exception on failure."""
    print(f"Executing: {' '.join(command)}", flush=True)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error executing command: {' '.join(command)}", file=sys.stderr, flush=True)
        print(f"STDOUT: {result.stdout}", file=sys.stderr, flush=True)
        print(f"STDERR: {result.stderr}", file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    print(result.stdout, flush=True)
    return result

def sync_code_and_tags():
    """Configures git and force-pushes all branches and tags to Gitee."""
    print("--- Starting Code and Tag Synchronization ---", flush=True)
    if not GITEE_USERNAME:
        raise ValueError("Error: INPUT_GITEE_USERNAME environment variable is not set. It is required for git push authentication.")
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

def get_github_releases():
    """Fetches all releases from the GitHub repository."""
    print("Fetching releases from GitHub...", flush=True)
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = f"{GITHUB_API_URL}/releases"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    print("Successfully fetched GitHub releases.", flush=True)
    return response.json()

def get_gitee_releases():
    """Fetches all releases from the Gitee repository."""
    print("Fetching releases from Gitee...", flush=True)
    url = f"{GITEE_API_URL}/releases"
    params = {"access_token": GITEE_TOKEN}
    response = requests.get(url, params=params)
    response.raise_for_status()
    print("Successfully fetched Gitee releases.", flush=True)
    return response.json()

def delete_gitee_release(release_id):
    """Deletes an existing release on Gitee by its ID."""
    print(f"  - Deleting existing Gitee release with ID: {release_id}...", flush=True)
    url = f"{GITEE_API_URL}/releases/{release_id}"
    params = {"access_token": GITEE_TOKEN}
    response = requests.delete(url, params=params)
    if response.status_code == 204:
        print(f"  - Successfully deleted Gitee release ID: {release_id}.", flush=True)
    else:
        print(f"  - Warning: Could not delete Gitee release ID {release_id}. Status: {response.status_code}, Body: {response.text}", file=sys.stderr, flush=True)

def create_gitee_release(release_data):
    """Creates a new release on Gitee."""
    tag_name = release_data['tag_name']
    print(f"  - Creating Gitee release for tag '{tag_name}'...", flush=True)
    url = f"{GITEE_API_URL}/releases"
    if release_data['name'] is None:
        release_data['name'] = tag_name
    payload = {
        "access_token": GITEE_TOKEN,
        "tag_name": tag_name,
        "name": release_data['name'],
        "body": release_data['body'] or f"Release for {tag_name}",
        "prerelease": release_data['prerelease'],
        "target_commitish": release_data['target_commitish']
    }
    print(payload, flush=True)
    response = requests.post(url, json=payload)
    if response.status_code == 201:
        print(f"  - Successfully created Gitee release '{release_data['name']}'.", flush=True)
        return response.json()
    else:
        print(f"  - Error creating Gitee release. Status: {response.status_code}, Body: {response.text}", file=sys.stderr, flush=True)
        return None

def upload_gitee_asset(gitee_release_id, asset):
    """Downloads an asset from GitHub (Fail Fast) and uploads it to Gitee (With Retries)."""
    asset_name = asset['name']
    asset_url = asset['url']
    
    print(f"    - Handling asset: {asset_name}", flush=True)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        local_file_path = os.path.join(temp_dir, asset_name)

        print(f"      - Downloading {asset_name} from GitHub to local disk...", flush=True)
        download_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/octet-stream"}
        
        with requests.get(asset_url, headers=download_headers, stream=True) as download_response:
            download_response.raise_for_status()
            with open(local_file_path, 'wb') as f:
                for chunk in download_response.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"      - Download complete.", flush=True)

        print(f"      - Uploading {asset_name} to Gitee...", flush=True)
        upload_url = f"{GITEE_API_URL}/releases/{gitee_release_id}/attach_files"
        params = {"access_token": GITEE_TOKEN}
        
        upload_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        UPLOAD_RETRIES = 10
        UPLOAD_TIMEOUT = 600
        
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

def main():
    """Main synchronization function."""
    try:
        # Validate that all required environment variables are set
        if not all([GITHUB_TOKEN, GITEE_TOKEN, GITEE_OWNER, GITEE_REPO, GITEE_USERNAME, GITHUB_REPO]):
            print("Error: One or more required environment variables are not set.", file=sys.stderr, flush=True)
            sys.exit(1)

        # STEP 1: Sync Code and Tags
        sync_code_and_tags()

        # STEP 2: Force Sync Recent Releases
        print("--- Starting Release Synchronization (Recent Releases Only) ---", flush=True)
        github_releases = get_github_releases()
        gitee_releases = get_gitee_releases()
        
        gitee_release_map = {release['tag_name']: release['id'] for release in gitee_releases}
        print(f"\nFound {len(gitee_release_map)} existing releases on Gitee.", flush=True)

        # Define the time window: now - 24 hours.
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24)
        print(f"Filtering for releases published after: {cutoff_time.isoformat()}", flush=True)

        recent_releases_found = False

        for gh_release in reversed(github_releases):
            tag_name = gh_release['tag_name']

            # Get the 'published_at' timestamp from the GitHub release data.
            published_at_str = gh_release.get('published_at')
            if not published_at_str:
                # Skip drafts or releases without a publication date
                continue

            # Parse the ISO 8601 date string into a datetime object.
            published_at_dt = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))

            # Check if the release is within our 24-hour window.
            if published_at_dt < cutoff_time:
                print(f"\nSkipping older release: '{gh_release['name']}' (published at {published_at_str})", flush=True)
                continue # Move to the next release
            
            recent_releases_found = True
            print(f"\nProcessing recent release: '{gh_release['name']}' (published at {published_at_str})", flush=True)

            # If the release exists on Gitee, delete it first to ensure a clean sync.
            if tag_name in gitee_release_map:
                release_id_to_delete = gitee_release_map[tag_name]
                delete_gitee_release(release_id_to_delete)
            
            # Create the new release on Gitee.
            new_gitee_release = create_gitee_release(gh_release)
            
            # If creation was successful and there are assets, upload them.
            if new_gitee_release and gh_release.get('assets'):
                gitee_release_id = new_gitee_release['id']
                print(f"  - Uploading {len(gh_release['assets'])} asset(s)...", flush=True)
                for asset in gh_release['assets']:
                    upload_gitee_asset(gitee_release_id, asset)
        
        if not recent_releases_found:
            print("\nNo recent releases found to sync in the last 24 hours.", flush=True)
            
        print("\n--- Synchronization Complete ---", flush=True)

    except (requests.exceptions.RequestException, subprocess.CalledProcessError, ValueError) as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
