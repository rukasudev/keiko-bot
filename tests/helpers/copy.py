"""A copy file out of EXCEPTIONS, written for one test: loaded flat and typed under a
prefix no command owns, and gone once the test ends."""

from collections.abc import Callable, Iterator
from pathlib import Path

import i18n as python_i18n
import pytest

from app.core import i18n

Install = Callable[..., None]

SAMPLE = """\
facts:
  blocked: {type: boolean, description: Whether the check would block the sample.}
  counted: {type: boolean, description: Whether the samples could be counted.}
  done: {type: boolean, description: Whether every sample is set up.}
  page: {type: number, description: The page shown.}
  pages: {type: number, description: How many pages there are.}
  ping: {type: number, description: The latency in milliseconds.}
  shape: {type: enum, values: [round, square], description: The shape of the sample.}
  who: {type: text, description: Who saved the samples.}
command:
  slash:
    name: {en-us: sample, pt-br: amostra}
    description: {en-us: Keiko shows a sample, pt-br: O Keiko mostra uma amostra}
    params:
      member:
        name: {en-us: member, pt-br: membro}
        description: {en-us: Whose sample, pt-br: De quem é a amostra}
    commands:
      check:
        name: {en-us: Check Sample, pt-br: Verificar Amostra}
  setup:
    label: {en-us: Sample, pt-br: Amostra}
    description: {en-us: Shows a sample, pt-br: Mostra uma amostra}
  stats:
    title: {en-us: "📊 Sample statistics", pt-br: "📊 Estatísticas da amostra"}
    delivered:
      one: {en-us: "1 sample in $days days", pt-br: "1 amostra em $days dias"}
      other: {en-us: "$count samples in $days days", pt-br: "$count amostras em $days dias"}
    empty: {en-us: Nothing counted yet, pt-br: Nada contado ainda}
    figures:
      total: {en-us: Total, pt-br: Total}
      recent: {en-us: Recent, pt-br: Recentes}
  panel:
    info:
      title: {en-us: About, pt-br: Sobre}
      description: {en-us: "I count $count samples.", pt-br: "Eu conto $count amostras."}
      overrides:
        - when: {key: sample.facts.counted, is: false}
          description: {en-us: I could not count them., pt-br: Não consegui contar.}
    pages:
      title: {en-us: Samples, pt-br: Amostras}
      description: {en-us: In saved order, pt-br: Na ordem salva}
      empty: {en-us: No sample yet, pt-br: Nenhuma amostra ainda}
    rows:
      channel: {en-us: Channel, pt-br: Canal}
    authors:
      member: {en-us: Written by the member, pt-br: Escrita pelo membro}
ui:
  cards:
    dashboard:
      title: {en-us: "⚙️ Samples", pt-br: "⚙️ Amostras"}
      description: {en-us: Everything at a glance, pt-br: Tudo de uma vez}
      note: {en-us: "Page $page of $pages", pt-br: "Página $page de $pages"}
      footer: null
      overrides:
        - when: {key: sample.facts.done, is: true}
          description: {en-us: "All set!", pt-br: "Tudo pronto!"}
      rows:
        prefix:
          title: {en-us: Prefix, pt-br: Prefixo}
          description: {en-us: "Now `$prefix`", pt-br: "Agora `$prefix`"}
          placeholder: {en-us: Pick one, pt-br: Escolha um}
  texts:
    hint: {en-us: Try the prefix, pt-br: Tente o prefixo}
    states:
      active: {en-us: Active, pt-br: Ativo}
      paused: {en-us: Paused, pt-br: Pausado}
  records:
    saved:
      title: {en-us: "📜 Saved by $who", pt-br: "📜 Salvos por $who"}
      description: {en-us: "Everything $who saved", pt-br: "Tudo que $who salvou"}
      empty: {en-us: "Nothing saved yet!", pt-br: "Nada salvo ainda!"}
      filter:
        label: {en-us: Filter by member, pt-br: Filtrar por membro}
        clear_label: {en-us: Show everyone, pt-br: Mostrar todos}
        title: {en-us: "Whose samples?", pt-br: "De quem?"}
        description: {en-us: Pick a member, pt-br: Escolha um membro}
        empty: {en-us: This member saved nothing., pt-br: Esse membro não salvou nada.}
      lines:
        line: {en-us: "$user on $date", pt-br: "$user em $date"}
  responses:
    saved:
      title: {en-us: "✅ Saved", pt-br: "✅ Salvo"}
      description: {en-us: "I saved $item.", pt-br: "Salvei $item."}
    quiet:
      title: {en-us: "🏓 Pong!", pt-br: "🏓 Pong!"}
      description: {en-us: "$ping ms", pt-br: "$ping ms"}
      footer: null
    signed:
      title: {en-us: "💌 Sent", pt-br: "💌 Enviado"}
      description: {en-us: I sent it., pt-br: Enviei.}
      footer: {en-us: "Use `/sample` again", pt-br: "Use `/amostra` de novo"}
    check:
      title: {en-us: "✅ Allowed", pt-br: "✅ Permitido"}
      description: {en-us: "I checked $item.", pt-br: "Verifiquei $item."}
      empty: {en-us: Nothing to check., pt-br: Nada para verificar.}
      overrides:
        - when: {key: sample.facts.blocked, is: true}
          title: {en-us: "🚫 Blocked", pt-br: "🚫 Bloqueado"}
      fields:
        how:
          title: {en-us: How, pt-br: Como}
          value: {en-us: "Ask $who", pt-br: "Pergunte a $who"}
        files:
          title: {en-us: Files, pt-br: Arquivos}
          empty: {en-us: No files, pt-br: Sem arquivos}
        author:
          title: {en-us: Author, pt-br: Autor}
  errors:
    not_found:
      title: {en-us: Not Found, pt-br: Não Encontrado}
      message: {en-us: "I found no $item.", pt-br: "Não encontrei $item."}
  buttons:
    sync:
      label: {en-us: Sync, pt-br: Sincronizar}
      description: {en-us: Syncs the samples, pt-br: Sincroniza as amostras}
  announcement:
    fields:
      game: {en-us: Game, pt-br: Jogo}
      tags: {en-us: Tags, pt-br: Tags}
    states:
      online: {en-us: Online, pt-br: Online}
    samples:
      title: {en-us: A sample title, pt-br: Um título de amostra}
    defaults:
      title: {en-us: "Hello {user}!", pt-br: "Olá {user}!"}
      description: {en-us: "Welcome to {server}.", pt-br: "Bem-vindo ao {server}."}
"""
"""A `copy.yml` that writes every block of the schema once."""


@pytest.fixture
def checked_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Install]:
    """Install `text` as the schema-checked `copy.yml` of `prefix` (default
    `sample`), beside the real copy files."""
    real = i18n.files()
    installed: list[tuple[Path, str]] = []
    added: list[tuple[str, str]] = []

    def install(text: str, prefix: str = "sample") -> None:
        path = tmp_path / prefix / "copy.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        installed.append((path, prefix))
        monkeypatch.setattr(i18n, "files", lambda: [*real, *installed])
        i18n.typed.cache_clear()
        for locale in i18n.locales():
            for key, value in i18n.collect(locale).items():
                if key.startswith(f"{prefix}."):
                    python_i18n.add_translation(key, value, locale=locale)
                    added.append((locale, key))

    yield install
    for locale, key in added:
        python_i18n.translations.container.get(locale, {}).pop(key, None)
    monkeypatch.undo()
    i18n.typed.cache_clear()
    i18n.typed()
