from unittest.mock import MagicMock, patch

import main

MAX_MSG_LEN = main.MAX_MSG_LEN


def make_jobs(n, company="Acme"):
    return [
        {
            "id": str(i),
            "title": f"Software Engineer, New Grad #{i}",
            "url": f"https://example.com/{company}/jobs/{i}",
            "location": "Toronto, ON",
        }
        for i in range(n)
    ]


def test_single_small_digest_is_one_message():
    new_by_company = {"Acme": make_jobs(2)}
    messages = main.build_messages(new_by_company)
    assert len(messages) == 1
    assert "Acme" in messages[0]
    assert "linkedin.com" in messages[0]


def test_no_message_exceeds_limit():
    new_by_company = {f"Company{i}": make_jobs(20, f"Company{i}") for i in range(15)}
    messages = main.build_messages(new_by_company)
    assert len(messages) > 1
    for msg in messages:
        assert len(msg) <= MAX_MSG_LEN


def test_chunking_splits_on_company_boundaries():
    new_by_company = {f"Company{i}": make_jobs(30, f"Company{i}") for i in range(10)}
    messages = main.build_messages(new_by_company)
    for msg in messages:
        # every company block that appears must appear in full (header present)
        for company in new_by_company:
            if company in msg:
                assert f"*{company}*" in msg


def test_grouped_by_company_formatting():
    new_by_company = {"Acme": make_jobs(1), "Widgets": make_jobs(1, "Widgets")}
    messages = main.build_messages(new_by_company)
    combined = "\n".join(messages)
    assert "*Acme*" in combined
    assert "*Widgets*" in combined


def test_includes_apply_link_and_linkedin_link():
    new_by_company = {"Acme": make_jobs(1)}
    messages = main.build_messages(new_by_company)
    combined = "\n".join(messages)
    assert "https://example.com/Acme/jobs/0" in combined
    assert "linkedin.com/search/results/people" in combined
    assert "Acme" in main.linkedin_recruiter_link("Acme")


def test_extremely_long_single_block_is_hard_split():
    huge_jobs = make_jobs(500)  # guaranteed to exceed 4096 chars on its own
    new_by_company = {"Acme": huge_jobs}
    messages = main.build_messages(new_by_company)
    assert len(messages) > 1
    for msg in messages:
        assert len(msg) <= MAX_MSG_LEN


def test_oversized_block_splits_on_line_boundaries():
    """A hard-split must never slice a [title](url) Markdown entity in half."""
    huge_jobs = make_jobs(500)
    messages = main.build_messages({"Acme": huge_jobs})
    for msg in messages:
        # every bullet line that appears must be complete: balanced brackets
        for line in msg.split("\n"):
            if line.startswith("•"):
                assert line.count("[") == line.count("]")
                assert line.count("(") == line.count(")")


def test_markdown_metacharacters_are_escaped():
    jobs = [{"id": "1", "title": "Back_end Developer [Toronto] *urgent*", "url": "https://x.io/jobs/1", "location": "Toronto"}]
    block = main.build_company_block("Snap_Commerce [Inc]", jobs)
    assert "Back\\_end Developer \\[Toronto\\] \\*urgent\\*" in block
    assert "*Snap\\_Commerce \\[Inc\\]*" in block


def test_link_urls_with_parentheses_are_escaped():
    jobs = [{"id": "1", "title": "Engineer", "url": "https://x.io/jobs/1(a)", "location": "Toronto"}]
    block = main.build_company_block("Acme", jobs)
    assert "(https://x.io/jobs/1%28a%29)" in block


@patch("ats.request_with_retry")
def test_send_telegram_posts_expected_payload(mock_request):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_request.return_value = mock_response

    main.send_telegram("TOKEN", "CHATID", "hello world")

    assert mock_request.called
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert "TOKEN" in args[1]
    assert kwargs["json"]["chat_id"] == "CHATID"
    assert kwargs["json"]["text"] == "hello world"
