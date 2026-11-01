import sqlite3
from contextlib import closing

import httpx
import pytest

from emporio_ai.agent.service import ConversationService
from emporio_ai.config import get_settings
from emporio_ai.data.loader import build_database
from emporio_ai.data.policy_index import build_policy_index
from emporio_ai.data.repository import Repository
from emporio_ai.observability import events


@pytest.fixture(scope="session")
def data_dir():
    return get_settings().data_dir


@pytest.fixture(scope="session")
def db_path(data_dir, tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    build_database(data_dir, path)
    return path


@pytest.fixture(scope="session")
def repo(db_path):
    return Repository(db_path)


@pytest.fixture(scope="session")
def policy_index(data_dir):
    return build_policy_index(data_dir / "politicas_da_loja.pdf")


@pytest.fixture(autouse=True)
def isolate_from_the_outside(monkeypatch):
    """Keep tests off the network, so a real key in .env can never spend credit by accident."""

    def refuse(*_, **__):
        raise AssertionError("tests must not use the network")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    yield
    # A handler set up during a test would keep writing to a stream that pytest has closed.
    events.logger.handlers.clear()


@pytest.fixture
def make_service(repo, policy_index):
    def build(llm):
        return ConversationService(llm, repo, policy_index)

    return build


@pytest.fixture
def owner_email(db_path):
    def email_of(customer_id):
        with closing(sqlite3.connect(db_path)) as connection:
            return connection.execute(
                "SELECT email FROM customers WHERE customer_id = ?", (customer_id,)
            ).fetchone()[0]

    return email_of
