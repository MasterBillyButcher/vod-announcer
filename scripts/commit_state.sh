#!/usr/bin/env bash
# Commit state/state.json back to the repo after a run.
# Called by the GitHub Actions workflow; safe to run locally too.
set -euo pipefail

cd "$(dirname "$0")/.."

git config user.name "vod-announcer-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

git add state/state.json

if git diff --cached --quiet; then
  echo "No state changes to commit."
  exit 0
fi

git commit -m "chore(state): update [skip ci]"

# Push with retries (another run may have pushed in between).
for attempt in 1 2 3; do
  if git push origin "$BRANCH"; then
    echo "State pushed."
    exit 0
  fi
  echo "Push failed (attempt $attempt) - rebasing on remote and retrying..."
  git fetch origin "$BRANCH"
  git rebase "origin/$BRANCH" || git rebase --abort
  sleep 5
done

echo "ERROR: could not push state after 3 attempts." >&2
exit 1
