"""
Assertions customizadas para testes.

Uso:
    from tests.helpers.assertions import assert_embed_contains, assert_error_sent
"""

import discord
from typing import Optional, Any, Union


def assert_embed_contains(embed: discord.Embed, text: str, field: str = "any"):
    """
    Verifica que um embed contem o texto especificado.

    Args:
        embed: O embed a verificar
        text: Texto que deve estar presente
        field: "title", "description", "footer", "fields", ou "any"
    """
    assert embed is not None, "Embed e None"

    found = False
    locations_checked = []

    if field in ("title", "any") and embed.title:
        locations_checked.append(f"title: {embed.title}")
        if text.lower() in embed.title.lower():
            found = True

    if field in ("description", "any") and embed.description:
        locations_checked.append(f"description: {embed.description[:100]}...")
        if text.lower() in embed.description.lower():
            found = True

    if field in ("footer", "any") and embed.footer and embed.footer.text:
        locations_checked.append(f"footer: {embed.footer.text}")
        if text.lower() in embed.footer.text.lower():
            found = True

    if field in ("fields", "any") and embed.fields:
        for f in embed.fields:
            field_text = f"{f.name}: {f.value}"
            locations_checked.append(f"field: {field_text[:50]}...")
            if text.lower() in f.name.lower() or text.lower() in f.value.lower():
                found = True

    # Verifica author tambem
    if field == "any" and embed.author and embed.author.name:
        locations_checked.append(f"author: {embed.author.name}")
        if text.lower() in embed.author.name.lower():
            found = True

    assert found, f"Texto '{text}' nao encontrado no embed. Verificado: {locations_checked}"


def assert_embed_title(embed: discord.Embed, expected: str, exact: bool = False):
    """Verifica titulo do embed."""
    assert embed is not None, "Embed e None"
    assert embed.title is not None, "Embed nao tem titulo"

    if exact:
        assert embed.title == expected, f"Titulo: '{embed.title}', esperado: '{expected}'"
    else:
        assert expected.lower() in embed.title.lower(), \
            f"Titulo '{embed.title}' nao contem '{expected}'"


def assert_embed_field(embed: discord.Embed, name: str, value: str = None):
    """Verifica que um campo especifico existe no embed."""
    assert embed is not None, "Embed e None"
    assert embed.fields, "Embed nao tem campos"

    field = next((f for f in embed.fields if name.lower() in f.name.lower()), None)
    assert field is not None, f"Campo '{name}' nao encontrado. Campos: {[f.name for f in embed.fields]}"

    if value:
        assert value.lower() in field.value.lower(), \
            f"Campo '{name}' nao contem '{value}'. Valor: {field.value}"


def assert_embed_color(embed: discord.Embed, color: Union[discord.Color, int]):
    """Verifica a cor do embed."""
    assert embed is not None, "Embed e None"

    expected_value = color.value if isinstance(color, discord.Color) else color
    actual_value = embed.color.value if embed.color else None

    assert actual_value == expected_value, \
        f"Cor do embed: {actual_value}, esperado: {expected_value}"


def assert_message_deleted(message):
    """Verifica que uma mensagem foi deletada."""
    message._delete.assert_called_once()


def assert_message_not_deleted(message):
    """Verifica que uma mensagem NAO foi deletada."""
    message._delete.assert_not_called()


def assert_message_edited(message):
    """Verifica que uma mensagem foi editada."""
    message._edit.assert_called()


def assert_ephemeral_sent(interaction, contains: str = None):
    """Verifica que uma mensagem efemera foi enviada."""
    for resp in interaction._responses:
        if resp.get("type") == "send_message" and resp.get("ephemeral"):
            if contains:
                embed = resp.get("embed")
                content = resp.get("content") or ""
                if embed:
                    full_text = f"{embed.title or ''} {embed.description or ''} {content}"
                else:
                    full_text = content
                assert contains.lower() in full_text.lower(), \
                    f"Mensagem efemera nao contem '{contains}'"
            return

    raise AssertionError("Nenhuma mensagem efemera foi enviada")


def assert_defer_called(interaction, ephemeral: bool = None):
    """Verifica que defer foi chamado."""
    for resp in interaction._responses:
        if resp.get("type") == "defer":
            if ephemeral is not None:
                assert resp.get("ephemeral") == ephemeral
            return

    raise AssertionError("defer nao foi chamado")


def assert_modal_sent(interaction):
    """Verifica que um modal foi enviado."""
    for resp in interaction._responses:
        if resp.get("type") == "send_modal":
            return resp.get("modal")

    raise AssertionError("Nenhum modal foi enviado")
