#!/usr/bin/env python3
"""テストプレイ配布用の zip を作る（SPEC 7.5.6）。

    python tools/make_tester_zip.py            # dist/IR_Dojo_テスト版.zip

**入れるのは遊ぶのに要るものだけ。** SPEC も CLAUDE.md もテストも道具も
入れない — 設計の議論が全部書いてあり、読めば答えに近づける。
`.git` を入れないのも同じ理由である（履歴に全部残っている）。

**`scenarios/` は入れざるを得ない。** エンジンが読むからで、
中には `ground_truth`（真相）が書いてある。ローカルで動く道具である以上、
これは技術では閉じられない。**閉じられないものは、はっきり頼む** —
同梱する手引きの一行目に「開かないでください」と書く。
"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 遊ぶのに要るものだけ。**足すときは「無いと遊べないか」で決める**
INCLUDE_DIRS = ["irdojo", "web", "scenarios"]
INCLUDE_FILES = ["pyproject.toml"]

# 入れないもの。名前を書いて残す — 「なんとなく入れなかった」を後から復元できない
EXCLUDE_NOTE = {
    "SPEC.md": "設計の全部。読めば答えが分かる",
    "CLAUDE.md": "作業規則。同上",
    "README.md": "開発者向け。テスターには別の手引きを入れる",
    "tests/": "テストの中に真相の断片がある",
    "tools/": "balance.py は真相を読むプレイ像を持っている",
    ".claude/": "エージェントの指示",
    ".git/": "履歴に全部残っている",
    ".playtest/": "スクリーンショット",
}


def _strip_readme(path: Path) -> None:
    """`readme = "README.md"` を落とす。

    **README.md は配らない**（開発者向けで、テスターには別の手引きを入れる）。
    落としたまま `pip install -e .` を走らせると、hatchling が
    `Readme file does not exist` で止まり、**テスターの機械でだけ**失敗する。
    こちらでは動いているので気づけない — 実際に展開して入れてみるまで出なかった。
    """
    lines = [
        ln for ln in path.read_text("utf-8").split("\n")
        if not ln.strip().startswith("readme")
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build(out: Path, name: str) -> Path:
    stage = out / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for d in INCLUDE_DIRS:
        shutil.copytree(
            ROOT / d, stage / d,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
    for f in INCLUDE_FILES:
        shutil.copy2(ROOT / f, stage / f)
    _strip_readme(stage / "pyproject.toml")
    shutil.copy2(ROOT / "docs" / "TESTER.md", stage / "はじめに読んでください.md")
    shutil.copy2(ROOT / "start.command", stage / "起動_Mac.command")
    shutil.copy2(ROOT / "start.bat", stage / "起動_Windows.bat")
    (stage / "起動_Mac.command").chmod(0o755)

    zip_path = out / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(out))
    shutil.rmtree(stage)
    return zip_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="テスト配布用 zip を作る")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--name", default="IR_Dojo_テスト版")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    path = build(args.out, args.name)
    size = path.stat().st_size / 1024 / 1024

    print(f"■ 作りました: {path}  （{size:.1f} MB）")
    print("\n  入れたもの")
    for d in INCLUDE_DIRS + INCLUDE_FILES:
        print(f"    {d}")
    print("    はじめに読んでください.md / 起動_Mac.command / 起動_Windows.bat")
    print("\n  入れなかったもの")
    for k, why in EXCLUDE_NOTE.items():
        print(f"    {k:<14} {why}")
    print("\n  **scenarios/ には真相が書いてあります。**")
    print("  技術では閉じられないので、手引きの一行目で開かないよう頼んでいます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
