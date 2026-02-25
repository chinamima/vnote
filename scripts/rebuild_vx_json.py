#!/usr/bin/env python3
"""
Rebuild VNote VX node configs (`vx.json`) by scanning notebook folders.

Rules:
1. Include child folders and `.md` files only.
2. Exclude resource folders ending with `_assets`.
3. Exclude common built-in folders (such as `vx_notebook` and `vx_recycle_bin`).
4. Rebuild `vx.json` for root and every included folder recursively.

Examples:
  python scripts/rebuild_vx_json.py /path/to/notebook
  python scripts/rebuild_vx_json.py /path/to/notebook --dry-run
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CONFIG_FILE_NAME = "vx.json"
NOTE_SUFFIX = ".md"
DEFAULT_CONFIG_VERSION = 3

BUILT_IN_DIR_NAMES = {
    "vx_notebook",
    "vx_recycle_bin",
    "vx_images",
    "vx_attachments",
    "_v_images",
    "_v_attachments",
}

NODE_VISUAL_KEYS = ("background_color", "border_color", "name_color")
ATTACHMENT_ROOT_CANDIDATES = ("vx_attachments", "_v_attachments")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild VNote vx.json recursively by scanning folders and markdown files."
    )
    parser.add_argument(
        "notebook_root",
        type=Path,
        help="Notebook root directory path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be generated, without writing files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each generated vx.json path with counters.",
    )
    return parser.parse_args()


def iso_utc_from_timestamp(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return datetime.now(tz=timezone.utc).timestamp()


def parse_id_like(value: Any) -> Optional[str]:
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return value
    return None


def parse_signature_like(value: Any) -> Optional[str]:
    parsed = parse_id_like(value)
    if parsed is None:
        return None
    return parsed if parsed != "0" else None


def generate_signature(rng: random.SystemRandom) -> str:
    while True:
        sig = rng.getrandbits(63)
        if sig > 0:
            return str(sig)


def load_existing_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def map_entries_by_name(entries: Any) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not isinstance(entries, list):
        return result
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name and name not in result:
            result[name] = item
    return result


def get_existing_time(existing: Dict[str, Any], key: str, fallback_ts: float) -> str:
    value = existing.get(key)
    if isinstance(value, str) and value:
        return value
    return iso_utc_from_timestamp(fallback_ts)


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def should_exclude_dir(name: str) -> bool:
    low = name.lower()
    if is_hidden_name(name):
        return True
    if low.endswith("_assets"):
        return True
    if low in BUILT_IN_DIR_NAMES:
        return True
    return False


def iter_children(dir_path: Path) -> Tuple[List[Path], List[Path]]:
    folders: List[Path] = []
    notes: List[Path] = []
    for child in dir_path.iterdir():
        name = child.name
        if name == CONFIG_FILE_NAME:
            continue
        if child.is_symlink():
            continue

        if child.is_dir():
            if should_exclude_dir(name):
                continue
            folders.append(child)
            continue

        if child.is_file() and child.suffix.lower() == NOTE_SUFFIX:
            notes.append(child)

    folders.sort(key=lambda p: p.name.casefold())
    notes.sort(key=lambda p: p.name.casefold())
    return folders, notes


def infer_attachment_folder_from_assets(note_path: Path) -> str:
    assets_root = note_path.parent / f"{note_path.stem}_assets"
    if not assets_root.is_dir():
        return ""

    for root_name in ATTACHMENT_ROOT_CANDIDATES:
        root = assets_root / root_name
        if not root.is_dir():
            continue
        subdirs = sorted([p.name for p in root.iterdir() if p.is_dir()], key=str.casefold)
        if len(subdirs) == 1:
            return subdirs[0]
    return ""


def clean_tags(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [tag for tag in value if isinstance(tag, str) and tag]


def build_folder_entry(name: str, existing: Dict[str, Any]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"name": name}
    for key in NODE_VISUAL_KEYS:
        value = existing.get(key)
        if isinstance(value, str) and value:
            entry[key] = value
    return entry


def build_file_entry(note_path: Path, existing: Dict[str, Any], rng: random.SystemRandom) -> Dict[str, Any]:
    mtime = safe_mtime(note_path)
    entry: Dict[str, Any] = {
        "name": note_path.name,
        "id": parse_id_like(existing.get("id")) or "0",
        "signature": parse_signature_like(existing.get("signature")) or generate_signature(rng),
        "created_time": get_existing_time(existing, "created_time", mtime),
        "modified_time": iso_utc_from_timestamp(mtime),
        "tags": clean_tags(existing.get("tags")),
    }

    attachment_folder = existing.get("attachment_folder")
    if not isinstance(attachment_folder, str):
        attachment_folder = ""
    if not attachment_folder:
        attachment_folder = infer_attachment_folder_from_assets(note_path)
    entry["attachment_folder"] = attachment_folder

    for key in NODE_VISUAL_KEYS:
        value = existing.get(key)
        if isinstance(value, str) and value:
            entry[key] = value

    return entry


def build_node_config(
    dir_path: Path,
    folder_paths: Iterable[Path],
    note_paths: Iterable[Path],
    existing: Dict[str, Any],
    rng: random.SystemRandom,
) -> Dict[str, Any]:
    mtime = safe_mtime(dir_path)
    existing_files = map_entries_by_name(existing.get("files"))
    existing_folders = map_entries_by_name(existing.get("folders"))

    folder_entries = [
        build_folder_entry(folder.name, existing_folders.get(folder.name, {})) for folder in folder_paths
    ]
    file_entries = [
        build_file_entry(note, existing_files.get(note.name, {}), rng) for note in note_paths
    ]

    version = existing.get("version")
    if not isinstance(version, int):
        version = DEFAULT_CONFIG_VERSION

    config: Dict[str, Any] = {
        "version": version,
        "id": parse_id_like(existing.get("id")) or "0",
        "signature": parse_signature_like(existing.get("signature")) or generate_signature(rng),
        "created_time": get_existing_time(existing, "created_time", mtime),
        "modified_time": iso_utc_from_timestamp(mtime),
        "files": file_entries,
        "folders": folder_entries,
    }

    for key in NODE_VISUAL_KEYS:
        value = existing.get(key)
        if isinstance(value, str) and value:
            config[key] = value

    return config


def write_json(path: Path, data: Dict[str, Any]) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def rebuild_recursively(
    dir_path: Path,
    dry_run: bool,
    verbose: bool,
    rng: random.SystemRandom,
) -> Tuple[int, int, int]:
    existing = load_existing_config(dir_path / CONFIG_FILE_NAME)
    folders, notes = iter_children(dir_path)
    config = build_node_config(dir_path, folders, notes, existing, rng)

    if verbose or dry_run:
        print(f"{dir_path / CONFIG_FILE_NAME}  folders={len(folders)} md={len(notes)}")
    if not dry_run:
        write_json(dir_path / CONFIG_FILE_NAME, config)

    total_configs = 1
    total_folders = len(folders)
    total_notes = len(notes)

    for folder in folders:
        sub_configs, sub_folders, sub_notes = rebuild_recursively(folder, dry_run, verbose, rng)
        total_configs += sub_configs
        total_folders += sub_folders
        total_notes += sub_notes

    return total_configs, total_folders, total_notes


def main() -> int:
    args = parse_args()
    root = args.notebook_root.resolve()

    if not root.is_dir():
        raise SystemExit(f"notebook root is not a directory: {root}")

    rng = random.SystemRandom()
    configs, folders, notes = rebuild_recursively(root, args.dry_run, args.verbose, rng)

    mode = "Dry-run" if args.dry_run else "Rebuilt"
    print(f"{mode} vx.json count: {configs}")
    print(f"Scanned folder count: {folders}")
    print(f"Scanned markdown count: {notes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

