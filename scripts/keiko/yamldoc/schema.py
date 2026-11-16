"""The schema of one YAML type as the reference shows it, read from the JSON schema of
its pydantic root model."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import BaseModel

Json = Mapping[str, Any]
ShapeKind = Literal[
    "model", "kinds", "one_of", "list", "open", "enum", "const", "scalar", "any"
]
TargetKind = Literal["model", "kinds", "shapes"]

ROLES: tuple[str, ...] = (
    "identity",
    "copy",
    "visual",
    "behavior",
    "conditions",
    "data",
    "structure",
)
RULES: tuple[str, ...] = (
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "minProperties",
    "maxProperties",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "pattern",
    "uniqueItems",
)
WORD = re.compile(r"[A-Z][a-z0-9]*|[a-z0-9]+")
LOCALE = re.compile(r"[a-z]{2}-[a-z]{2}")


@dataclass(frozen=True)
class Shape:
    """What a value may be: a model, one of a union's kinds, one of several shapes, a
    list, an open mapping, an enum, a constant, a plain scalar or anything."""

    kind: ShapeKind
    name: str = ""
    values: tuple[Any, ...] = ()
    children: tuple[Shape, ...] = ()
    nullable: bool = False

    def reaches(self) -> Iterator[Shape]:
        """The model and kinds shapes this shape names, itself included, in order."""
        if self.kind in ("model", "kinds"):
            yield self
        for child in self.children:
            yield from child.reaches()

    def opens(self) -> bool:
        """True when the value is a mapping whose keys the author chooses."""
        return self.kind == "open"


@dataclass(frozen=True)
class Rule:
    """One JSON schema constraint on a value, or on each item of a list."""

    keyword: str
    value: Any
    each: bool


@dataclass(frozen=True)
class Field:
    """One key of a model: its YAML name, its shape and what the model says of it."""

    name: str
    shape: Shape
    required: bool
    description: str
    default: str | None
    examples: tuple[str, ...]
    rules: tuple[Rule, ...]
    role: str

    @property
    def segment(self) -> str:
        """The key as a path names it: `steps[]` for a list, `texts{}` for an open
        mapping, the bare name otherwise."""
        suffix = {"list": "[]", "open": "{}"}.get(self.shape.kind, "")
        return self.name + suffix


@dataclass(frozen=True)
class Model:
    """One mapping of the schema with fixed keys, the keys in schema order."""

    name: str
    description: str
    fields: tuple[Field, ...]
    closed: bool
    in_order: bool = False

    def field(self, name: str) -> Field | None:
        """The field the YAML key `name` writes, None when the model has none."""
        return next((field for field in self.fields if field.name == name), None)

    def position(self, name: str, first: str = "") -> tuple[int, int]:
        """Where the key `name` belongs: the key `first` names (a kind's tag) before
        all, then the closed keys in schema order, then the open ones; a model
        `in_order` keeps every key in schema order."""
        if name == first:
            return (-1, 0)
        for index, field in enumerate(self.fields):
            if field.name == name:
                return (0 if self.in_order else int(field.shape.opens()), index)
        return (1, len(self.fields))

    def ordered(self, first: str = "") -> tuple[Field, ...]:
        """The fields in the order a file writes them."""
        return tuple(
            sorted(self.fields, key=lambda field: self.position(field.name, first))
        )

    def bilingual(self) -> bool:
        """True when every key is a language code: the model is one text in each
        language, a leaf the reference does not open."""
        return bool(self.fields) and all(
            LOCALE.fullmatch(field.name) for field in self.fields
        )


@dataclass(frozen=True)
class Member:
    """One kind of a union: the values its key takes and the model it is."""

    values: tuple[str, ...]
    model: str

    @property
    def tag(self) -> str:
        """The value a path names the kind by: its first value, else its model."""
        return self.values[0] if self.values else self.model


@dataclass(frozen=True)
class Kinds:
    """A union whose members one key tells apart, the way Kubernetes tells kinds."""

    name: str
    key: str
    members: tuple[Member, ...]
    shared: tuple[str, ...]

    def member(self, value: str) -> Member | None:
        """The member the key's `value` names, None for a value no member takes."""
        return next((item for item in self.members if value in item.values), None)

    def values(self) -> tuple[str, ...]:
        """Every value the key takes, in member order."""
        return tuple(value for item in self.members for value in item.values)


@dataclass(frozen=True)
class Target:
    """What a value opens into: one model, a union its key tells apart, or several
    models (`shapes`) the keys a file writes tell apart."""

    kind: TargetKind
    names: tuple[str, ...]

    @property
    def name(self) -> str:
        """The model or union it names; the shapes joined by ` | `."""
        return " | ".join(self.names)


@dataclass(frozen=True)
class Schema:
    """Every model and union a root model reaches, and the order they are reached in."""

    root: Shape
    models: Mapping[str, Model]
    kinds: Mapping[str, Kinds]
    reached: tuple[str, ...]
    kinds_reached: tuple[str, ...]

    def member_of(self, model: str) -> Kinds | None:
        """The union `model` is a kind of, None when it is no kind."""
        return next(
            (
                kinds
                for kinds in self.kinds.values()
                if any(item.model == model for item in kinds.members)
            ),
            None,
        )

    def undescribed(self) -> dict[str, tuple[str, ...]]:
        """The fields of each model that carry no description, models without any
        left out."""
        found = {
            model.name: tuple(
                field.name for field in model.fields if not field.description
            )
            for model in self.models.values()
        }
        return {name: fields for name, fields in sorted(found.items()) if fields}

    def unroled(self) -> dict[str, tuple[str, ...]]:
        """The fields of each model that declare no role, models without any left
        out."""
        found = {
            model.name: tuple(field.name for field in model.fields if not field.role)
            for model in self.models.values()
        }
        return {name: fields for name, fields in sorted(found.items()) if fields}

    def target(self, shape: Shape) -> Target | None:
        """What a value of `shape` (or each item or child of it) opens into; None for
        a scalar, an enum or a bilingual text."""
        inner = shape
        if shape.kind in ("list", "open") and shape.children:
            inner = shape.children[0]
        if inner.kind == "kinds":
            return Target("kinds", (inner.name,))
        if inner.kind == "model":
            if self.models[inner.name].bilingual():
                return None
            return Target("model", (inner.name,))
        if inner.kind != "one_of":
            return None
        options = [child for child in inner.children if self._opens(child)]
        if len(options) == 1:
            return self.target(options[0])
        if len(options) > 1 and all(child.kind == "model" for child in options):
            return Target("shapes", tuple(child.name for child in options))
        return None

    def _opens(self, shape: Shape) -> bool:
        if shape.kind == "kinds":
            return True
        return shape.kind == "model" and not self.models[shape.name].bilingual()


def read_schema(model: type[BaseModel]) -> Schema:
    """The schema of the YAML whose root is `model`."""
    document = model.model_json_schema()
    definitions = dict(document.get("$defs", {}))
    top = {key: value for key, value in document.items() if key != "$defs"}
    builder = _Builder(definitions)
    root = builder.shape(top)
    reached, kinds_reached = _reached(root, builder)
    return Schema(
        root=root,
        models={name: builder.models[name] for name in reached},
        kinds={name: builder.finished(name) for name in kinds_reached},
        reached=reached,
        kinds_reached=kinds_reached,
    )


class _Builder:
    def __init__(self, definitions: Mapping[str, Json]) -> None:
        self.definitions = definitions
        self.models: dict[str, Model] = {}
        self.kinds: dict[str, Kinds] = {}
        self.raw: dict[tuple[str, str], str] = {}
        self._building: set[str] = set()
        self._union_names: dict[tuple[str, tuple[str, ...]], str] = {}

    def shape(self, node: Json) -> Shape:
        if "$ref" in node:
            return self._ref(str(node["$ref"]))
        if "discriminator" in node and "oneOf" in node:
            return self._union(node)
        if "allOf" in node and len(node["allOf"]) == 1:
            return self.shape(node["allOf"][0])
        if "anyOf" in node or "oneOf" in node:
            return self._options(node.get("anyOf") or node.get("oneOf") or [])
        if "const" in node:
            return Shape("const", values=(node["const"],))
        if "enum" in node:
            return Shape("enum", values=tuple(node["enum"]))
        return self._typed(node)

    def finished(self, name: str) -> Kinds:
        kinds = self.kinds[name]
        return replace(kinds, shared=self._shared(kinds))

    def _typed(self, node: Json) -> Shape:
        kind = node.get("type")
        if isinstance(kind, list):
            return self._options([{"type": item} for item in kind])
        if kind == "array":
            items = node.get("items")
            child = self.shape(items) if isinstance(items, Mapping) else Shape("any")
            return Shape("list", children=(child,))
        if kind == "object":
            if "properties" in node or node.get("additionalProperties") is False:
                return self._model(str(node.get("title", "Object")), node)
            extra = node.get("additionalProperties", True)
            child = self.shape(extra) if isinstance(extra, Mapping) else Shape("any")
            return Shape("open", children=(child,))
        if isinstance(kind, str):
            return Shape("scalar", name=kind)
        return Shape("any")

    def _options(self, branches: Sequence[Json]) -> Shape:
        nullable = any(branch.get("type") == "null" for branch in branches)
        rest = [
            self.shape(branch) for branch in branches if branch.get("type") != "null"
        ]
        if len(rest) == 1:
            return replace(rest[0], nullable=nullable)
        if not rest:
            return Shape("scalar", name="null")
        return Shape("one_of", children=tuple(rest), nullable=nullable)

    def _ref(self, ref: str) -> Shape:
        name = ref.rsplit("/", 1)[-1]
        definition = self.definitions[name]
        if definition.get("type") == "object" and (
            "properties" in definition
            or definition.get("additionalProperties") is False
        ):
            return self._model(name, definition)
        return self.shape(definition)

    def _model(self, name: str, node: Json) -> Shape:
        if name in self.models or name in self._building:
            return Shape("model", name=name)
        self._building.add(name)
        required = set(node.get("required", ()))
        fields = []
        for key, prop in dict(node.get("properties", {})).items():
            self.raw[(name, key)] = json.dumps([prop, key in required], sort_keys=True)
            fields.append(self._field(name, key, prop, key in required))
        self.models[name] = Model(
            name=name,
            description=str(node.get("description", "")),
            fields=tuple(fields),
            closed=node.get("additionalProperties") is False,
            in_order=node.get("in_order") is True,
        )
        self._building.discard(name)
        return Shape("model", name=name)

    def _field(self, owner: str, key: str, prop: Json, required: bool) -> Field:
        examples = prop.get("examples", ())
        role = str(prop.get("role", ""))
        if role and role not in ROLES:
            raise ValueError(
                f"{owner}.{key}: the role {role!r} is none of {', '.join(ROLES)}"
            )
        return Field(
            name=key,
            shape=self.shape(prop),
            required=required,
            description=str(prop.get("description", "")),
            default=_dumped(prop["default"]) if "default" in prop else None,
            examples=tuple(
                _dumped(item)
                for item in (examples if isinstance(examples, list) else ())
            ),
            rules=tuple(_rules(prop)),
            role=role,
        )

    def _union(self, node: Json) -> Shape:
        discriminator = node["discriminator"]
        key = str(discriminator["propertyName"])
        mapping = dict(discriminator.get("mapping", {}))
        members: list[Member] = []
        for branch in node["oneOf"]:
            ref = str(branch["$ref"])
            name = ref.rsplit("/", 1)[-1]
            self._ref(ref)
            values = tuple(value for value, target in mapping.items() if target == ref)
            members.append(Member(values or _tag_values(self.models[name], key), name))
        names = tuple(member.model for member in members)
        union = self._union_names.get((key, names))
        if union is None:
            union = self._union_name(names, key)
            self._union_names[(key, names)] = union
            self.kinds[union] = Kinds(union, key, tuple(members), ())
        return Shape("kinds", name=union)

    def _union_name(self, names: Sequence[str], key: str) -> str:
        words = [WORD.findall(name) for name in names]
        common: list[str] = []
        for parts in zip(*(reversed(item) for item in words)):
            if len(set(parts)) != 1:
                break
            common.insert(0, parts[0])
        name = "".join(common) if common else key.capitalize()
        taken = set(self.kinds) | set(self.definitions)
        candidate, count = name, 1
        while candidate in taken:
            count += 1
            candidate = f"{name} ({count})"
        return candidate

    def _shared(self, kinds: Kinds) -> tuple[str, ...]:
        first = self.models[kinds.members[0].model]
        return tuple(
            field.name
            for field in first.fields
            if field.name != kinds.key
            and len({self.raw.get((item.model, field.name)) for item in kinds.members})
            == 1
        )


def _reached(root: Shape, builder: _Builder) -> tuple[tuple[str, ...], tuple[str, ...]]:
    models: list[str] = []
    kinds: list[str] = []
    queue = list(root.reaches())
    while queue:
        shape = queue.pop(0)
        if shape.kind == "kinds":
            if shape.name in kinds:
                continue
            kinds.append(shape.name)
            queue += [
                Shape("model", name=item.model)
                for item in builder.kinds[shape.name].members
            ]
            continue
        if shape.name in models:
            continue
        models.append(shape.name)
        for field in builder.models[shape.name].fields:
            queue += list(field.shape.reaches())
    return tuple(models), tuple(kinds)


def _tag_values(model: Model, key: str) -> tuple[str, ...]:
    field = model.field(key)
    return tuple(str(value) for value in field.shape.values) if field else ()


def _rules(prop: Json, each: bool = False) -> Iterator[Rule]:
    for keyword in RULES:
        if keyword in prop and prop[keyword] is not False:
            yield Rule(keyword, prop[keyword], each)
    for branch in prop.get("anyOf", ()):
        if branch.get("type") != "null":
            yield from _rules(branch, each)
    items = prop.get("items")
    if isinstance(items, Mapping) and not each:
        yield from _rules(items, True)


def _dumped(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
