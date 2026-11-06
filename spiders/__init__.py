"""Maintenance spiders: read-only checks that report problems in the music library.

They use the read-only database role and never change the database or any file.
Run them with:  python -m spiders.run   (from the TOTRsrv directory)
"""

import os
import pathlib

import dotenv

import paths

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
dotenv.load_dotenv(paths.env_file())

REPORT_PATH = pathlib.Path(
    os.environ.get("MAINT_REPORT", paths.home() / "reports" / "maintenance.json")
)
