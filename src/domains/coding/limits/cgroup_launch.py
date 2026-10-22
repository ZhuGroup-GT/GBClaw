"""Trusted host launcher; join the coding cgroup before running any worker command."""
import os
import sys

if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
    os.execvp(sys.argv[2], sys.argv[2:])
