"""A small schema and a file laid out by the standard, so the reference generator and
the layout check are tested on a shape every YAML type can take. Every field of `Flow`
declares its role the way the reference reads it; `Loose` declares none."""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel

from app.core.commands import Open

IDENTITY = {"json_schema_extra": {"role": "identity"}}
COPY = {"json_schema_extra": {"role": "copy"}}
VISUAL = {"json_schema_extra": {"role": "visual"}}
BEHAVIOR = {"json_schema_extra": {"role": "behavior"}}
DATA = {"json_schema_extra": {"role": "data"}}
STRUCTURE = {"json_schema_extra": {"role": "structure"}}


class Words(BaseModel):
    """One text in both locales."""

    model_config = ConfigDict(extra="forbid")

    en_us: str = Field(alias="en-us", description="The English text.", **COPY)
    pt_br: str = Field(alias="pt-br", description="The Portuguese text.", **COPY)


class IntroStep(BaseModel):
    """The first screen."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="The answer key the step writes.", **DATA)
    title: Words = Field(description="The heading of the screen.", **COPY)
    kind: Literal["intro"] = Field(description="Picks the intro kind.", **IDENTITY)
    lines: int = Field(
        default=1, ge=1, le=5, description="How many lines it draws.", **VISUAL
    )
    texts: Open[Words] = Field(
        default_factory=dict, description="Texts a later step names, by name.", **COPY
    )


class PickStep(BaseModel):
    """A choice among options."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="The answer key the step writes.", **DATA)
    title: Words = Field(description="The heading of the screen.", **COPY)
    kind: Literal["pick"] = Field(description="Picks the pick kind.", **IDENTITY)
    style: Literal["buttons", "select"] = Field(
        default="buttons", description="How the options are drawn.", **VISUAL
    )
    options: list[str] = Field(
        min_length=1, description="The values to pick from.", **BEHAVIOR
    )


Step = Annotated[Union[IntroStep, PickStep], Field(discriminator="kind")]


class Flow(BaseModel):
    """A whole flow file."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(
        default=1, description="The version a session is pinned to.", **IDENTITY
    )
    steps: list[Step] = Field(description="The screens, in order.", **STRUCTURE)
    names: Open[str] = Field(default_factory=dict, description="Labels by key.", **COPY)


class Glossary(RootModel[Open[Words]]):
    """A file that is one open mapping of texts."""


class Loose(BaseModel):
    """A model that leaves a field undescribed."""

    model_config = ConfigDict(extra="forbid")

    said: str = Field(description="Something described.")
    unsaid: str = ""


FLOW = """\
version: 2
steps:
  - kind: intro
    key: form
    title: {ref: welcome}
    lines: 2
    # open: text names -> Words
    texts:
      hello:
        en-us: Hello
        pt-br: Oi
  - kind: pick
    key: color
    title:
      en-us: Color
      pt-br: Cor
    style: select
    options:
      - red
      - blue
# open: answer keys -> the label of each
names:
  color: The color
"""

GLOSSARY = """\
# open: text names -> Words
hello:
  en-us: Hello
  pt-br: Oi
"""
