#!/usr/bin/env bash
# The input files of the workflow referee-import (TAX-27): the files or patterns of the input `found`,
# separated by spaces, each in referee/import/ and ending in .csv. Prints one file per line, in order,
# each once; stops when a value is not such a path or matches no file.
set -euo pipefail
found="${1:-}"
seen=()
read -r -a values <<< "$found"
if [ "${#values[@]}" -eq 0 ]; then
  echo "::error::found is empty" >&2
  exit 1
fi
pattern='^referee/import/[][A-Za-z0-9*?._-]+\.csv$'  # a name, or a pattern with * ? [..]
for value in "${values[@]}"; do
  if ! [[ "$value" =~ $pattern ]] || [[ "$value" == *..* ]]; then
    echo "::error::not a file in referee/import/: $value" >&2
    exit 1
  fi
  matches=()
  while IFS= read -r m; do matches+=("$m"); done < <(compgen -G "$value" | sort)
  if [ "${#matches[@]}" -eq 0 ]; then
    echo "::error::no file matches $value" >&2
    exit 1
  fi
  for m in "${matches[@]}"; do
    if [[ " ${seen[*]-} " != *" $m "* ]]; then
      seen+=("$m")
      echo "$m"
    fi
  done
done
