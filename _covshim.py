import os
import sys

if os.environ.get("COVERAGE_PROCESS_START"):
    import coverage

    coverage.process_startup()
