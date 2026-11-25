import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ["COPILOT_PLUGIN_ROOT"] = str(root)
sys.path.insert(0, str(root / "src"))
from s2f.cli import main

sys.exit(main())
