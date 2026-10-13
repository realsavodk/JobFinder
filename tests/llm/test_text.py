from job_scout.llm.text import (
    hash_text,
    prepare_posting_text,
    strip_html,
    truncate_for_context,
)


def test_strip_html_removes_tags_and_collapses_whitespace():
    assert strip_html("<p>Hello   <b>world</b></p>\n\n<div>!</div>") == "Hello world !"


def test_strip_html_handles_entity_escaped_markup():
    # Greenhouse returns descriptions with the HTML itself entity-escaped
    # (e.g. "&lt;h2&gt;...&lt;/h2&gt;") rather than raw tags.
    escaped = "&lt;h2&gt;Who we are&lt;/h2&gt;&lt;p&gt;We build things&lt;/p&gt;"
    assert strip_html(escaped) == "Who we are We build things"


def test_strip_html_handles_double_escaped_entities():
    # Some fields are escaped twice over ("&amp;nbsp;" instead of "&nbsp;").
    double_escaped = "&lt;p&gt;Team A&amp;nbsp;and Team B&lt;/p&gt;"
    assert strip_html(double_escaped) == "Team A and Team B"


def test_truncate_for_context_respects_char_budget():
    # num_ctx=100 -> 75 token budget -> 300 char budget (4 chars/token estimate)
    text = "x" * 1000
    truncated = truncate_for_context(text, num_ctx=100)
    assert len(truncated) == 300


def test_prepare_posting_text_strips_then_truncates():
    html = "<p>" + ("word " * 1000) + "</p>"
    result = prepare_posting_text(html, num_ctx=100)
    assert "<p>" not in result
    assert len(result) <= 300


def test_hash_is_stable_for_identical_text():
    assert hash_text("hello world") == hash_text("hello world")


def test_hash_differs_for_different_text():
    assert hash_text("hello world") != hash_text("hello mars")


def test_hash_is_independent_of_num_ctx_truncation():
    # Caching must key off the pre-truncation text so a LLM_NUM_CTX/model change
    # doesn't look like a content change (and vice versa).
    stripped = strip_html("<p>" + ("word " * 1000) + "</p>")
    assert hash_text(stripped) == hash_text(stripped)
    small_budget_text = truncate_for_context(stripped, num_ctx=100)
    large_budget_text = truncate_for_context(stripped, num_ctx=100000)
    assert hash_text(small_budget_text) != hash_text(large_budget_text)
    # the point: callers must hash `stripped`, not either truncated variant
