"""The checking helper the test scripts share. check() prints one line per check and counts the
failures; finish() prints the summary and ends the script with the exit code run_all.py reads."""

import sys

fails = 0


def check(name, cond, extra=""):
    global fails
    fails += not cond
    print(("ok  " if cond else "BAD ") + name + (f"   <- {extra}" if extra and not cond else ""))


def finish():
    print("\nALL PASSED" if not fails else f"\nFAILED ({fails} failing)")
    sys.exit(1 if fails else 0)


def relay(label, lines):
    """Prints the ok/BAD lines of a Node test run under a label and counts its failures."""
    global fails
    for line in lines:
        if line.startswith(("ok", "BAD")):
            print(f"{label}: " + line)
            fails += line.startswith("BAD")
