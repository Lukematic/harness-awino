"""Wrapper: run the sidecar with faulthandler dumping the traceback after N sec.

Usage: python win_sidecar_wrap.py <seconds> <sidecar.py>
If the sidecar stalls, faulthandler prints the Python stack of every thread
to stderr, then exits — turning a silent hang into a located hang.
"""
import faulthandler
import runpy
import sys

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
target = sys.argv[2]
faulthandler.enable()
faulthandler.dump_traceback_later(secs, exit=True)
sys.argv = [target]
runpy.run_path(target, run_name="__main__")
