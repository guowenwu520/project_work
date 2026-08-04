#!/usr/bin/env bash
set -Eeuo pipefail

# Self-contained canonical-v7 dataset entry point. This file intentionally
# does not source or invoke run_dataset.sh and has no XLSX/template-library
# synchronization step.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/run_dataset_canonical.py" "$@"
