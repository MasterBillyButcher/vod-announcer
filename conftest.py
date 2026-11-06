"""pytest conftest - ensures the project root is importable."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
