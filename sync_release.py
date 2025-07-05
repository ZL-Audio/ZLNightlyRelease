import os
import sys
import requests
import json
import subprocess

# --- Configuration ---
# Get credentials and repository details from environment variables
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
        print(f"Error executing command: {' '.join(command)}", file=sys.stderr)
        print(f"STDOUT: {result.stdout}", file=sys.stderr)
        print(f"STDERR: {result.stderr}", file=sys.stderr)
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    print(result.stdout)
    return result

def sync_code_and_tags():
    """Configures git and force-pushes all branches and tags to Gitee."""
    # (This function remains unchanged)
    print("--- Starting Code and Tag Synchronization ---", flush=True)
    if not GITEE_USERNAME:
        raise ValueError("Error: INPUT_GITEE_USERNAME environment variable is not set. It is required for git push authentication.")
    run_command(["git", "config", "--global", "user.name", "GitHub Actions"])
    run_command(["git", "config", "--global", "user.email", "actions@github.com"])
    gitee_repo_url = f"https://{GITEE_USERNAME}:{GITEE_TOKEN}@gitee.com/{GITEE_OWNER}/{GITEE_REPO}.git"
    try:
        run_command(["git", "remote", "get-url", "gitee"])
        run_command(["git", "remote", "set-url", "gitee", gitee_repo_url])
        print("Updated existing 'gitee' remote.")
    except subprocess.CalledProcessError:
        print("Remote 'gitee' not found, adding it.")
        run_command(["git", "remote", "add", "gitee", gitee_repo_url])
    print("\nPushing all branches to Gitee...", flush=True)
    run_command(["git", "push", "--all", "gitee", "-f"])
    print("All branches successfully pushed.")
    print("\nPushing all tags to Gitee...", flush=True)
    run_command(["git", "push", "--tags", "gitee", "-f"])
    print("All tags successfully pushed.")
    print("--- Code and Tag Synchronization Complete ---\n", flush=True)

def get_github_releases():
    """Fetches all releases from the GitHub repository."""
    # (This function remains unchanged)
    print("Fetching releases from GitHub...")
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    url = f"{GITHUB_API_URL}/releases"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    print("Successfully fetched GitHub releases.")
    return response.json()

def get_gitee_releases():
    """Fetches all releases from the Gitee repository."""
    # (This function remains unchanged)
    print("Fetching releases from Gitee...")
    url = f"{GITEE_API_URL}/releases"
    params = {"access_token": GITEE_TOKEN}
    response = requests.get(url, params=params)
    response.raise_for_status()
    print("Successfully fetched Gitee releases.")
    return response.json()

def delete_gitee_release(release_id):
    """Deletes an existing release on Gitee by its ID."""
    print(f"  - Deleting existing Gitee release with ID: {release_id}...")
    url = f"{GITEE_API_URL}/releases/{release_id}"
    params = {"access_token": GITEE_TOKEN}
    response = requests.delete(url, params=params)
    if response.status_code == 204:
        print(f"  - Successfully deleted Gitee release ID: {release_id}.")
    else:
        # Don't fail the whole script, just log the error
        print(f"  - Warning: Could not delete Gitee release ID {release_id}. Status: {response.status_code}, Body: {response.text}", file=sys.stderr)

def create_gitee_release(release_data):
    """Creates a new release on Gitee."""
    # (This function remains unchanged)
    tag_name = release_data['tag_name']
    print(f"  - Creating Gitee release for tag '{tag_name}'...")
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
        print(f"  - Successfully created Gitee release '{release_data['name']}'.")
        return response.json()
    else:
        print(f"  - Error creating Gitee release. Status: {response.status_code}, Body: {response.text}", file=sys.stderr)
        return None

def upload_gitee_asset(gitee_release_id, asset):
    """Downloads an asset from GitHub and uploads it to a Gitee release."""
    # (This function remains unchanged)
    asset_name = asset['name']
    asset_url = asset['url']
    print(f"    - Handling asset: {asset_name}")
    print(f"      - Downloading from GitHub...")
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/octet-stream"}
    download_response = requests.get(asset_url, headers=headers, stream=True)
    download_response.raise_for_status()
    print(f"      - Uploading to Gitee...")
    upload_url = f"{GITEE_API_URL}/releases/{gitee_release_id}/attach_files"
    params = {"access_token": GITEE_TOKEN}
    files = {'file': (asset_name, download_response.content, asset['content_type'])}
    upload_response = requests.post(upload_url, params=params, files=files)
    if upload_response.status_code == 201:
        print(f"      - Successfully uploaded asset.")
    else:
        print(f"      - Error uploading asset. Status: {upload_response.status_code}, Body: {upload_response.text}", file=sys.stderr)

# --- Main Logic ---

def main():
    """Main synchronization function."""
    try:
        # Validate that all required environment variables are set
        if not all([GITHUB_TOKEN, GITEE_TOKEN, GITEE_OWNER, GITEE_REPO, GITEE_USERNAME, GITHUB_REPO]):
            print("Error: One or more required environment variables are not set.", file=sys.stderr)
            sys.exit(1)

        # STEP 1: Sync Code and Tags
        sync_code_and_tags()

        # STEP 2: Force Sync Releases
        print("--- Starting Release Synchronization (Force Mode) ---", flush=True)
        github_releases = get_github_releases()
        gitee_releases = get_gitee_releases()
        
        # Create a map of Gitee tags to their release IDs for easy lookup and deletion
        gitee_release_map = {release['tag_name']: release['id'] for release in gitee_releases}
        print(f"\nFound {len(gitee_release_map)} existing releases on Gitee.")

        # Iterate through all GitHub releases to ensure they are all on Gitee
        for gh_release in reversed(github_releases):
            tag_name = gh_release['tag_name']
            print(f"\nProcessing GitHub release: '{gh_release['name']}' (tag: {tag_name})")

            # <<-- NEW LOGIC: If a release with the tag exists on Gitee, delete it first -->>
            if tag_name in gitee_release_map:
                release_id_to_delete = gitee_release_map[tag_name]
                delete_gitee_release(release_id_to_delete)
            
            # Proceed to create the release, regardless of whether it existed before
            new_gitee_release = create_gitee_release(gh_release)
            
            if new_gitee_release and gh_release.get('assets'):
                gitee_release_id = new_gitee_release['id']
                print(f"  - Uploading {len(gh_release['assets'])} asset(s)...")
                for asset in gh_release['assets']:
                    upload_gitee_asset(gitee_release_id, asset)
            
        print("\n--- Synchronization Complete ---", flush=True)

    except (requests.exceptions.RequestException, subprocess.CalledProcessError, ValueError) as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()