#!/usr/bin/env bash
# List covered files that changed since the sync commit, in the checkout that holds the cwd.
# Usage: drift.sh [repo-dir]   (default: the git checkout of the current directory)
# Reads the sync commit and the covered files from this skill's SKILL.md. Counts commits after
# the sync commit and uncommitted edits. Exit 0 when nothing covered changed, 1 when something did.
set -euo pipefail

SKILL_MD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/SKILL.md"
REPO="$(git -C "${1:-.}" rev-parse --show-toplevel)"

SYNC="$(sed -n 's/^Sync commit: `\([0-9a-f]\{7,40\}\)`.*/\1/p' "$SKILL_MD" | head -n1)"
[[ -n "$SYNC" ]] || { echo "no 'Sync commit:' line in $SKILL_MD" >&2; exit 2; }
git -C "$REPO" cat-file -e "$SYNC^{commit}" 2>/dev/null || { echo "sync commit $SYNC not in $REPO" >&2; exit 2; }

mapfile -t COVERED < <(sed -n 's/^| `\(backend\/[^`]*\)`.*/\1/p' "$SKILL_MD" | sort -u)
[[ "${#COVERED[@]}" -gt 0 ]] || { echo "no coverage rows in $SKILL_MD" >&2; exit 2; }

CHANGED="$(git -C "$REPO" diff --name-only "$SYNC" -- "${COVERED[@]}")"
echo "checkout: $REPO at $(git -C "$REPO" rev-parse --short HEAD), sync commit: $SYNC"
if [[ -z "$CHANGED" ]]; then
  echo "no covered file changed since the sync commit"
  exit 0
fi
echo "covered files changed since the sync commit (committed or not):"
printf '  %s\n' $CHANGED
exit 1
