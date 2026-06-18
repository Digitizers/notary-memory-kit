#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: scripts/audit_public_tree.sh /path/to/public-tree" >&2
  exit 2
fi

if [[ ! -d "$TARGET" ]]; then
  echo "Public tree candidate does not exist: $TARGET" >&2
  exit 2
fi

SENSITIVE_TERMS=(
  "A""SMR"
  "Trace""Memory"
  "trace""memory"
  "/""home""/""gidon"
  "Open""Claw"
  "open""claw"
  "Her""mes"
  "her""mes"
  "shared""-agents"
  "shared""_agents"
  "\\.""open""claw"
  "open""claw"
  "private""-""prototypes"
)
SENSITIVE_PATTERN="$(IFS='|'; echo "${SENSITIVE_TERMS[*]}")"

if grep -R -n -i -E "$SENSITIVE_PATTERN" "$TARGET" \
  --exclude-dir=.git \
  --exclude-dir=.notary-memory-kit \
  --exclude-dir=__pycache__ \
  --exclude='*.pyc'; then
  echo "Public tree audit failed: sensitive/private term found" >&2
  exit 1
fi

if find "$TARGET" \( -path '*/.notary-memory-kit/*' -o -path '*/out/*' \) -type f | grep -q .; then
  echo "Public tree audit failed: generated output files found" >&2
  exit 1
fi

PACKAGE_CLAIMS=(
  "p""ip install notary-memory-kit"
  "n""pm install notary-memory-kit"
  "p""ypi"
  "n""pm package"
  "package ""publication"
  "published ""package"
)
PACKAGE_PATTERN="$(IFS='|'; echo "${PACKAGE_CLAIMS[*]}")"

if grep -R -n -i -E "$PACKAGE_PATTERN" "$TARGET" \
  --exclude-dir=.git \
  --exclude-dir=__pycache__ \
  --exclude='*.pyc'; then
  echo "Public tree audit failed: unsupported package/install claim found" >&2
  exit 1
fi

echo "PASS: public tree audit draft"
