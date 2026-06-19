#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO="$ROOT/demo/atlas-docs-migration"
OUT="$ROOT/out/notary-evidence.json"
NOTARY_REPO="${NOTARY_REPO:-/tmp/notary-memory-kit-notary}"
NOTARY_COMMIT="${NOTARY_COMMIT:-6a7b73f8d15bca1b23be67f63446ffaac3b032af}"

cd "$ROOT"

rm -rf "$DEMO/.notary-memory-kit" "$ROOT/out"

echo "Using Notary revision: $NOTARY_COMMIT"

python3 -m notary_memory_kit.cli ingest "$DEMO"
python3 -m notary_memory_kit.cli facts "$DEMO"
python3 -m notary_memory_kit.cli search "$DEMO" "migration target"
python3 -m notary_memory_kit.cli export "$DEMO" --notary "$OUT"

if [[ ! -d "$NOTARY_REPO/.git" ]]; then
  rm -rf "$NOTARY_REPO"
  git clone --depth 1 https://github.com/Digitizers/notary.git "$NOTARY_REPO" >/tmp/notary-memory-kit-clone.log 2>&1
fi
git -C "$NOTARY_REPO" fetch --depth 1 origin "$NOTARY_COMMIT" >/tmp/notary-memory-kit-fetch.log 2>&1 || true
git -C "$NOTARY_REPO" checkout --detach "$NOTARY_COMMIT" >/tmp/notary-memory-kit-checkout.log 2>&1

cd "$NOTARY_REPO"
PYTHONPATH="$NOTARY_REPO" python3 benchmark/runner.py "$OUT"

cd "$ROOT"
PYTHONPATH="$ROOT" NOTARY_REPO="$NOTARY_REPO" python3 tests/test_demo_flow.py "$OUT"

SENSITIVE_PATTERN="$(printf '%s|%s|%s|%s|%s|%s|%s|%s' \
  "A""SMR" \
  "Trace""Memory" \
  "trace""memory" \
  "/""home""/""gidon" \
  "Open""Claw" \
  "Her""mes" \
  "shared""-agents" \
  "\\.""open""claw")"

if grep -R -n -E "$SENSITIVE_PATTERN" \
  README.md notary_memory_kit demo scripts tests \
  --exclude-dir=.notary-memory-kit \
  --exclude-dir=__pycache__; then
  echo "Sensitive-term audit failed" >&2
  exit 1
fi

git -C "$ROOT" check-ignore -q "$DEMO/.notary-memory-kit/store.json"
git -C "$ROOT" check-ignore -q "$ROOT/out/notary-evidence.json"
if [[ -n "$(git -C "$ROOT" status --porcelain -- "$DEMO/.notary-memory-kit" "$ROOT/out")" ]]; then
  echo "Generated outputs are not ignored cleanly" >&2
  exit 1
fi

echo "PASS: sensitive audit and generated-output checks"
