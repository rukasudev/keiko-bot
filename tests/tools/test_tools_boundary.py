"""`tools/` may import `app/`. `app/` may never import `tools/`.

The tooling exists to inspect the product, so it is allowed to read the
product's own constants and shapes. The reverse would put developer tooling on
the production import path and into the deploy image, and the failure mode is
silent: everything works locally, and the container dies on an import error.
"""
import os
import re

import pytest

pytestmark = pytest.mark.unit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMPORTS_TOOLS = re.compile(r"^\s*(?:from|import)\s+tools\b", re.MULTILINE)


def python_files(directory):
    for folder, _subfolders, filenames in os.walk(os.path.join(ROOT, directory)):
        if "__pycache__" in folder:
            continue
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(folder, filename)


def test_no_module_under_app_imports_the_tooling():
    offenders = [
        os.path.relpath(path, ROOT)
        for path in python_files("app")
        if IMPORTS_TOOLS.search(open(path, encoding="utf-8").read())
    ]

    assert offenders == [], (
        f"app/ must not depend on tools/: {offenders}. "
        "tools/ is excluded from the deploy image, so this import fails in prod."
    )


def test_the_tooling_is_excluded_from_the_deploy_image():
    with open(os.path.join(ROOT, ".dockerignore"), encoding="utf-8") as handle:
        ignored = {line.strip().rstrip("/") for line in handle}

    assert "tools" in ignored
