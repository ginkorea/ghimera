"""Run identifier alphabet shared by every journal, archive and service boundary.

A run id is one path segment: it starts with an ASCII letter or digit and then
uses only ASCII letters, digits, ``.``, ``_`` and ``-``. The leading character
rule excludes ``.``/``..`` and hidden names; ``/`` and every other separator are
outside the alphabet, so a valid id can never name another directory.
"""

import re

RUN_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]*"
RUN_ID_SCHEMA_PATTERN = "^" + RUN_ID_PATTERN + "$"


def valid_run_id(run_id: object) -> bool:
    return isinstance(run_id, str) and re.fullmatch(RUN_ID_PATTERN, run_id) is not None
