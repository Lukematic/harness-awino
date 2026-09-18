#!/bin/bash
# Full adversarial suite for the Phase 0 prototype.
set -e
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -t . -v
