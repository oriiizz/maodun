"""馆方常用操作。请用项目里的 Python 运行：.venv/bin/python scripts/使用.py 检查"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "检查"
    if command == "检查":
        raise SystemExit(check())
    if command == "启动":
        python = ROOT / ".venv" / "bin" / "python"
        if not python.is_file():
            raise SystemExit("还没有安装依赖。请先运行：.venv/bin/python scripts/使用.py 检查")
        os.execv(str(python), [str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"])
    from app.cli import main as cli_main

    mapping = {"生成": "build", "更新": "update", "增加": "add", "删除": "remove", "导出": "export-pack", "加载": "load-pack", "诊断": "trace"}
    if command not in mapping:
        raise SystemExit("可用操作：检查、生成、加载、增加、更新、删除、导出、诊断、启动")
    sys.argv = [sys.argv[0], mapping[command], *sys.argv[2:]]
    cli_main()


def check() -> int:
    problems = []
    if sys.version_info < (3, 11):
        problems.append("需要 Python 3.11 或更新版本。")
    venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.is_file():
        problems.append("还没有项目依赖。在项目目录执行：python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt")
    else:
        probe = subprocess.run([str(venv_python), "-c", "import fastapi, openpyxl, jieba, yaml, httpx"], cwd=ROOT, capture_output=True, text=True)
        if probe.returncode != 0:
            problems.append("依赖不完整。请执行：.venv/bin/python -m pip install -r requirements.txt")
    env_path = ROOT / ".env"
    if not env_path.is_file():
        problems.append("还没有 .env。可以先复制 .env.example，再填写 DeepSeek、百度和阿里云的密钥。")
    else:
        from app.config import load_settings

        settings = load_settings()
        for name, ready in (
            ("DEEPSEEK_API_KEY", settings.deepseek_configured),
            ("百度语音", settings.baidu_realtime_asr_configured),
            ("阿里云语音", settings.dashscope_tts_configured),
        ):
            print(f"{name}：{'已填写' if ready else '未填写'}")
    catalog = ROOT / "data" / "sources" / "catalog.json"
    pack = ROOT / "data" / "knowledge-pack" / "manifest.json"
    print(f"资料登记：{'有' if catalog.is_file() else '没有'}")
    print(f"知识包：{'有' if pack.is_file() else '没有'}")
    if catalog.is_file():
        from app.knowledge.catalog import CatalogError, load_catalog

        try:
            materials = load_catalog(catalog)
            print("已登记：" + "、".join(item.source_key for item in materials))
        except CatalogError as exc:
            problems.append(str(exc))
    if problems:
        print("还不能直接使用：")
        for item in problems:
            print(f"- {item}")
        return 1
    if pack.is_file():
        print("环境可以启动。已有知识包时可直接加载；表格有变动时再生成。")
    else:
        print("环境可以启动。还没有知识包，请先生成或放入知识包后再加载。")
    return 0


if __name__ == "__main__":
    main()
