"""命令行：导入表格、导入演示资料、试一次检索。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.knowledge.import_sources import CorpusImportError
from app.runtime import Runtime, WorkbookImportError


def main() -> None:
    parser = argparse.ArgumentParser(description="茅盾数字人资料工具")
    commands = parser.add_subparsers(dest="command", required=True)
    import_cmd = commands.add_parser("import", help="导入一个 .xlsx 文件")
    import_cmd.add_argument("path")
    commands.add_parser("demo", help="导入标明为演示、并非馆藏的测试资料")
    corpus_cmd = commands.add_parser("import-corpus", help="导入基础知识、章节表、主题表和馆藏表")
    corpus_cmd.add_argument("directory", help="放着四份原始资料的目录，文件名需包含对应关键词")
    search_cmd = commands.add_parser("search", help="用本机检索试一个问题")
    search_cmd.add_argument("query")
    args = parser.parse_args()

    runtime = Runtime(load_settings())
    try:
        if args.command == "demo":
            print(json.dumps(runtime.import_demo(), ensure_ascii=False, indent=2))
            return
        if args.command == "import-corpus":
            try:
                report = runtime.import_corpus(Path(args.directory))
            except CorpusImportError as exc:
                raise SystemExit(str(exc)) from exc
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        if args.command == "import":
            try:
                report = runtime.import_path(Path(args.path))
            except WorkbookImportError as exc:
                raise SystemExit(str(exc)) from exc
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        print(json.dumps(runtime.search(args.query), ensure_ascii=False, indent=2))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
