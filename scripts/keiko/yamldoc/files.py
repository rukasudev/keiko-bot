"""A YAML file read against its schema: where each field is written, which kind each
union item is, and where the file breaks the layout standard."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import yaml
from scripts.keiko.yamldoc.schema import Model, Schema, Shape

SNIPPET_LINES = 8
OPEN_COMMENT = "# open:"

Trail = tuple[str, ...]


@dataclass(frozen=True)
class Use:
    """A field written in a file: the model, the key, where, the lines it takes, and
    its trail, the schema path the reference names it by (`steps[]/kind=card/key`)."""

    model: str
    field: str
    path: str
    line: int
    snippet: str
    trail: str
    end: int


@dataclass(frozen=True)
class Tag:
    """A union item of a file, the kind its key names and the lines it takes."""

    kinds: str
    value: str
    path: str
    line: int
    snippet: str


@dataclass(frozen=True)
class Problem:
    """A place a file breaks the layout standard, and how to fix it."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Note:
    """The `# open:` comment a file writes above an open block of a model."""

    model: str
    field: str
    comment: str


@dataclass
class Reading:
    """Everything a set of files says about one schema."""

    uses: list[Use] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def extend(self, other: Reading) -> None:
        """Add what another reading found."""
        self.uses += other.uses
        self.tags += other.tags
        self.problems += other.problems
        self.notes += other.notes


def read_file(schema: Schema, text: str, path: str) -> Reading:
    """What the file `path`, holding `text`, writes against `schema`."""
    walk = _Walk(schema, text.splitlines(), path)
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    if node is not None:
        if schema.root.opens() and isinstance(node, yaml.MappingNode) and node.value:
            walk.commented(node.value[0][0], "the first key of this open file")
        walk.value(node, schema.root, ())
    return walk.reading


class _Walk:
    def __init__(self, schema: Schema, lines: Sequence[str], path: str) -> None:
        self.schema = schema
        self.lines = lines
        self.path = path
        self.reading = Reading()

    def value(self, node: yaml.Node, shape: Shape, trail: Trail) -> None:
        if _is_ref(node):
            return
        if shape.kind == "model" and isinstance(node, yaml.MappingNode):
            self.mapping(node, self.schema.models[shape.name], trail)
        elif shape.kind == "kinds" and isinstance(node, yaml.MappingNode):
            self.kind(node, shape.name, trail)
        elif shape.kind == "one_of":
            chosen = self.choose(node, shape.children)
            if chosen is not None:
                self.value(node, chosen, self.shaped(shape, chosen, trail))
        elif shape.kind == "list" and isinstance(node, yaml.SequenceNode):
            for item in node.value:
                self.value(item, shape.children[0], trail)
        elif shape.kind == "open" and isinstance(node, yaml.MappingNode):
            for _key, child in node.value:
                self.value(child, shape.children[0], trail)

    def shaped(self, shape: Shape, chosen: Shape, trail: Trail) -> Trail:
        target = self.schema.target(shape)
        if target is None or target.kind != "shapes" or chosen.kind != "model":
            return trail
        return (*trail, f"@{chosen.name}")

    def mapping(
        self, node: yaml.MappingNode, model: Model, trail: Trail, tag: str = ""
    ) -> None:
        known = [str(key.value) for key, _value in node.value if model.field(key.value)]
        expected = sorted(known, key=lambda name: model.position(name, tag))
        if known != expected:
            first = f"`{tag}` first, then " if tag else ""
            rule = (
                "every key in schema order"
                if model.in_order
                else "the closed keys in schema order, then the open ones"
            )
            self.problem(
                node,
                f"{model.name} writes {', '.join(known)}; the standard order is "
                f"{', '.join(expected)} ({first}{rule})",
            )
        for key, value in node.value:
            found = model.field(str(key.value))
            if found is None:
                if model.closed:
                    self.problem(key, f"`{key.value}` is no key of {model.name}")
                continue
            here = (*trail, found.segment)
            self.reading.uses.append(
                Use(
                    model.name,
                    found.name,
                    self.path,
                    _line(key),
                    self.snippet(key, value),
                    "/".join(here),
                    max(_line(key), _last_line(value) + 1),
                )
            )
            if found.shape.opens() and not _is_ref(value):
                comment = self.commented(key, f"the open block `{found.name}`")
                if comment:
                    self.reading.notes.append(Note(model.name, found.name, comment))
            self.value(value, found.shape, here)

    def kind(self, node: yaml.MappingNode, name: str, trail: Trail) -> None:
        kinds = self.schema.kinds[name]
        tag = next((value for key, value in node.value if key.value == kinds.key), None)
        values = ", ".join(kinds.values())
        if not isinstance(tag, yaml.ScalarNode):
            self.problem(node, f"no `{kinds.key}`: a {name} names one of {values}")
            return
        member = kinds.member(str(tag.value))
        if member is None:
            self.problem(tag, f"`{kinds.key}: {tag.value}` is none of {values}")
            return
        self.reading.tags.append(
            Tag(name, str(tag.value), self.path, _line(tag), self.snippet(node, node))
        )
        here = (*trail, f"{kinds.key}={member.tag}")
        self.mapping(node, self.schema.models[member.model], here, kinds.key)

    def choose(self, node: yaml.Node, options: Sequence[Shape]) -> Shape | None:
        if isinstance(node, yaml.SequenceNode):
            return next((item for item in options if item.kind == "list"), None)
        if not isinstance(node, yaml.MappingNode) or _is_ref(node):
            return None
        keys = {str(key.value) for key, _value in node.value}
        models = [item for item in options if item.kind == "model"]
        for item in models:
            fields = {field.name for field in self.schema.models[item.name].fields}
            if keys <= fields:
                return item
        for item in options:
            if item.kind == "kinds" and self.schema.kinds[item.name].key in keys:
                return item
        rest = [item for item in options if item.kind in ("open", "kinds")]
        if rest:
            return rest[0]
        if len(models) == 1:
            return models[0]
        names = ", ".join(item.name for item in models)
        self.problem(node, f"the keys {', '.join(sorted(keys))} fit none of {names}")
        return None

    def commented(self, key: yaml.Node, block: str) -> str:
        above = key.start_mark.line - 1
        comment = self.lines[above].strip() if above >= 0 else ""
        if comment.startswith(OPEN_COMMENT):
            return comment
        self.problem(
            key,
            f"{block} needs one `{OPEN_COMMENT} <what the keys are> -> <what each "
            "child is>` comment line right above it",
        )
        return ""

    def problem(self, node: yaml.Node, message: str) -> None:
        self.reading.problems.append(Problem(self.path, _line(node), message))

    def snippet(self, key: yaml.Node, value: yaml.Node) -> str:
        first = key.start_mark.line
        last = max(first, _last_line(value))
        column = key.start_mark.column
        taken = [
            _dedented(line, column, index == 0)
            for index, line in enumerate(self.lines[first : last + 1])
        ]
        while taken and not taken[-1].strip():
            taken.pop()
        if len(taken) > SNIPPET_LINES:
            hidden = len(taken) - SNIPPET_LINES + 1
            taken = [*taken[: SNIPPET_LINES - 1], f"# ... {hidden} more lines"]
        return "\n".join(taken)


def _is_ref(node: yaml.Node) -> bool:
    return (
        isinstance(node, yaml.MappingNode)
        and len(node.value) == 1
        and node.value[0][0].value == "ref"
    )


def _line(node: yaml.Node) -> int:
    return int(node.start_mark.line) + 1


def _last_line(node: yaml.Node) -> int:
    if isinstance(node, yaml.ScalarNode):
        end = node.end_mark
        return int(end.line) if end.column > 0 else max(int(end.line) - 1, 0)
    children: list[yaml.Node] = []
    if isinstance(node, yaml.MappingNode):
        children = [part for pair in node.value for part in pair]
    if isinstance(node, yaml.SequenceNode):
        children = list(node.value)
    if not children:
        return int(node.end_mark.line)
    return max(_last_line(child) for child in children)


def _dedented(line: str, column: int, first: bool) -> str:
    if first or not line[:column].strip():
        return line[column:]
    return line.lstrip()
