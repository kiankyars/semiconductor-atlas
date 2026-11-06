#!/usr/bin/env python3
"""Validate manufacturing project records (all records, or the files given)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semiconductor_atlas.manufacturing_projects import main

if __name__ == "__main__":
    raise SystemExit(main())
