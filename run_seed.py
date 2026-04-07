import sys
import os
from pathlib import Path

# Add root to sys.path to resolve 'pipeline' package
root_dir = str(Path(__file__).resolve().parent)
if root_dir not in sys.path:
    sys.path.append(root_dir)

from data.seed_tenants import seed_tenants

if __name__ == "__main__":
    seed_tenants()
