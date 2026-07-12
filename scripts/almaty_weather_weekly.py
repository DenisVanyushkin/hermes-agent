#!/usr/bin/env python3
from pathlib import Path
import os
import sys

repo = Path(os.environ.get("HERMES_REPO", Path.home() / ".hermes" / "hermes-agent"))
sys.path.insert(0, str(repo))

from hermes_cli.almaty_weather import main

raise SystemExit(main(["weekly"]))
