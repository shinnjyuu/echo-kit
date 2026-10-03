import pytest


@pytest.fixture(autouse=True)
def isolated_operation_journal(tmp_path_factory, monkeypatch):
    monkeypatch.setenv('ECHO_KIT_DATA_HOME', str(tmp_path_factory.mktemp('user-data')))
    monkeypatch.setenv('ECHO_KIT_OFFLINE', '1')
