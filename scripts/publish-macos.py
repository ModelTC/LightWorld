"""Publish the verified package to a rolling GitHub release."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def gh(*args, input=None):
    return subprocess.run(
        ["gh", *args], input=input, stdout=subprocess.PIPE, text=True, check=True,
    ).stdout


def api(path, method="GET", **data):
    endpoint = f"repos/{os.environ['GH_REPO']}/{path}"
    args = ("--input", "-") if data else ()
    result = gh("api", "--method", method, endpoint, *args, input=json.dumps(data) if data else None)
    return json.loads(result) if result else None


def publish(archive, channel):
    repository = os.environ["GH_REPO"]
    commit = os.environ["GITHUB_SHA"]
    source = api("git/ref/heads/main")
    if source["object"]["sha"] != commit:
        print("A newer commit is on main. Skipping this release.")
        return

    preview = channel == "preview"
    title = "LightWorld Preview" if preview else "LightWorld"
    releases = api("releases?per_page=100")
    release = next((item for item in releases if item["tag_name"] == channel), None)
    if release is None:
        release = api("releases", "POST", tag_name=channel, name=title, draft=True,
                      prerelease=preview, target_commitish=commit)
    if release.get("immutable"):
        raise RuntimeError(f"The {channel} release is immutable. Enable asset updates for this rolling channel.")

    checksum = hashlib.sha256()
    with archive.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(chunk)
    digest = checksum.hexdigest()
    build = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    candidate = archive.with_name(f"{archive.stem}.build-{build}.zip")
    os.link(archive, candidate)
    gh("release", "upload", channel, str(candidate))
    release_path = f"releases/{release['id']}"
    assets = api(f"{release_path}/assets?per_page=100")
    staged = next((asset for asset in assets if asset["name"] == candidate.name), None)
    if (staged is None or staged["state"] != "uploaded" or staged["size"] != archive.stat().st_size
            or staged.get("digest") != f"sha256:{digest}"):
        raise RuntimeError("GitHub did not confirm the size and checksum of the uploaded app.")

    previous = next((asset for asset in assets if asset["name"] == archive.name), None)
    if previous:
        api(f"releases/assets/{previous['id']}", "PATCH", name=f"{archive.stem}.backup-{previous['id']}.zip")
    try:
        api(f"releases/assets/{staged['id']}", "PATCH", name=archive.name)
    except subprocess.CalledProcessError:
        if previous:
            api(f"releases/assets/{previous['id']}", "PATCH", name=archive.name)
        raise

    url = f"https://github.com/{repository}/releases/download/{channel}/{archive.name}"
    signing = ("This preview is signed ad hoc. macOS may block it after download."
               if preview else "Signed with Developer ID and notarized by Apple.")
    notes = (
        f"[Download LightWorld for Apple Silicon]({url})\n\n"
        f"Requires macOS 13 or later. Unzip and open **LightWorld.app**. "
        f"The package includes UPBGE, Python, and Blender MCP.\n\n{signing}\n\n"
        f"Build commit `{commit}`\n\nSHA-256 `{digest}`\n"
    )
    if not release["draft"]:
        api(f"git/refs/tags/{channel}", "PATCH", sha=commit, force=True)
    api(release_path, "PATCH", body=notes, draft=False, prerelease=preview, name=title,
        target_commitish=commit, make_latest="false" if preview else "true")

    for asset in assets:
        if asset["id"] == staged["id"]:
            continue
        if asset == previous or asset["name"].startswith((f"{archive.stem}.build-", f"{archive.stem}.backup-")):
            api(f"releases/assets/{asset['id']}", "DELETE")
    print(url)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(notes)


if __name__ == "__main__":
    publish(Path(sys.argv[1]), sys.argv[2])
