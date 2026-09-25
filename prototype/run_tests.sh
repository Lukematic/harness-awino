#!/bin/bash
# Full adversarial suite for the Phase 0 prototype.
set -e
cd "$(dirname "$0")"
# Sweep stray sidecar temp dirs before and after the run. The EXIT trap
# guarantees the post-run sweep even when the suite fails or is
# interrupted, so a crashed run can never accumulate
# awino-sidecar-test-*/awino-home-test-* dirs.
python3 tests/temp_sweep.py
trap 'python3 tests/temp_sweep.py' EXIT
python3 -m unittest discover -s tests -t . -v
