#!/usr/bin/env python3
"""CLI entry point for the Amazon Video Agent importer."""
from pathlib import Path
import sys

# Make the repository root importable when this file is invoked by path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integrations.amazon_video_agent.importer import main

if __name__ == "__main__":
    raise SystemExit(main())
