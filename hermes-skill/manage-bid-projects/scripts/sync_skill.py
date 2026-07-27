#!/usr/bin/env python3
"""Check and safely synchronize this Hermes Skill from official GitHub releases."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path


REPOSITORY = "YUGUORUI007/hermes-dynamic-bid-platform"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
SKILL_SUFFIX = ("hermes-skill", "manage-bid-projects")


def normalize_version(value: str) -> tuple[int, ...]:
    candidate = value.strip().lstrip("v")
    parts = candidate.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in parts)


def installed_version(target: Path) -> str:
    version_file = target / "VERSION"
    return version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "unknown"


def latest_release() -> dict[str, str]:
    request = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "Hermes-Bid-Skill-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot check GitHub releases: {exc}") from exc
    tag = value.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("GitHub latest release does not contain tag_name")
    normalize_version(tag)
    return {"tag": tag, "zip_url": f"https://github.com/{REPOSITORY}/archive/refs/tags/{tag}.zip"}


def release_state(target: Path, tag: str) -> dict[str, object]:
    local = installed_version(target)
    try:
        update_available = local == "unknown" or normalize_version(tag) > normalize_version(local)
    except ValueError:
        update_available = True
    return {"target": str(target), "installed_version": local, "latest_version": tag.lstrip("v"), "update_available": update_available}


def download_archive(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Hermes-Bid-Skill-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise RuntimeError(f"Cannot download GitHub release archive: {exc}") from exc


def locate_skill_source(archive: Path, workdir: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = Path(member.filename).parts
            if ".." in parts or Path(member.filename).is_absolute():
                raise RuntimeError("Release archive contains an unsafe path")
        bundle.extractall(workdir)
    candidates = [path for path in workdir.rglob(SKILL_SUFFIX[-1]) if path.is_dir() and path.parts[-2:] == SKILL_SUFFIX]
    if len(candidates) != 1:
        raise RuntimeError("Could not locate the Skill in the GitHub release archive")
    return candidates[0]


def apply_release(target: Path, release: dict[str, str]) -> dict[str, object]:
    if not target.is_dir():
        raise RuntimeError(f"Skill directory does not exist: {target}")
    with tempfile.TemporaryDirectory(prefix="hermes-bid-skill-") as temp:
        workdir = Path(temp)
        archive = workdir / "release.zip"
        download_archive(release["zip_url"], archive)
        source = locate_skill_source(archive, workdir)
        incoming = target.parent / f".{target.name}.incoming"
        if incoming.exists():
            shutil.rmtree(incoming)
        shutil.copytree(source, incoming)
        expected = release["tag"].lstrip("v")
        if installed_version(incoming) != expected:
            shutil.rmtree(incoming)
            raise RuntimeError("Downloaded Skill version does not match the requested release")
        backup = target.parent / f"{target.name}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        target.rename(backup)
        try:
            incoming.rename(target)
        except OSError:
            backup.rename(target)
            raise
    return {"updated": True, "installed_version": expected, "backup": str(backup), "target": str(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize the Hermes bid-management Skill from GitHub releases")
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parent.parent, help="Installed Skill directory")
    parser.add_argument("--apply", action="store_true", help="Download and install the newest official release")
    args = parser.parse_args()

    release = latest_release()
    state = release_state(args.target, release["tag"])
    if args.apply and state["update_available"]:
        state.update(apply_release(args.target, release))
    else:
        state["updated"] = False
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
