import pytest

from irdojo.loader import SCENARIO_DIR, load_scenario


def _bundled() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """記録は必ず一時ディレクトリへ。~/.irdojo を汚さない。"""
    monkeypatch.setenv("IRDOJO_HOME", str(tmp_path / "irdojo"))


@pytest.fixture
def scenario():
    """エンジン側の検査で使う固定の1本。**盤面の性質ではなく実装を測る用。**"""
    return load_scenario("ransomware-initial-response-01")


@pytest.fixture(params=_bundled())
def any_scenario(request):
    """同梱シナリオを1本ずつ。

    **書き方の規則は、同梱の全部にかかる。** 2本目を書くまで、
    漏洩と読みやすさの検査は1本目を名指しで読んでいた。
    1本しか無い間は同じことだが、2本目が入った瞬間に
    「規則はあるが誰も見ていない」状態が生まれる。
    シナリオを足したら検査対象も自動で増える形にしておく。
    """
    return load_scenario(request.param)
