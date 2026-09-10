import pytest

from irdojo.loader import load_scenario


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """記録は必ず一時ディレクトリへ。~/.irdojo を汚さない。"""
    monkeypatch.setenv("IRDOJO_HOME", str(tmp_path / "irdojo"))


@pytest.fixture
def scenario():
    return load_scenario("ransomware-initial-response-01")
