#!/usr/bin/env bash
#
# Regenerates the `main` branch (candidate-facing) from `answers` (full) by:
#   1. Copying the answers worktree onto main
#   2. Removing the _hiring_manager/ folder
#   3. Stripping [BUG-N] markers from .py files under app/ and tests/
#
# Run from the repo root. Requires a clean answers worktree.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

current_branch="$(git rev-parse --abbrev-ref HEAD)"

if [[ "$current_branch" != "answers" ]]; then
  echo "Switching to answers (was on $current_branch)..."
  git checkout answers
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: answers has uncommitted changes. Commit or stash first." >&2
  exit 1
fi

answers_sha="$(git rev-parse --short answers)"

# Move to main, replace its tree with answers'
if git show-ref --quiet refs/heads/main; then
  git checkout main
  git rm -rf --quiet . >/dev/null 2>&1 || true
  git checkout answers -- .
else
  git checkout --orphan main
  git rm -rf --quiet . >/dev/null 2>&1 || true
  git checkout answers -- .
fi

# Drop the answer key folder
rm -rf _hiring_manager

# Strip [BUG-N] markers from python sources under app/ and tests/
strip_bug_markers() {
  local f="$1"
  # Whole-line comments (only [BUG-N] content) -> delete the line
  sed -i '' -E '/^[[:space:]]*#[[:space:]]*\[BUG-[0-9]+\]/d' "$f"
  # Trailing comments on code lines -> drop the comment, keep code
  sed -i '' -E 's/[[:space:]]*#[[:space:]]*\[BUG-[0-9]+\].*$//' "$f"
}

while IFS= read -r -d '' f; do
  strip_bug_markers "$f"
done < <(find app tests -name '*.py' -print0 2>/dev/null)

# Sanity check: no [BUG- should survive on main
if git grep -nE '\[BUG-[0-9]+\]' -- 'app/**' 'tests/**' >/dev/null 2>&1; then
  echo "ERROR: [BUG-N] markers still present in main worktree after strip:" >&2
  git grep -nE '\[BUG-[0-9]+\]' -- 'app/**' 'tests/**' >&2 || true
  exit 1
fi

git add -A
if git diff --cached --quiet; then
  echo "No changes to commit on main."
else
  git commit -m "Sync from answers (${answers_sha})"
  echo "Committed sync from answers ${answers_sha}."
fi

git checkout answers
echo "Done. main is up to date; back on answers."
