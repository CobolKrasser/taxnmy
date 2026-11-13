#!/usr/bin/env bash
# The budget counts every earlier run: the ones committed in referee/runs/ and the ones kept as
# artifacts of the workflow referee. An expired artifact whose run is not committed would make the
# count too low, so this stops if there is one. Needs GH_TOKEN and GITHUB_REPOSITORY.
set -euo pipefail
mkdir -p referee/runs
gh api --paginate "repos/${GITHUB_REPOSITORY}/actions/artifacts?per_page=100" \
  --jq '.artifacts[] | select(.name | startswith("referee-run-")) | "\(.id) \(.expired) \(.name)"' |
while read -r id expired name; do
  dir="referee/runs/${name#referee-run-}"
  if [ -e "$dir" ]; then continue; fi
  if [ "$expired" = "true" ]; then
    echo "::error::Run ${name#referee-run-} has expired and is not committed; its cost cannot be counted"
    exit 1
  fi
  gh api "repos/${GITHUB_REPOSITORY}/actions/artifacts/${id}/zip" > /tmp/run.zip
  mkdir -p "$dir"
  unzip -q /tmp/run.zip -d "$dir"
done
ls referee/runs
