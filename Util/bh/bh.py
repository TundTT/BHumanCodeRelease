#!/usr/bin/env python3
"""Start script for the bh command line (see ../../bh and ../../bh.cmd)."""

import os
import sys

if sys.version_info < (3, 9):
    sys.exit("bh needs Python 3.9 or newer (this is %d.%d)." % sys.version_info[:2])

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bhtool.cli import main  # noqa: E402

sys.exit(main())
