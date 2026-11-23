"""连续提问并打印每一轮的处理经过。给维护者用，不经过网页，不写聊天记录。

用法：
  .venv/bin/python scripts/对话检查.py "你爱秦德君吗" "所以你承认了" "那你能介绍一下秦德君吗"
  .venv/bin/python scripts/对话检查.py --file 问题.txt        每行一问，空行表示另开一场对话
  加 --save 结果.json 可以把完整记录存下来；加 --no-model 可以不调用模型，只看程序的处理

会真实调用 DeepSeek（除非加 --no-model）。不打印密钥。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="连续提问并查看处理经过")
    parser.add_argument("questions", nargs="*")
    parser.add_argument("--file", default="")
    parser.add_argument("--save", default="")
    parser.add_argument("--no-model", action="store_true")
    args = parser.parse_args()

    sessions: list[list[str]] = []
    if args.file:
        current: list[str] = []
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text.startswith("#"):
                continue
            if not text:
                if current:
                    sessions.append(current)
                    current = []
                continue
            current.append(text)
        if current:
            sessions.append(current)
    if args.questions:
        sessions.append(list(args.questions))
    if not sessions:
        raise SystemExit("请给出问题，或用 --file 指定问题文件。")

    import httpx

    from app.cli import trace_summary
    from app.config import load_settings
    from app.database import Database
    from app.knowledge.index import KnowledgeIndex
    from app.knowledge.respond import answer_from_corpus
    from app.knowledge.store import ensure_schema
    from app.llm.providers import DeepSeekChatModel

    settings = load_settings()
    db = Database(settings.database_path)
    ensure_schema(db)
    started = time.perf_counter()
    index = KnowledgeIndex.load(db)
    db.close()
    print(f"知识库载入 {time.perf_counter() - started:.1f} 秒：{index.status_message()}")
    http = httpx.Client(timeout=settings.request_timeout_seconds)
    model = None if args.no_model else DeepSeekChatModel(settings, http)
    saved = []
    try:
        from app.session.memory import build_context, update_after_answer

        for number, questions in enumerate(sessions, start=1):
            print(f"\n================ 第 {number} 场对话 ================")
            history: list[dict] = []
            focus: dict = {}
            for order, question in enumerate(questions):
                bundle = build_context(history, question, focus)
                focus = bundle["focus"]
                public, audit, focus = answer_from_corpus(
                    index,
                    question,
                    model=model,
                    history=bundle["working"],
                    focus=focus,
                    provider_name="local_corpus",
                    memory=bundle["pack"],
                )
                focus = update_after_answer(
                    focus,
                    history,
                    question=question,
                    spoken=public["spoken_text"],
                    mode=str(audit.get("mode") or ""),
                    reading=(audit.get("trace") or {}).get("reading") or {},
                )
                trace = audit["trace"]
                print(trace_summary(trace))
                print(f"记入审计的方式：{audit['mode']}；备注：{audit['notice'] or '无'}\n")
                spoken = public["spoken_text"]
                usable = audit["mode"] not in {"model_error", "no_model", "not_ready"} and not spoken.startswith(("这一次没有答成", "资料还没有准备好"))
                history.append(
                    {
                        "id": f"check-{number}-{order}",
                        "question": question,
                        "spoken": spoken,
                        "usable": usable,
                        "mode": audit["mode"],
                        "created_at": f"{order:04d}",
                    }
                )
                saved.append({"session": number, "mode": audit["mode"], "notice": audit["notice"], **trace})
    finally:
        http.close()
    if args.save:
        Path(args.save).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完整记录已存到 {args.save}")


if __name__ == "__main__":
    main()
