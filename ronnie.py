#!/usr/bin/env python3
import os
import sys

# Add the directory containing this script to sys.path to allow imports of the `ronnie` package
# without installation.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from ronnie.cli import main

if __name__ == "__main__":
    main()
