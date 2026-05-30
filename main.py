#!/usr/bin/env python
import sys
from pathlib import Path

# Add src to path to ensure proper module resolution when running directly
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ril.cli import main

if __name__ == "__main__":
    main()
