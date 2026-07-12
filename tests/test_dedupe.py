import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def test_same_job_produces_same_key():
    job = {"id": "123", "url": "https://example.com/jobs/123", "title": "ML Engineer"}
    key1 = main.dedupe_key("Acme", job)
    key2 = main.dedupe_key("Acme", dict(job))
    assert key1 == key2


def test_different_id_produces_different_key():
    job_a = {"id": "123", "url": "https://example.com/jobs/123"}
    job_b = {"id": "456", "url": "https://example.com/jobs/123"}
    assert main.dedupe_key("Acme", job_a) != main.dedupe_key("Acme", job_b)


def test_different_company_produces_different_key():
    job = {"id": "123", "url": "https://example.com/jobs/123"}
    assert main.dedupe_key("Acme", job) != main.dedupe_key("Widgets Inc", job)


def test_falls_back_to_url_when_no_id():
    job = {"id": None, "url": "https://example.com/jobs/xyz"}
    key1 = main.dedupe_key("Acme", job)
    key2 = main.dedupe_key("Acme", {"id": None, "url": "https://example.com/jobs/xyz"})
    assert key1 == key2


def test_key_is_stable_string_not_random():
    job = {"id": "123", "url": "https://example.com/jobs/123"}
    keys = {main.dedupe_key("Acme", job) for _ in range(5)}
    assert len(keys) == 1
