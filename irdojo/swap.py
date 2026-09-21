"""方針の入れ替え表 — 入口に置く 3×3（SPEC 7.6.5 / 9.1 差分#1）。

この製品の主張は「**同じ盤面・同じ真相でも、方針が変われば別の対応が
最善になる**」の一点である。トップ画面はそれを 4 ブロックの文章で
2 回言っていた。**主張は書くより見せるほうが短い** —
3つの方針それぞれに向けて立てた対応を、3つの方針すべてで採点し直すと、
対角が各列で最大になる。説明を足さずに同じことが言える。

**数字は手で書かない。** 手書きの数字を置いた日から、盤面を触るたびに
入口が嘘をつく（周4 で凡例のモックが3フェーズのまま腐った実例がある）。
数字は `tools/balance.py --emit-swap` が実際にエンジンを走らせて作り、
ここはそれを読むだけにする。

**算出をサーバに持ち込まない。** 入れ替え表を出すには `ground_truth` を
読むプレイ像が要る（`balance.py` の `_skilled` / `_play`）。真実を読む
コードを配信プロセスに入れるのは、7.7.3「答えを配信経路に載せない」と
逆向きである。加えて実測で 1本目 9.3秒・2本目 4.4秒かかるので、
入口の描画に間に合わない。

**腐りは「古い数字を出す」ではなく「出さない」で閉じる。** 算出に効く
ソース（エンジン一式・`balance.py`・全シナリオ）の指紋を一緒に保存し、
食い違ったら表を出さない。入口から表が消えたら
`tools/balance.py --emit-swap` を流し直せ、という合図である。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "irdojo" / "data" / "policy_swap.json"

# 指紋から外すもの。**既定は「入れる」側である** — 新しく足した
# モジュールは黙って指紋に入る。外すのは、点数に触れないと言い切れる
# 配信・記録・表示の層だけ（ここを外さないと、画面を1行直すたびに
# 入口から表が消える）
EXCLUDED = {
    "irdojo/api.py",
    "irdojo/__main__.py",
    "irdojo/records.py",
    "irdojo/swap.py",
}
EXCLUDED_DIRS = ("irdojo/players/", "irdojo/report/")


class PolicyScores(BaseModel):
    """1つの対応を、全方針で採点し直した行。"""

    label: str          # 「Aの対応」。**どの盤面の何なのかは言わない**
    policy_id: str      # この対応が向けて立てられた方針
    scores: dict[str, int]


class ScenarioSwap(BaseModel):
    scenario_id: str
    policy_ids: list[str]
    rows: list[PolicyScores]


class SwapFile(BaseModel):
    """生成物。読むのはこのプロセスだけだが、形は入口で見る。

    壊れた JSON を黙って部分的に信じると、入口が**半分だけ本当の表**を
    描く。形が合わなければ表ごと出さない。
    """

    fingerprint: str
    scenarios: list[ScenarioSwap]
    # 表を焼いた道具そのものの指紋（v1.56）。
    # **`balance.py` は配布物に入らない** — 真実を読むプレイ像を持っているので、
    # テスターに渡す zip からは外す（7.5.6）。それでも「どの道具が焼いたか」は
    # 記録しておく。手元では突き合わせ、無い場所では名乗るだけにする
    generator: str = ""


# 表を焼く道具。**配布物には入らない**ので、指紋の扱いを分けてある
GENERATOR = ROOT / "tools" / "balance.py"


def shipped_files() -> list[Path]:
    """指紋を取る対象。**点数を1点でも動かしうるもので、かつ配布物に入るもの。**

    エンジン一式と同梱シナリオ。どちらも配布物に入るので、
    テスターの手元でも最後まで突き合わせられる。
    """
    out: list[Path] = []
    for path in sorted(ROOT.joinpath("irdojo").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXCLUDED or rel.startswith(EXCLUDED_DIRS):
            continue
        if "__pycache__" in rel:
            continue
        out.append(path)
    out += sorted(ROOT.joinpath("scenarios").glob("*.yaml"))
    return out


def _digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        h.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def fingerprint() -> str:
    """配布物に入るソースとシナリオの指紋。"""
    return _digest(shipped_files())


def generator_digest() -> str:
    """表を焼いた道具の指紋。**無ければ空。**"""
    return _digest([GENERATOR]) if GENERATOR.is_file() else ""


def load(path: Path | None = None) -> SwapFile | None:
    """生成物を読む。無い・壊れている・古い・確かめられないなら None。

    **None は「表を出さない」であって「0点の表を出す」ではない。**

    突き合わせは2段ある。

    | | 手元（開発） | 配布物（テスター） |
    |---|---|---|
    | エンジン・シナリオ | 突き合わせる | **突き合わせる** |
    | `balance.py` | 突き合わせる | 無いので名乗るだけ |

    **点数を決めるのはエンジンとシナリオで、そこは配った先でも最後まで見る。**
    `balance.py` はプレイ像の作り方を決めるので手元では見るが、
    配布物には入らない（真実を読むコードを配らない / 7.7.3）。
    配った先では中身が変わりようがないので、確かめられないまま出す。

    **確かめられないことと、確かめずに出すことは違う。** ここで分けているのは
    「変わりうる場所」で、変わりうる場所は最後まで突き合わせている。
    """
    path = DATA_FILE if path is None else path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        data = SwapFile.model_validate(raw)
    except ValidationError:
        return None
    try:
        if data.fingerprint != fingerprint():
            return None
        # **道具が手元にあるときだけ突き合わせる。**
        gen = generator_digest()
        if gen and data.generator and data.generator != gen:
            return None
    except OSError:
        # 指紋の材料が読めない。**読めないものは確かめられない** ので出さない。
        # ここを素通りさせていたので、配布物で `/api/policy-swap` が 500 を返した
        return None
    return data
