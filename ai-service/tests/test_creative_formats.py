import pytest

from app.pipelines.creative_formats import (
    CREATIVE_FORMATS,
    choose_creative_format,
    creative_format_instruction,
)


def test_formats_rotate_and_never_repeat_previous_plan():
    seen = {choose_creative_format("sales", previous="pov_reaction").key for _ in range(400)}
    assert "pov_reaction" not in seen and len(seen) >= 6


def test_general_requests_skip_product_only_formats():
    product_only = {item.key for item in CREATIVE_FORMATS if item.sales_only}
    seen = {choose_creative_format("general").key for _ in range(400)}
    assert seen and not seen & product_only


@pytest.mark.parametrize("item", CREATIVE_FORMATS, ids=lambda item: item.key)
def test_explicit_format_is_honored_and_keeps_truth_rules(item):
    assert choose_creative_format("general", requested=item.key, previous=item.key) is item
    instruction = creative_format_instruction(item)
    assert item.direction in instruction and "never justifies invented facts" in instruction
    assert not any(character.isdigit() for character in instruction)
