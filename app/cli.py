"""命令行：生成知识库、加载知识包、导入演示表格。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import load_settings
from app.knowledge.catalog import pack_dir
from app.knowledge.import_sources import CorpusImportError, import_corpus, import_registered, remove_source
from app.knowledge.package import PackError, export_pack, load_pack
from app.runtime import Runtime, WorkbookImportError


def main() -> None:
    parser = argparse.ArgumentParser(description="茅盾数字人资料工具")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="按 data/sources/catalog.json 完整生成知识库")
    update_cmd = commands.add_parser("update", help="只替换登记中的一份资料")
    update_cmd.add_argument("source_key")
    add_cmd = commands.add_parser("add", help="增加一份登记中还没有进入知识库的资料")
    add_cmd.add_argument("source_key")
    remove_cmd = commands.add_parser("remove", help="删除一份资料，不影响其他资料和聊天记录")
    remove_cmd.add_argument("source_key")
    export_cmd = commands.add_parser("export-pack", help="导出不含聊天记录和密钥的知识包")
    export_cmd.add_argument("directory", nargs="?", default="")
    load_cmd = commands.add_parser("load-pack", help="用知识包替换或并入知识，保留聊天记录")
    load_cmd.add_argument("directory", nargs="?", default="")
    load_cmd.add_argument("--mode", choices=("replace", "merge"), default="replace")
    import_cmd = commands.add_parser("import", help="把一份演示或历史 Excel 导入管理页使用的旧条目")
    import_cmd.add_argument("path")
    commands.add_parser("demo", help="导入标明为演示、并非馆藏的测试资料")
    corpus_cmd = commands.add_parser("import-corpus", help="按表头识别一个目录里的资料，只更新读到的部分")
    corpus_cmd.add_argument("directory")
    search_cmd = commands.add_parser("search", help="用演示条目检索试一个问题")
    search_cmd.add_argument("query")
    trace_cmd = commands.add_parser("trace", help="查看最近几轮问答的处理经过（只给维护者看）")
    trace_cmd.add_argument("session_id", nargs="?", default="", help="会话编号；不填就看最近的几轮")
    trace_cmd.add_argument("--last", type=int, default=3, help="看最近几轮，默认 3")
    trace_cmd.add_argument("--full", action="store_true", help="输出完整记录，不只是摘要")
    args = parser.parse_args()

    settings = load_settings()
    if args.command == "trace":
        _trace(settings, args.session_id, args.last, args.full)
        return
    runtime = Runtime(settings)
    try:
        if args.command == "demo":
            print(json.dumps(runtime.import_demo(), ensure_ascii=False, indent=2))
            return
        if args.command == "build":
            _print(import_registered(runtime.db, "rebuild", root=settings.root))
            runtime._reload_knowledge_if_needed()
            return
        if args.command == "update":
            _print(import_registered(runtime.db, "update", args.source_key, root=settings.root))
            runtime._reload_knowledge_if_needed()
            return
        if args.command == "add":
            _print(import_registered(runtime.db, "add", args.source_key, root=settings.root))
            runtime._reload_knowledge_if_needed()
            return
        if args.command == "remove":
            _print(remove_source(runtime.db, args.source_key, root=settings.root))
            runtime._reload_knowledge_if_needed()
            return
        if args.command == "export-pack":
            target = Path(args.directory) if args.directory else pack_dir()
            _print(export_pack(runtime.db, target))
            return
        if args.command == "load-pack":
            target = Path(args.directory) if args.directory else pack_dir()
            _print(load_pack(runtime.db, target, args.mode, root=settings.root))
            runtime._reload_knowledge_if_needed()
            return
        if args.command == "import-corpus":
            _print(runtime.import_corpus(Path(args.directory)))
            return
        if args.command == "import":
            try:
                report = runtime.import_path(Path(args.path))
            except WorkbookImportError as exc:
                raise SystemExit(str(exc)) from exc
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        print(json.dumps(runtime.search(args.query), ensure_ascii=False, indent=2))
    except (CorpusImportError, PackError) as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        runtime.close()


def _trace(settings, session_id: str, last: int, full: bool) -> None:
    """只读数据库，不加载知识库，也不调用模型。"""

    from app.database import Database
    from app.knowledge.store import ensure_schema

    db = Database(settings.database_path)
    try:
        ensure_schema(db)
        if session_id:
            rows = db.query(
                "SELECT turn_id, session_id, created_at, trace_json FROM answer_trace WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, max(last, 1)),
            )
        else:
            rows = db.query(
                "SELECT turn_id, session_id, created_at, trace_json FROM answer_trace ORDER BY created_at DESC LIMIT ?",
                (max(last, 1),),
            )
    finally:
        db.close()
    if not rows:
        print("还没有处理经过的记录。新的提问之后才会有。")
        return
    for row in reversed(rows):
        trace = json.loads(row["trace_json"] or "{}")
        if full:
            print(json.dumps({"turn_id": row["turn_id"], "session_id": row["session_id"], "created_at": row["created_at"], **trace}, ensure_ascii=False, indent=2))
            continue
        print(trace_summary(trace, row["session_id"], row["created_at"]))
        print()


def trace_summary(trace: dict, session_id: str = "", created_at: str = "") -> str:
    reading = trace.get("reading") or {}
    retrieval = trace.get("retrieval") or {}
    lines = [
        f"【{created_at}】会话 {session_id}" if session_id else "【本轮】",
        f"问：{trace.get('question', '')}",
        f"答：{trace.get('final', '')}",
        f"正文来历：{trace.get('final_source', '')}（fixed 固定句，model 模型一稿，rewrite 重写稿，kept_part 只留合格句，fallback 备用句，error 请求失败）",
        f"去向：{trace.get('route', '')}。{trace.get('route_reason', '')}",
    ]
    if trace.get("early_return"):
        lines.append(f"提前返回的位置：{trace['early_return']}")
    if reading:
        parts = "；".join(f"{part.get('type')}/{part.get('object') or '-'}/{part.get('ask') or '-'}" for part in reading.get("parts") or [])
        lines.append(f"理解（{reading.get('source')}）：{parts}；人物={reading.get('person') or '-'}；作品={reading.get('work') or '-'}；未确认前提={reading.get('premise') or '-'}")
    if retrieval.get("ran"):
        lines.append(f"检索词：{retrieval.get('query', '')}")
        for item in retrieval.get("adopted") or []:
            lines.append(f"  采用 {item.get('label')} {item.get('id')}｜{item.get('归属')}｜可支持：{item.get('可以支持')}｜不能支持：{item.get('不能支持')}")
        for item in retrieval.get("withheld") or []:
            lines.append(f"  扣下 {item.get('id')} 的{item.get('field')}中的一句：{item.get('reason')}")
        if retrieval.get("used"):
            lines.append(f"  模型说用到：{'、'.join(retrieval['used'])}")
        if retrieval.get("missing") or retrieval.get("model_missing"):
            lines.append(f"  没有覆盖：{'、'.join(retrieval.get('missing') or [])} {retrieval.get('model_missing') or ''}".rstrip())
    else:
        lines.append("没有检索。")
    for note in trace.get("sufficiency") or []:
        lines.append(f"材料判断：{note}")
    plan = trace.get("plan") or {}
    if plan:
        lines.append(
            f"回答安排：动作={plan.get('action') or '-'}；对象={plan.get('person') or '-'}；"
            f"aim={plan.get('aim') or '-'}；判断={plan.get('judgment') or '-'}；材料={plan.get('hit_count', 0)}条"
        )
    memory = trace.get("memory") or {}
    meta = memory.get("meta") or memory
    if meta.get("total_turns") is not None or memory.get("recalled") or memory.get("summary_segments"):
        lines.append(
            f"会话记忆：总轮次={meta.get('total_turns', '-')}；近期={meta.get('recent_count', '-')}；"
            f"摘要段={meta.get('summary_segments', memory.get('summary_segments', 0))}；"
            f"本轮找回={len(meta.get('recalled_ids') or []) or memory.get('recalled', 0)}"
        )
    continuity = trace.get("continuity") or {}
    if continuity:
        lines.append(f"承接：mode={continuity.get('mode') or '-'}；{continuity.get('note') or ''}".rstrip("；"))
    lines.append(f"规则版本 {trace.get('rules_version')}，风格版本 {trace.get('style_version')}，样本 {'、'.join(trace.get('style_samples') or []) or '无'}")
    calls = trace.get("calls") or []
    lines.append("模型调用：" + ("；".join(f"{call['purpose']} {call['ms']}ms{'' if call['ok'] else ' 失败：' + call['error']}" for call in calls) or "无") + f"。全程 {trace.get('ms', 0)}ms")
    for draft in trace.get("drafts") or []:
        if draft.get("stage") == "private_candidates":
            for candidate in draft.get("candidates") or []:
                lines.append(f"  候选：{candidate['text']}｜{'、'.join(candidate['problems']) or '合格'}")
        elif draft.get("stage") in {"draft", "rewrite"}:
            problems = "、".join(f"{item['kind']}（{item.get('detail') or item.get('sentence') or ''}）" for item in draft.get("findings") or []) or "合格"
            lines.append(f"  {'一稿' if draft['stage'] == 'draft' else '重写稿'}：{draft.get('text', '')}｜{problems}")
        elif draft.get("error"):
            lines.append(f"  {draft.get('stage')}：{draft['error']}")
    return "\n".join(lines)


def _print(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
