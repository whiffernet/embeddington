#!/usr/bin/env bash
# Scope-hygiene gate for public gold artifacts (spec §3.3). Exit 1 on any hit.
set -euo pipefail
cd "$(dirname "$0")"
PATTERNS='/home/e|whiffernet/(brewing|enab|eportfolio|ernestine|curator|promptuary|homeschool)|langchain-output|spark-a4ad|ARANGO_ROOT_PASSWORD=[^$]'
if grep -rEn "$PATTERNS" --include='*.json' --include='*.md' .; then
  echo "SCOPE SCRUB FAILED — private references above" >&2
  exit 1
fi
echo "scope scrub clean"
