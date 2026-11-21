"""The AST mutator: every mutation point of a function body, by statement."""

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

OPERATORS = ("compare", "negate", "drop-call", "return-none", "bool", "constant")

FLIPS = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}

BLOCKS = ("body", "orelse", "finalbody")

NOT_OWN = {*BLOCKS, "handlers", "cases", "decorator_list", "annotation", "returns"}

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class Mutant:
    """One mutation of one statement, addressed without line numbers."""

    path: str
    function: str
    statement: str
    operator: str
    occurrence: int
    line: int
    end_line: int
    start: int
    end: int
    text: str

    @property
    def id(self) -> str:
        """The stable address: file, function, statement, operator and occurrence."""
        return (
            f"{self.path}::{self.function}::{self.statement}"
            f"::{self.operator}#{self.occurrence}"
        )


def _line_starts(raw: bytes) -> List[int]:
    starts = [0]
    for index, byte in enumerate(raw):
        if byte == 0x0A:
            starts.append(index + 1)
    return starts


def _span(node: ast.AST, starts: Sequence[int]) -> Tuple[int, int]:
    start = starts[node.lineno - 1] + node.col_offset
    end = starts[node.end_lineno - 1] + node.end_col_offset
    return start, end


def _padded(text: str, node: ast.AST, wrap: bool) -> str:
    extra = "\n" * (node.end_lineno - node.lineno)
    if wrap:
        return f"({text}{extra})"
    return text + extra


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else ""


def _is_log_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "print"
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr in LOG_METHODS and "log" in _root_name(func).lower()


def _own_nodes(statement: ast.stmt) -> Iterator[ast.AST]:
    pending: List[ast.AST] = []
    for field, value in ast.iter_fields(statement):
        if field in NOT_OWN:
            continue
        values = value if isinstance(value, list) else [value]
        pending.extend(item for item in values if isinstance(item, ast.AST))
    while pending:
        node = pending.pop(0)
        if isinstance(node, SCOPES) or isinstance(node, ast.JoinedStr):
            continue
        if _is_log_call(node):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))


def _expression_points(node: ast.AST) -> Iterator[Tuple[str, ast.AST, str]]:
    if isinstance(node, ast.Compare):
        flipped = FLIPS[type(node.ops[0])]()
        mutated = ast.Compare(node.left, [flipped, *node.ops[1:]], node.comparators)
        yield "compare", node, _padded(ast.unparse(mutated), node, True)
    if isinstance(node, ast.IfExp):
        text = f"not ({ast.unparse(node.test)})"
        yield "negate", node.test, _padded(text, node.test, True)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool):
            yield "bool", node, _padded(repr(not value), node, True)
        elif isinstance(value, (int, float)):
            yield "constant", node, _padded(repr(value + 1), node, True)
        elif isinstance(value, str):
            yield "constant", node, _padded(repr(f"XX{value}XX"), node, True)


def _statement_points(statement: ast.stmt) -> Iterator[Tuple[str, ast.AST, str]]:
    if isinstance(statement, (ast.If, ast.While)):
        test = statement.test
        yield "negate", test, _padded(f"not ({ast.unparse(test)})", test, True)
    if isinstance(statement, ast.Expr) and not _is_log_call(statement.value):
        call = statement.value
        if isinstance(call, ast.Await):
            call = call.value
        if isinstance(call, ast.Call):
            yield "drop-call", statement, _padded("pass", statement, False)
    if isinstance(statement, ast.Return) and statement.value is not None:
        value = statement.value
        if not (isinstance(value, ast.Constant) and value.value is None):
            yield "return-none", statement, _padded("return None", statement, False)
    for node in _own_nodes(statement):
        yield from _expression_points(node)


def _is_bare_constant(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _statements(
    body: Sequence[ast.stmt], prefix: str, function: str
) -> Iterator[Tuple[str, str, ast.stmt]]:
    for index, statement in enumerate(body):
        address = f"{prefix}{index}"
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _statements(statement.body, "", f"{function}.{statement.name}")
            continue
        if isinstance(statement, ast.ClassDef) or _is_bare_constant(statement):
            continue
        yield function, address, statement
        for field in BLOCKS:
            nested = getattr(statement, field, None)
            if isinstance(nested, list) and nested:
                yield from _statements(nested, f"{address}.{field}", function)
        for number, handler in enumerate(getattr(statement, "handlers", None) or []):
            yield from _statements(
                handler.body, f"{address}.handlers{number}.body", function
            )
        for number, case in enumerate(getattr(statement, "cases", None) or []):
            yield from _statements(case.body, f"{address}.cases{number}.body", function)


def _definitions(tree: ast.Module) -> Iterator[Tuple[str, ast.AST]]:
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield statement.name, statement
        if isinstance(statement, ast.ClassDef):
            for member in statement.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield f"{statement.name}.{member.name}", member


def mutants(source: str, path: str) -> List[Mutant]:
    """Every mutation point of the function bodies of one module, in a fixed order."""
    starts = _line_starts(source.encode("utf-8"))
    found: List[Mutant] = []
    for qualname, definition in _definitions(ast.parse(source)):
        for function, address, statement in _statements(definition.body, "", qualname):
            seen: Dict[str, int] = {}
            for operator, node, text in _statement_points(statement):
                occurrence = seen.get(operator, 0)
                seen[operator] = occurrence + 1
                start, end = _span(node, starts)
                found.append(
                    Mutant(
                        path=path,
                        function=function,
                        statement=address,
                        operator=operator,
                        occurrence=occurrence,
                        line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        start=start,
                        end=end,
                        text=text,
                    )
                )
    return found


def apply(source: str, mutant: Mutant) -> str:
    """The module's source with one mutant spliced in, every other byte kept."""
    raw = source.encode("utf-8")
    mutated = raw[: mutant.start] + mutant.text.encode("utf-8") + raw[mutant.end :]
    return mutated.decode("utf-8")


def valid(source: str, mutant: Mutant) -> bool:
    """Whether the mutated module still parses and means something else."""
    mutated = apply(source, mutant)
    try:
        return ast.dump(ast.parse(mutated)) != ast.dump(ast.parse(source))
    except SyntaxError:
        return False


def rank(seed: int, identifier: str) -> str:
    """The seeded rank of a mutant id, independent of every other id."""
    return hashlib.sha256(f"{seed}|{identifier}".encode("utf-8")).hexdigest()


def sample(root: Path, files: Sequence[str], quota: int, seed: int) -> List[Mutant]:
    """`quota` valid mutants, taken in turn from each operator by lowest seeded rank."""
    sources = {path: (root / path).read_text(encoding="utf-8") for path in files}
    queues: Dict[str, List[Mutant]] = {operator: [] for operator in OPERATORS}
    for path in files:
        for mutant in mutants(sources[path], path):
            queues[mutant.operator].append(mutant)
    for queue in queues.values():
        queue.sort(key=lambda item: rank(seed, item.id), reverse=True)

    chosen: List[Mutant] = []
    while len(chosen) < quota and any(queues.values()):
        for operator in OPERATORS:
            queue = queues[operator]
            while queue and len(chosen) < quota:
                mutant = queue.pop()
                if valid(sources[mutant.path], mutant):
                    chosen.append(mutant)
                    break
    return sorted(chosen, key=lambda item: item.id)


def find(root: Path, identifier: str) -> Optional[Mutant]:
    """The mutant an id names in the current source, or None once it does not apply."""
    relative = identifier.split("::", 1)[0]
    if not (root / relative).is_file():
        return None
    source = (root / relative).read_text(encoding="utf-8")
    for mutant in mutants(source, relative):
        if mutant.id == identifier:
            return mutant if valid(source, mutant) else None
    return None
