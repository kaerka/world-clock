#!/usr/bin/env bash
# Convenience wrapper to run the Spectra-upper clock by hand (needs REAL root —
# the hzeller matrix driver mmaps /dev/mem). Pass "test" for the 3x3 grid pattern.
#
#   sudo ./run.sh          # clock
#   sudo ./run.sh test     # geometry/orientation test pattern
#
# rgbmatrix is imported from the prebuilt hzeller build already on this box.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
export PYTHONPATH=/home/kaerka/led-matrix-display/rpi-rgb-led-matrix/bindings/python
exec /usr/bin/python3 spectra_clock.py "$@"
