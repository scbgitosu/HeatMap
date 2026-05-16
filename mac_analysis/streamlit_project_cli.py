"""Parse ``--project`` for Streamlit apps in this folder.

Import from here (not ``shared.utils``) so ``streamlit run mac_analysis/...`` always
resolves the module via ``sys.path`` — avoids another ``shared`` on ``PYTHONPATH``.
"""
from __future__ import annotations

import argparse
import sys
from typing import List


def streamlit_app_argv() -> List[str]:
    """CLI args meant for the Streamlit script (not for Streamlit itself).

    ``streamlit run app.py -- --project survey_projects/foo`` inserts a ``--``
    token; some environments pass ``--project`` without it. Handle both.
    """
    argv = sys.argv[1:]
    if "--" in argv:
        return argv[argv.index("--") + 1 :]
    return argv


def parse_streamlit_project_args(
    default_project: str = "survey_projects/apartment_test",
) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=default_project)
    return parser.parse_known_args(streamlit_app_argv())[0]
