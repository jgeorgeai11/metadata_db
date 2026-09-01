"""Shared path setup for pgconn unit tests."""

import sys
from pathlib import Path

# The pgconn package lives at code/lib/pgconn; put code/lib on the path
# so `import pgconn` resolves regardless of pytest's rootdir handling —
# the same directory every consumer inserts before importing it.
LIB_DIR = Path(__file__).resolve().parents[2]
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
