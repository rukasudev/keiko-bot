"""Behavioral scenarios: the custom links of block links say how many of 25 are used.

The list declares a `counter` (`$count de $max`), read after its title on the
panel and on the review while it holds a link; an empty list keeps reading
"Em branco", the way every empty setting does.
"""
import copy

import pytest

from tests.behavioral.golden.paths import block_links
from tests.behavioral.golden.paths.common import seed_document

pytestmark = pytest.mark.behavioral

LINK_FIELD = block_links.LINK_FIELD["pt-br"]


async def _panel(scenario_factory, deps, document):
    seed_document(deps, block_links.FORM, document)
    return await scenario_factory(locale="pt-br").start_command(block_links.FORM)


async def test_the_panel_counts_the_custom_links_of_their_limit(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps, block_links.ENABLED)

    assert "📌 **Links personalizados permitidos:** 2 de 25" in scenario.rendered_summary
    await scenario.finish()


async def test_an_empty_list_of_custom_links_reads_empty(scenario_factory, deps):
    document = copy.deepcopy(block_links.ENABLED)
    document["custom_links"]["values"] = []
    scenario = await _panel(scenario_factory, deps, document)

    shown = scenario.rendered_summary
    assert "📌 **Links personalizados permitidos:** Em branco" in shown
    assert "0 de 25" not in shown
    await scenario.finish()


async def test_the_review_counts_the_custom_links_added_in_the_setup(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start(block_links.FORM)
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")
    for link in ("bit.ly/golpe", "spam.com"):
        await scenario.click(block_links.CUSTOM_LINKS)
        await scenario.submit_modal({LINK_FIELD: link})
        if link == "bit.ly/golpe":
            await scenario.click(block_links.DOMAIN_OPTION["pt-br"])
    await scenario.click("done")

    scenario.expect_step("confirm")
    assert "📌 **Links personalizados bloqueados:** 2 de 25" in scenario.rendered_summary
    await scenario.finish()
