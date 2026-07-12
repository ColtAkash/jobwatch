import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

BASE_CONFIG = {
    "role_keywords": ["software engineer", "ml engineer", "data engineer"],
    "exclude_title_keywords": ["senior", "intern", "manager"],
    "locations": ["canada", "toronto", "remote - canada"],
}


def dead_company(i):
    return {"name": f"DeadCo{i}", "ats": "greenhouse", "slug": f"dead-slug-{i}"}


def test_ten_dead_companies_never_raise_and_all_logged_as_errors():
    config = dict(BASE_CONFIG, companies=[dead_company(i) for i in range(10)])

    with patch("ats.fetch_jobs", side_effect=main.ats.AtsError("board not found")):
        new_by_company, all_keys, errors = main.collect_new_postings(config, seen=set())

    assert new_by_company == {}
    assert all_keys == set()
    assert len(errors) == 10


def test_mixed_dead_and_healthy_companies_isolate_failures():
    healthy = {"name": "GoodCo", "ats": "greenhouse", "slug": "goodco"}
    config = dict(BASE_CONFIG, companies=[dead_company(i) for i in range(10)] + [healthy])

    def fake_fetch(entry):
        if entry["name"] == "GoodCo":
            return [{"id": "1", "title": "Software Engineer, New Grad", "location": "Toronto", "url": "https://x/1"}]
        raise main.ats.AtsError("board not found")

    with patch("ats.fetch_jobs", side_effect=fake_fetch):
        new_by_company, all_keys, errors = main.collect_new_postings(config, seen=set())

    assert len(errors) == 10
    assert "GoodCo" in new_by_company
    assert len(all_keys) == 1


def test_first_run_seeds_silently_and_exits_zero(tmp_path, monkeypatch):
    config = dict(
        BASE_CONFIG,
        companies=[{"name": "Acme", "ats": "greenhouse", "slug": "acme"}],
    )
    config_file = tmp_path / "config.json"
    seen_file = tmp_path / "seen.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(main, "CONFIG_FILE", config_file)
    monkeypatch.setattr(main, "SEEN_FILE", seen_file)
    monkeypatch.chdir(tmp_path)

    jobs = [{"id": "1", "title": "Software Engineer, New Grad", "location": "Toronto", "url": "https://x/1"}]
    with patch("ats.fetch_jobs", return_value=jobs), patch.object(main, "send_telegram") as mock_send:
        rc = main.main()

    assert rc == 0
    assert seen_file.exists()
    mock_send.assert_not_called()


def test_second_run_with_one_new_job_sends_exactly_one_message(tmp_path, monkeypatch):
    config = dict(
        BASE_CONFIG,
        companies=[{"name": "Acme", "ats": "greenhouse", "slug": "acme"}],
    )
    config_file = tmp_path / "config.json"
    seen_file = tmp_path / "seen.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    existing_job = {"id": "1", "title": "Software Engineer, New Grad", "location": "Toronto", "url": "https://x/1"}
    existing_key = main.dedupe_key("Acme", existing_job)
    seen_file.write_text(json.dumps([existing_key]), encoding="utf-8")

    monkeypatch.setattr(main, "CONFIG_FILE", config_file)
    monkeypatch.setattr(main, "SEEN_FILE", seen_file)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")

    new_job = {"id": "2", "title": "Data Engineer, New Grad", "location": "Remote - Canada", "url": "https://x/2"}
    with patch("ats.fetch_jobs", return_value=[existing_job, new_job]), patch.object(main, "send_telegram") as mock_send:
        rc = main.main()

    assert rc == 0
    assert mock_send.call_count == 1
    sent_text = mock_send.call_args[0][2]
    assert "*Acme*" in sent_text
    assert "https://x/2" in sent_text
    assert "linkedin.com" in sent_text
    assert "https://x/1" not in sent_text  # previously-seen job must not be re-sent

    saved_keys = set(json.loads(seen_file.read_text(encoding="utf-8")))
    assert main.dedupe_key("Acme", new_job) in saved_keys
