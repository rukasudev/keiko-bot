"""`make command name=<x> [form=yes]` writes a whole command folder, and nothing else.

The files come out filled, the Python parses, the copy carries both locales and the
form compiles; an existing command is refused untouched. Once the registry exists, the
templates may import only modules the tree has.
"""

import ast
import os
import subprocess
import sys

import pytest
import yaml

from app.core.copy_schema import CommandCopy
from tests.helpers.commands import form_compiler

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.keiko.command import ScaffoldError, main, scaffold  # noqa: E402

pytestmark = pytest.mark.unit

REGISTRY = ("app/core/commands.py", "app/commands/__init__.py")
NO_REGISTRY = (
    "the command registry does not exist yet (app/core/commands.py, "
    "app/commands/__init__.py): the command checks start with it"
)


def _relative(paths, root):
    return sorted(path.relative_to(root).as_posix() for path in paths)


def test_a_plain_command_gets_its_folder_its_copy_and_its_test(tmp_path):
    written = scaffold("good_boy", root=tmp_path)

    assert _relative(written, tmp_path) == [
        "app/commands/good_boy/__init__.py",
        "app/commands/good_boy/cog.py",
        "app/commands/good_boy/copy.yml",
        "app/commands/good_boy/service.py",
        "tests/commands/__init__.py",
        "tests/commands/good_boy/__init__.py",
        "tests/commands/good_boy/test_good_boy.py",
    ]
    declaration = (tmp_path / "app/commands/good_boy/__init__.py").read_text()
    assert 'key="good_boy"' in declaration
    assert "cogs=(GoodBoy,)" in declaration
    assert "form=" not in declaration and "copy=" not in declaration
    assert not (tmp_path / "app/commands/good_boy/form.yml").exists()


def test_a_command_with_a_form_gets_a_form_that_compiles(tmp_path):
    written = scaffold("good_boy", form=True, root=tmp_path)
    form = tmp_path / "app/commands/good_boy/form.yml"

    assert form in written
    assert tmp_path / "app/commands/good_boy/copy.yml" in written
    definition = form_compiler()("good_boy", yaml.safe_load(form.read_text()))
    assert [step.key for step in definition.steps] == ["form", "message", "confirm"]


@pytest.mark.parametrize("form", [False, True])
def test_every_file_is_filled_and_reads(tmp_path, form):
    for path in scaffold("good_boy", form=form, root=tmp_path):
        text = path.read_text()
        assert "${" not in text, path
        if path.suffix == ".py":
            ast.parse(text)
        elif path.name == "copy.yml":
            copy = yaml.safe_load(text)
            assert set(copy["slash"]["description"]) == {"en-us", "pt-br"}
            assert all(copy["slash"]["description"].values())
            CommandCopy.model_validate(copy)


def test_an_existing_command_is_refused_untouched(tmp_path):
    scaffold("good_boy", root=tmp_path)
    service = tmp_path / "app/commands/good_boy/service.py"
    service.write_text("kept")

    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold("good_boy", form=True, root=tmp_path)
    assert service.read_text() == "kept"
    assert not (tmp_path / "app/commands/good_boy/form.yml").exists()


@pytest.mark.parametrize("name", ["", "Good", "good-boy", "1good", "class"])
def test_a_name_that_is_no_folder_name_is_refused(tmp_path, name):
    with pytest.raises(ScaffoldError, match="not a command folder name"):
        scaffold(name, root=tmp_path)
    assert not (tmp_path / "app").exists()


def test_the_command_line_says_why_it_refused(capsys):
    assert main(["Good"]) == 1
    assert "not a command folder name" in capsys.readouterr().err


def test_make_command_passes_the_name_and_the_form():
    dry_run = subprocess.run(
        ["make", "-n", "command", "name=good_boy", "form=yes"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    plain = subprocess.run(
        ["make", "-n", "command", "name=good_boy"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout

    assert "-m scripts.keiko.command good_boy --form" in dry_run
    assert "-m scripts.keiko.command good_boy" in plain
    assert "--form" not in plain


@pytest.mark.skipif(
    not all(os.path.isfile(os.path.join(ROOT, path)) for path in REGISTRY),
    reason=NO_REGISTRY,
)
@pytest.mark.parametrize("form", [False, True])
def test_the_templates_import_only_modules_the_tree_has(tmp_path, form):
    missing = []
    for path in scaffold("good_boy", form=form, root=tmp_path):
        if path.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            else:
                continue
            own = module.startswith("app.commands.good_boy")
            if module.startswith("app.") and not own:
                base = os.path.join(ROOT, *module.split("."))
                if not (
                    os.path.isfile(f"{base}.py")
                    or os.path.isfile(os.path.join(base, "__init__.py"))
                ):
                    missing.append(f"{path.name}: {module}")
    assert missing == [], "point the templates at the modules' new homes"
