#!/usr/bin/env python3
"""Start the Streamlit dashboard."""

import sys
import subprocess
from pathlib import Path


def main() -> None:
    app_path = Path(__file__).parent.parent / "src" / "bot" / "monitoring" / "dashboard" / "app.py"

    if not app_path.exists():
        print(f"Dashboard app not found: {app_path}")
        sys.exit(1)

    print("Starting dashboard at http://localhost:8501")
    subprocess.run(
        ["streamlit", "run", str(app_path), "--server.port=8501"],
        check=True,
    )


if __name__ == "__main__":
    main()
