import json
from pathlib import Path

import main

CONFIG = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))


def test_title_accepts_new_grad_ml_engineer():
    assert main.is_relevant_title("ML Engineer, New Grad", CONFIG) is True


def test_title_rejects_senior_data_engineer():
    assert main.is_relevant_title("Senior Data Engineer", CONFIG) is False


def test_title_rejects_product_manager():
    assert main.is_relevant_title("Product Manager", CONFIG) is False


def test_title_rejects_intern():
    assert main.is_relevant_title("Software Engineer Intern", CONFIG) is False


def test_title_rejects_blank():
    assert main.is_relevant_title("", CONFIG) is False
    assert main.is_relevant_title(None, CONFIG) is False


def test_location_accepts_toronto():
    assert main.is_relevant_location("Toronto, ON", CONFIG) is True


def test_location_accepts_remote_canada():
    assert main.is_relevant_location("Remote - Canada", CONFIG) is True


def test_location_rejects_new_york():
    assert main.is_relevant_location("New York, NY", CONFIG) is False


def test_location_keeps_blank():
    assert main.is_relevant_location("", CONFIG) is True
    assert main.is_relevant_location(None, CONFIG) is True
