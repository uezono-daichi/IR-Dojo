"""`python -m irdojo` でローカルサーバを起動する（SPEC 7.6.1）。"""

from __future__ import annotations

import argparse
import webbrowser

# バインドは 127.0.0.1 固定。0.0.0.0 にするオプションは提供しない（SPEC 7.7.4）。
HOST = "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="irdojo", description="IR Dojo をローカルで起動する")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--cli", action="store_true", help="開発用 CLI を使う")
    args, rest = parser.parse_known_args(argv)

    if args.cli:
        from .players.cli import main as cli_main

        return cli_main(rest)

    import uvicorn

    from .api import app

    url = f"http://{HOST}:{args.port}"
    print(f"IR Dojo — {url}")
    print("終了するには Ctrl+C")
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
