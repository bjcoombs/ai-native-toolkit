#!/usr/bin/env bash
# Build standalone skill ZIPs from the plugin SKILL.md source files.
#
# Usage:
#   scripts/build-standalone-skills.sh              # build all skills
#   scripts/build-standalone-skills.sh assess       # build one skill by name
#   scripts/build-standalone-skills.sh --dest ~/Desktop  # override output dir
#
# Output: dist/standalone-skills/<name>.zip  (default)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$REPO_ROOT/dist/standalone-skills"

SKILLS_TO_BUILD=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest)
      if [[ $# -lt 2 || "$2" == -* ]]; then
        echo "Error: --dest requires a value" >&2; exit 1
      fi
      DEST="$2"; shift 2
      ;;
    --dest=*) DEST="${1#*=}"; shift ;;
    -*)        echo "Unknown flag: $1" >&2; exit 1 ;;
    *)         SKILLS_TO_BUILD+=("$1"); shift ;;
  esac
done

mkdir -p "$DEST"

cd "$SCRIPT_DIR"
# Use `uv run` so the script honours scripts/pyproject.toml's `requires-python`
# and provisions a matching interpreter even when system python3 is older.
uv run python - "$DEST" "$REPO_ROOT" ${SKILLS_TO_BUILD[@]+"${SKILLS_TO_BUILD[@]}"} <<'PYEOF'
import sys
from pathlib import Path

dest = Path(sys.argv[1])
repo_root = Path(sys.argv[2])
requested = set(sys.argv[3:]) if len(sys.argv) > 3 else None

from standalone_skill_config import SKILLS
from transform_skill import build_standalone_skill_zip

if requested:
    unknown = sorted(requested - set(SKILLS.keys()))
    if unknown:
        print(f"Unknown skill(s): {', '.join(unknown)}", file=sys.stderr)
        sys.exit(1)

results: dict[str, str] = {}
for name, cfg in SKILLS.items():
    if requested and name not in requested:
        continue
    out_zip = dest / f"{cfg['standalone_name']}.zip"
    try:
        display_path = out_zip.relative_to(repo_root)
    except ValueError:
        display_path = out_zip
    print(f"Building {name} -> {display_path} ...", flush=True)
    bundle_files = {
        dest_rel: repo_root / src_rel
        for dest_rel, src_rel in cfg.get("bundle_files", {}).items()
    }
    issues = build_standalone_skill_zip(
        skill_source_dir=repo_root / cfg["source_dir"],
        out_zip=out_zip,
        standalone_name=cfg["standalone_name"],
        standalone_description=cfg["standalone_description"],
        replacements=cfg["replacements"],
        exclude_dirs=frozenset(cfg["exclude_dirs"]),
        bundle_files=bundle_files,
    )
    if issues:
        print(f"  FAIL — {len(issues)} issue(s):")
        for issue in issues:
            print(f"    {issue}")
        results[name] = "FAIL"
    else:
        size_kb = out_zip.stat().st_size // 1024
        print(f"  OK  ({size_kb} KB)")
        results[name] = "OK"

failed = [k for k, v in results.items() if v != "OK"]
if failed:
    print(f"\nFailed: {', '.join(failed)}", file=sys.stderr)
    sys.exit(1)
print(f"\n{len(results)} skill(s) built -> {dest}")
PYEOF
