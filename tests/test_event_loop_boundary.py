"""No coroutine anywhere in `app/` calls a blocking function directly.

Everything runs on one event loop: the gateway heartbeat, every interaction, every
listener and job. `requests`, the pymongo and Redis clients, `time.sleep`, the language
detection and every synchronous function that reaches one of them block it, so a
coroutine hands them to a thread (`asyncio.to_thread`) instead of calling them. Rule 18
of `.claude/rules/code-style.md` says it for `app/settings/`, where
`tests/forms/test_boundary.py` refuses the imports; here the whole app is read call by
call, because a coroutine may import a module that blocks and still only hand its
functions to a thread.

How it reads: a synchronous function blocks when it calls (or, synchronously, hands
along) a blocking primitive or another blocking function of `app/`. Names are resolved
through each module's imports, a method through `self`, a def nested in a function by
its name, and a client the bot holds (`bot.twitch`, `bot.youtube`, `bot.reminder`,
`bot.notion`) through `DiscordBot.__init__`. A name assigned in the function or at the
top of its module stands for what it holds: a receiver, a collection, an instance of a
class of `app/`, a `functools.partial` or a lambda; and a function whose whole body
returns a client or a collection stands for what it returns. A coroutine that calls a
blocking function directly is a finding. A finding that has to stay for now is listed in
`ALLOWED` with its reason, and an entry that no longer matches anything fails too, so the
list only shrinks.

What it cannot see: `open` and the other builtins that touch files, `subprocess`, a
method a class inherits (it is found only on the class that defines it), and a
synchronous helper of higher order that a coroutine hands a blocking function to
(`helper(things.find_thing)`), which blocks only when its own body names what it calls.
"""
import ast
import os
from typing import Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("event_loop")]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRIMITIVES = {
    "time.sleep",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.patch", "requests.head", "requests.options", "requests.request",
}
PRIMITIVE_ROOTS = ("app.mongo_client.", "app.redis_client.", "detectlanguage.")
HANDOFFS = {"asyncio.to_thread", "threading.Thread"}

ADMIN_COMMAND = (
    "an operator command in the admin guild, run by hand; its file is outside the loop "
    "PR of wave 2"
)
INSPECTION_VIEW = "operator inspection views, follow-up"
COUNTERS_PR = "moved off the loop by the counters PR"
ALLOWED: Dict[str, str] = {
    "app/cogs/admin/__init__.py::Admin.forget_guild -> "
    "app.data.analytics.delete_analytics_by_guild": ADMIN_COMMAND,
    "app/cogs/admin/__init__.py::Admin.show_guild_journey -> "
    "app.services.admin_analytics.build_guild_journey_embed": ADMIN_COMMAND,
    "app/cogs/admin/configs.py::Configs.show_config -> "
    "app.services.admin.get_admin_configs": ADMIN_COMMAND,
    "app/cogs/admin/configs.py::Configs.update_config -> "
    "app.services.admin.update_admin_configs": ADMIN_COMMAND,
    "app/cogs/admin/subscriptions.py::Subscriptions.show_reminder_subscriptions -> "
    "app.services.subscriptions.get_reminder_subscriptions": ADMIN_COMMAND,
    "app/cogs/admin/subscriptions.py::Subscriptions.show_twitch_subscriptions -> "
    "app.services.subscriptions.get_twitch_subscriptions": ADMIN_COMMAND,
    "app/services/config.py::update_activity -> app.data.config.update_db_configs": (
        ADMIN_COMMAND
    ),
    "app/services/config.py::update_description -> app.data.config.update_db_configs": (
        ADMIN_COMMAND
    ),
    "app/services/config.py::update_status -> app.data.config.update_db_configs": (
        ADMIN_COMMAND
    ),
    "app/services/block_links.py::check_message -> "
    "app.services.block_links.record_blocked_links": COUNTERS_PR,
    "app/services/block_links.py::send_blocked_links_stats_message -> "
    "app.services.block_links.get_blocked_links_stats": COUNTERS_PR,
    "app/settings/discord/callbacks.py::Runtime._open -> "
    "app.settings.discord.observability.open_journey": (
        "the journey service reads in a thread once PR6 merges, so only the no-loop "
        "fallback remains"
    ),
    "app/views/log_inspection.py::LogInspectionView._get_offline_guild_info_embed -> "
    "app.data.moderations.find_moderations_by_guild": INSPECTION_VIEW,
    "app/views/log_inspection.py::LogInspectionView._get_offline_guild_info_embed -> "
    "app.views.log_inspection.LogInspectionView._add_commands_status": INSPECTION_VIEW,
    "app/views/log_inspection.py::LogInspectionView.get_guild_info_embed -> "
    "app.data.moderations.find_moderations_by_guild": INSPECTION_VIEW,
    "app/views/log_inspection.py::LogInspectionView.get_guild_info_embed -> "
    "app.views.log_inspection.LogInspectionView._add_commands_status": INSPECTION_VIEW,
    "app/views/user_inspection.py::UserInspectionView.send -> "
    "app.views.user_inspection.UserInspectionView._get_embed": INSPECTION_VIEW,
}


Names = List[str]


class Function(NamedTuple):
    """A function of `app/`, with the class it belongs to and the defs nested in it."""

    qualname: str
    node: ast.AST
    owner: Optional[str]
    nested: Dict[str, str]


class Module:
    """One module of `app/`: its imports, aliases, definitions, classes and functions."""

    def __init__(self, root: str, path: str) -> None:
        self.path = os.path.relpath(path, root)
        dotted = self.path[:-3].replace(os.sep, ".")
        self.is_package = dotted.endswith(".__init__")
        self.name = dotted[: -len(".__init__")] if self.is_package else dotted
        with open(path, encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())
        self.imports: Dict[str, str] = {}
        self.aliases: Dict[str, Names] = {}
        self.definitions: Dict[str, str] = {}
        self.classes: Set[str] = set()
        self.functions: List[Function] = []
        for node in self.tree.body:
            self.bind_import(node, self.imports)
        self.collect(self.tree.body, self.name, None)

    def bind_import(self, node: ast.AST, table: Dict[str, str]) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    table[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    table[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = self.name.split(".")
                package = parts if self.is_package else parts[:-1]
                package = package[: len(package) - (node.level - 1)]
                base = ".".join(package + ([base] if base else []))
            for alias in node.names:
                table[alias.asname or alias.name] = f"{base}.{alias.name}"

    def collect(self, body: List[ast.stmt], prefix: str, owner: Optional[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{node.name}"
                if owner is None:
                    self.definitions[node.name] = qualname
                inner = [
                    child for child in ast.walk(node)
                    if child is not node
                    and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                nested = {child.name: f"{qualname}.{child.name}" for child in inner}
                self.functions.append(Function(qualname, node, owner, nested))
                for child in inner:
                    self.functions.append(Function(nested[child.name], child, owner, nested))
            elif isinstance(node, ast.ClassDef):
                qualname = f"{prefix}.{node.name}"
                self.definitions[node.name] = qualname
                self.classes.add(qualname)
                self.collect(node.body, qualname, qualname)
            elif owner is None:
                target = _assigned_name(node)
                expansion = _names(node.value) if target else None
                if target and expansion:
                    self.aliases[target] = expansion


Context = Tuple[Module, Dict[str, str], Dict[str, Names], Optional[str]]


class Scan:
    """The blocking functions of `app/`, and the coroutines that call one directly."""

    def __init__(self, root: str) -> None:
        self.modules = {
            module.name: module for module in map(lambda path: Module(root, path), _files(root))
        }
        self.known = {
            function.qualname
            for module in self.modules.values()
            for function in module.functions
        }
        self.classes = {
            qualname for module in self.modules.values() for qualname in module.classes
        }
        self.clients = self._clients()
        self.returns = self._returns()
        self.calls = {
            function.qualname: (module, function.node, list(self._calls(module, function)))
            for module in self.modules.values()
            for function in module.functions
        }
        self.blocking = self._blocking()

    def findings(self) -> Set[str]:
        found = set()
        for qualname, (module, node, calls) in self.calls.items():
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for callee in calls:
                if self.blocks(callee):
                    found.add(f"{module.path}::{_local(qualname, module)} -> {callee}")
        return found

    def blocks(self, callee: str) -> bool:
        return callee in PRIMITIVES or callee.startswith(PRIMITIVE_ROOTS) or (
            callee in self.blocking
        )

    def _blocking(self) -> Set[str]:
        blocking: Set[str] = set()
        changed = True
        while changed:
            changed = False
            for qualname, (_, node, calls) in self.calls.items():
                if isinstance(node, ast.AsyncFunctionDef) or qualname in blocking:
                    continue
                if any(
                    callee in PRIMITIVES
                    or callee.startswith(PRIMITIVE_ROOTS)
                    or callee in blocking
                    for callee in calls
                ):
                    blocking.add(qualname)
                    changed = True
        return blocking

    def _clients(self) -> Dict[str, str]:
        bot = self.modules.get("app.bot")
        clients: Dict[str, str] = {}
        if bot is None:
            return clients
        for function in bot.functions:
            if not function.qualname.endswith(".DiscordBot.__init__"):
                continue
            for statement in ast.walk(function.node):
                if not (
                    isinstance(statement, ast.Assign)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                ):
                    continue
                created = bot.imports.get(statement.value.func.id)
                for target in statement.targets:
                    if (
                        created
                        and isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        clients[target.attr] = created
        return clients

    def _returns(self) -> Dict[str, Tuple[Context, Names]]:
        """The functions whose whole body hands back a client or a collection."""
        returns: Dict[str, Tuple[Context, Names]] = {}
        for module in self.modules.values():
            for function in module.functions:
                if not isinstance(function.node, ast.FunctionDef):
                    continue
                body = [statement for statement in function.node.body if not _docstring(statement)]
                if len(body) != 1 or not isinstance(body[0], ast.Return):
                    continue
                names = _names(body[0].value) if body[0].value else None
                if names and "()" not in names:
                    context = (module, dict(function.nested), dict(module.aliases), function.owner)
                    returns[function.qualname] = (context, names)
        return returns

    def _calls(self, module: Module, function: Function) -> Iterator[str]:
        local = dict(function.nested)
        for node in ast.walk(function.node):
            module.bind_import(node, local)
        context = (module, local, self._aliases(module, function, local), function.owner)
        coroutine = isinstance(function.node, ast.AsyncFunctionDef)
        for node in _own_nodes(function.node, coroutine):
            if not isinstance(node, ast.Call):
                continue
            callee = self._resolve(node.func, context)
            if callee:
                yield callee
            if coroutine or callee in HANDOFFS:
                continue
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                handed = self._resolve(argument, context)
                if handed:
                    yield handed

    def _aliases(
        self, module: Module, function: Function, local: Dict[str, str]
    ) -> Dict[str, Names]:
        """What each local name holds: a receiver, a collection, a partial or a lambda."""
        parameters = {
            node.arg for node in ast.walk(function.node.args) if isinstance(node, ast.arg)
        }
        aliases = {
            name: names
            for name, names in module.aliases.items()
            if name not in parameters and name not in local
        }
        context = (module, local, aliases, function.owner)
        assignments = sorted(
            (node for node in _own_nodes(function.node, True) if _assigned_name(node)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for statement in assignments:
            expansion = self._expansion(statement.value, context)
            if expansion:
                aliases[_assigned_name(statement)] = expansion
        return aliases

    def _expansion(self, value: Optional[ast.AST], context: Context) -> Optional[Names]:
        if isinstance(value, ast.Lambda) and isinstance(value.body, ast.Call):
            return _names(value.body.func)
        if (
            isinstance(value, ast.Call)
            and value.args
            and self._resolve(value.func, context) == "functools.partial"
        ):
            return _names(value.args[0])
        return _names(value) if value is not None else None

    def _resolve(self, expression: ast.AST, context: Context) -> Optional[str]:
        names = _names(expression)
        return self._resolve_names(names, context, 0) if names else None

    def _resolve_names(self, names: Names, context: Context, depth: int) -> Optional[str]:
        module, local, aliases, owner = context
        root = names[0]
        if depth > 10:
            return None
        if root in aliases:
            return self._resolve_names([*aliases[root], *names[1:]], context, depth + 1)
        if "()" in names:
            return self._through_a_call(names, context, depth) or self._client_method(names)
        resolved = None
        if root == "self" and owner and len(names) == 2:
            resolved = f"{owner}.{names[1]}"
        elif root in local:
            resolved = self._canonical(".".join([local[root], *names[1:]]))
        elif root in module.imports:
            resolved = self._canonical(".".join([module.imports[root], *names[1:]]))
        elif root in module.definitions:
            resolved = self._canonical(".".join([module.definitions[root], *names[1:]]))
        if resolved in self.known:
            return resolved
        return self._client_method(names) or resolved

    def _client_method(self, names: Names) -> Optional[str]:
        if len(names) >= 3 and names[-2] in self.clients:
            return f"{self.clients[names[-2]]}.{names[-1]}"
        return None

    def _through_a_call(self, names: Names, context: Context, depth: int) -> Optional[str]:
        """A method of what a call made: an instance of a class of `app/`, or what a
        function that only returns a client or a collection hands back."""
        cut = names.index("()")
        made, rest = names[:cut], names[cut + 1:]
        maker = self._resolve_names(made, context, depth + 1) if rest else None
        if maker in self.classes:
            method = ".".join([maker, *rest])
            return method if method in self.known else None
        if maker in self.returns:
            returned_context, returned = self.returns[maker]
            return self._resolve_names([*returned, *rest], returned_context, depth + 1)
        return None

    def _canonical(self, dotted: str, depth: int = 0) -> str:
        parts = dotted.split(".")
        for cut in range(len(parts), 0, -1):
            module = self.modules.get(".".join(parts[:cut]))
            if module is None:
                continue
            rest = parts[cut:]
            if not rest:
                return dotted
            if rest[0] in module.definitions:
                return ".".join([module.definitions[rest[0]], *rest[1:]])
            if rest[0] in module.imports and depth < 10:
                return self._canonical(".".join([module.imports[rest[0]], *rest[1:]]), depth + 1)
            return dotted
        return dotted


def _files(root: str) -> Iterator[str]:
    for folder, _subfolders, filenames in os.walk(os.path.join(root, "app")):
        if "__pycache__" in folder:
            continue
        for filename in sorted(filenames):
            if filename.endswith(".py") and not filename.endswith("_test.py"):
                yield os.path.join(folder, filename)


def _own_nodes(function: ast.AST, coroutine: bool) -> Iterator[ast.AST]:
    skipped: Tuple[type, ...] = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    if coroutine:
        skipped += (ast.Lambda,)
    stack = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, skipped):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _assigned_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target: ast.AST = node.targets[0]
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        target = node.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def _docstring(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _names(expression: ast.AST) -> Optional[List[str]]:
    names: List[str] = []
    while isinstance(expression, (ast.Attribute, ast.Subscript)):
        names.append(expression.attr if isinstance(expression, ast.Attribute) else "[]")
        expression = expression.value
    if isinstance(expression, ast.Name):
        return [expression.id, *reversed(names)]
    if isinstance(expression, ast.Call):
        inner = _names(expression.func)
        return [*inner, "()", *reversed(names)] if inner else None
    return None


def _local(qualname: str, module: Module) -> str:
    return qualname[len(module.name) + 1:]


def test_no_coroutine_calls_a_blocking_function():
    found = Scan(ROOT).findings()

    assert sorted(found - set(ALLOWED)) == [], (
        "hand the call to a thread (`await asyncio.to_thread(...)`), or list it in "
        "ALLOWED with the reason it has to stay"
    )


def test_every_allowed_call_still_exists():
    """An entry whose call moved off the loop goes, so the list only shrinks."""
    assert sorted(set(ALLOWED) - Scan(ROOT).findings()) == []


def test_every_allowed_call_says_why():
    assert [entry for entry, reason in ALLOWED.items() if not reason.strip()] == []


def _write(root, path, source):
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(source, encoding="utf-8")


def test_the_scan_sees_every_way_a_coroutine_reaches_a_blocking_call(tmp_path):
    """The scan is a test too: a checker that went blind would pass everything."""
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/data/__init__.py", "")
    _write(tmp_path, "app/data/things.py", (
        "from app import mongo_client\n"
        "guild_database = mongo_client.guild\n"
        "def find_thing(guild_id):\n"
        "    return mongo_client.guild['things'].find_one({'guild_id': guild_id})\n"
        "def database():\n"
        "    \"\"\"The guild database.\"\"\"\n"
        "    return mongo_client.guild\n"
        "def count_things():\n"
        "    return database().things.count_documents({})\n"
        "def find_other(guild_id):\n"
        "    return guild_database.others.find_one({'guild_id': guild_id})\n"
        "def find_kept(guild_id):\n"
        "    kept = mongo_client.guild.kept\n"
        "    return kept.find_one({'guild_id': guild_id})\n"
    ))
    _write(tmp_path, "app/integrations/__init__.py", "")
    _write(tmp_path, "app/integrations/web.py", (
        "import requests\n"
        "class Client:\n"
        "    def fetch(self, url):\n"
        "        return requests.get(url, timeout=5)\n"
    ))
    _write(tmp_path, "app/bot.py", (
        "from app.integrations.web import Client\n"
        "class DiscordBot:\n"
        "    def __init__(self):\n"
        "        self.web = Client()\n"
    ))
    _write(tmp_path, "app/services/__init__.py", "")
    _write(tmp_path, "app/services/feature.py", (
        "import asyncio\n"
        "import functools\n"
        "import time\n"
        "from functools import partial\n"
        "from app import bot, mongo_client\n"
        "from app.data import things\n"
        "from app.integrations.web import Client\n"
        "def summary(guild_id):\n"
        "    return run(things.find_thing, guild_id)\n"
        "def run(operation, guild_id):\n"
        "    return operation(guild_id)\n"
        "async def direct(guild_id):\n"
        "    return things.find_thing(guild_id)\n"
        "async def through_a_helper(guild_id):\n"
        "    return summary(guild_id)\n"
        "async def through_the_bot(url):\n"
        "    return bot.web.fetch(url)\n"
        "async def sleeping():\n"
        "    time.sleep(1)\n"
        "async def through_a_nested_def(guild_id):\n"
        "    def load():\n"
        "        return things.find_thing(guild_id)\n"
        "    return load()\n"
        "async def through_a_receiver_in_a_local(url):\n"
        "    web = bot.web\n"
        "    return web.fetch(url)\n"
        "async def through_an_instance(url):\n"
        "    client = Client()\n"
        "    return client.fetch(url)\n"
        "async def through_a_collection_in_a_local(guild_id):\n"
        "    collection = mongo_client.guild.things\n"
        "    return collection.find_one({'guild_id': guild_id})\n"
        "async def through_a_partial(guild_id):\n"
        "    load = functools.partial(things.find_thing, guild_id)\n"
        "    later = partial(summary, guild_id)\n"
        "    return load(), later()\n"
        "async def through_data_functions(guild_id):\n"
        "    return things.count_things(), things.find_other(guild_id), things.find_kept(1)\n"
        "async def handed_to_a_thread(guild_id):\n"
        "    def load():\n"
        "        return things.find_thing(guild_id)\n"
        "    held = functools.partial(things.find_thing, guild_id)\n"
        "    await asyncio.to_thread(things.find_thing, guild_id)\n"
        "    await asyncio.to_thread(lambda: things.find_thing(guild_id))\n"
        "    await asyncio.to_thread(load)\n"
        "    await asyncio.to_thread(held)\n"
        "    return await asyncio.to_thread(summary, guild_id)\n"
    ))

    found = Scan(str(tmp_path)).findings()

    feature = "app/services/feature.py::"
    assert found == {
        f"{feature}direct -> app.data.things.find_thing",
        f"{feature}through_a_helper -> app.services.feature.summary",
        f"{feature}through_the_bot -> app.integrations.web.Client.fetch",
        f"{feature}sleeping -> time.sleep",
        f"{feature}through_a_nested_def -> app.services.feature.through_a_nested_def.load",
        f"{feature}through_a_receiver_in_a_local -> app.integrations.web.Client.fetch",
        f"{feature}through_an_instance -> app.integrations.web.Client.fetch",
        f"{feature}through_a_collection_in_a_local -> "
        "app.mongo_client.guild.things.find_one",
        f"{feature}through_a_partial -> app.data.things.find_thing",
        f"{feature}through_a_partial -> app.services.feature.summary",
        f"{feature}through_data_functions -> app.data.things.count_things",
        f"{feature}through_data_functions -> app.data.things.find_other",
        f"{feature}through_data_functions -> app.data.things.find_kept",
    }
