"""Streamlit Community Cloud entry point.

Streamlit Cloud (share.streamlit.io) defaults to a main module named
``streamlit_app.py`` in the repository root. The dashboard itself lives in
``app.py`` -- run locally with ``streamlit run app.py`` or
``streamlit run streamlit_app.py`` -- and this module just executes it so
there is a single source of truth for the UI.

The ``pages/`` directory next to this file is still picked up for the
multi-page sidebar navigation regardless of which entry point is used.
"""
from __future__ import annotations

import runpy
from pathlib import Path

_DASHBOARD = Path(__file__).resolve().with_name("app.py")

# Execute app.py as if it were the script Streamlit launched directly.
runpy.run_path(str(_DASHBOARD), run_name="__main__")
