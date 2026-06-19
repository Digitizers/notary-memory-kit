#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NOTARY_REPO="${NOTARY_REPO:-/tmp/notary-memory-kit-notary}"
NOTARY_COMMIT="${NOTARY_COMMIT:-6a7b73f8d15bca1b23be67f63446ffaac3b032af}"

cd "$ROOT"

rm -rf "$ROOT"/demo/*/.notary-memory-kit "$ROOT/out"

echo "Using Notary revision: $NOTARY_COMMIT"

if [[ ! -d "$NOTARY_REPO/.git" ]]; then
  rm -rf "$NOTARY_REPO"
  git clone --depth 1 https://github.com/Digitizers/notary.git "$NOTARY_REPO" >/tmp/notary-memory-kit-clone.log 2>&1
fi
git -C "$NOTARY_REPO" fetch --depth 1 origin "$NOTARY_COMMIT" >/tmp/notary-memory-kit-fetch.log 2>&1 || true
git -C "$NOTARY_REPO" checkout --detach "$NOTARY_COMMIT" >/tmp/notary-memory-kit-checkout.log 2>&1

run_scenario() {
  local slug="$1"
  local query="$2"
  local expected_facts="$3"
  local expected_authorities="$4"
  local warning_fact="$5"
  local warning_surface="$6"
  local unauthorized_fact="$7"
  local unauthorized_agent="$8"
  local overwritten_fact="$9"
  local correction_fact="${10}"
  local correction_agent="${11}"
  local corrected_fact="${12}"

  local demo="$ROOT/demo/$slug"
  local out="$ROOT/out/$slug-notary-evidence.json"

  echo
  echo "== Scenario: $slug =="

  python3 -m notary_memory_kit.cli ingest "$demo"
  python3 -m notary_memory_kit.cli facts "$demo"
  python3 -m notary_memory_kit.cli search "$demo" "$query"
  python3 -m notary_memory_kit.cli export "$demo" --notary "$out"

  cd "$NOTARY_REPO"
  PYTHONPATH="$NOTARY_REPO" python3 benchmark/runner.py "$out"

  cd "$ROOT"
  PYTHONPATH="$ROOT" NOTARY_REPO="$NOTARY_REPO" python3 tests/test_demo_flow.py \
    "$out" "$expected_facts" "$expected_authorities" \
    "$warning_fact" "$warning_surface" "$unauthorized_fact" "$unauthorized_agent" "$overwritten_fact" \
    "$correction_fact" "$correction_agent" "$corrected_fact"
}

run_scenario \
  "atlas-docs-migration" \
  "migration target" \
  8 3 \
  "atlas-f005" "project_scope" "atlas-f005" "builder" "atlas-f001" \
  "atlas-f004" "reviewer" "atlas-f006"

run_scenario \
  "beacon-launch-readiness" \
  "launch target" \
  5 3 \
  "beacon-f003" "launch_scope" "beacon-f003" "engineer" "beacon-f001" \
  "beacon-f005" "qa" "beacon-f004"

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

find "$ROOT/demo" -path '*/.notary-memory-kit/store.json' -print0 | while IFS= read -r -d '' store_path; do
  git -C "$ROOT" check-ignore -q "$store_path"
done
find "$ROOT/out" -type f -name '*-notary-evidence.json' -print0 | while IFS= read -r -d '' out_path; do
  git -C "$ROOT" check-ignore -q "$out_path"
done
if [[ -n "$(git -C "$ROOT" status --porcelain -- "$ROOT/out" "$ROOT"/demo/*/.notary-memory-kit)" ]]; then
  echo "Generated outputs are not ignored cleanly" >&2
  exit 1
fi

echo "PASS: sensitive audit and generated-output checks"
