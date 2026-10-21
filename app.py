"""Root launcher for Streamlit Dashboard."""

from pathlib import Path
import sys

# Ensure src is in python path
src_path = str(Path(__file__).parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from ugr_booking.ui.app import main

main()
