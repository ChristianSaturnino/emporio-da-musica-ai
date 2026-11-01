import pytest

from emporio_ai.config import get_settings
from emporio_ai.data.loader import build_database
from emporio_ai.data.policy_index import build_policy_index
from emporio_ai.data.repository import Repository


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
