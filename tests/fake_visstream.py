"""A stand-in for `cliamp visstream`: emit (24 lines a second), silent (never writes) or die (3 lines, then exits)."""
import json
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "emit"
if mode == "silent":
    while True:
        time.sleep(3600)
n = 0
while mode != "die" or n < 3:
    print(json.dumps({"bands": [n % 10] * 4}), flush=True)
    n += 1
    time.sleep(1 / 24)
