"""从 Excel 导入条目。重复导入会更新或跳过，并写下每一条错误。"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.database import Database
from app.ingest.mapping import (
    apply_packed_fields,
    build_mapping,
    clean_cell,
    map_review_status,
    speech_kind,
)
from app.retrieval.tokenize import dumps_tokens, tokenize

CANONICAL_FIELDS = [
    "external_entry_id",
    "volume_label",
    "chapter",
    "section",
    "time_label",
    "place",
    "core_person",
    "relation_to_subject",
    "related_people",
    "person_relations",
    "event_title",
    "summary",
    "narrative_level",
    "information_provider",
    "speaker",
    "original_text",
    "context_note",
    "source_title",
    "collection_no",
    "volume",
    "page",
    "paragraph_no",
    "fact_tags",
    "persona_tags",
    "style_tags",
    "answerable_questions",
    "answer_points",
    "style_evidence",
    "evidence_type",
    "evidence_strength",
    "review_status_raw",
    "author",
    "material_type",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_id_for(filename: str) -> str:
    stem = Path(filename).stem.strip() or "untitled"
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:12]
    return f"src-{digest}"


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _content_hash(record: dict[str, str]) -> str:
    payload = {key: record.get(key, "") for key in CANONICAL_FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _detect_header(rows: list[tuple[Any, ...]]) -> int:
    limit = min(8, len(rows))
    for index in range(limit):
        texts = [_cell_text(cell) for cell in rows[index]]
        blob = " ".join(texts)
        if "条目ID" in blob or "原文" in blob or "正文" in blob:
            return index
    return 0


class ImportError(Exception):
    """文件本身无法读取时抛出，让界面显示清楚的原因。"""


def import_workbook(
    db: Database,
    path: Path,
    *,
    originals_dir: Path,
    is_demo: bool = False,
    origin_path: str | None = None,
) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise ImportError(f"找不到文件：{path}")
    if path.suffix.lower() != ".xlsx":
        raise ImportError("目前只能导入 .xlsx 文件。旧的 .xls 需要先在表格软件里另存为 .xlsx。")
    try:
        digest = file_sha256(path)
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise ImportError("无法读取这个 Excel 文件。它可能已损坏，或不是有效的 .xlsx。") from exc

    source_id = source_id_for(path.name)
    stored_dir = originals_dir / source_id
    stored_dir.mkdir(parents=True, exist_ok=True)
    stored_path = stored_dir / path.name
    if path.resolve() != stored_path.resolve():
        shutil.copy2(path, stored_path)
    timestamp = now_iso()
    existing_source = db.query_one("SELECT id FROM sources WHERE id = ?", (source_id,))
    if existing_source:
        db.execute(
            """
            UPDATE sources
               SET display_name = ?, origin_path = ?, stored_path = ?, content_sha256 = ?,
                   is_demo = ?, updated_at = ?
             WHERE id = ?
            """,
            (path.stem, origin_path or str(path), str(stored_path), digest, int(is_demo), timestamp, source_id),
        )
    else:
        db.execute(
            """
            INSERT INTO sources (
                id, display_name, origin_path, stored_path, content_sha256, is_demo, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_id, path.stem, origin_path or str(path), str(stored_path), digest, int(is_demo), timestamp, timestamp),
        )

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    db.execute(
        """
        INSERT INTO batches (id, source_id, filename, started_at, status, mapping_json)
        VALUES (?, ?, ?, ?, 'running', '{}')
        """,
        (batch_id, source_id, path.name, timestamp),
    )

    inserted = updated = unchanged = skipped = errors = warnings = data_rows = 0
    mappings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        for sheet in workbook.worksheets:
            rows = [tuple(row) for row in sheet.iter_rows(values_only=True)]
            if not rows:
                db.execute(
                    """
                    INSERT INTO import_issues (batch_id, severity, sheet_name, row_number, message)
                    VALUES (?, 'warning', ?, NULL, ?)
                    """,
                    (batch_id, sheet.title, "这个工作表是空的，已跳过。"),
                )
                warnings += 1
                continue
            header_index = _detect_header(rows)
            headers = [_cell_text(cell) for cell in rows[header_index]]
            while headers and not headers[-1]:
                headers.pop()
            mapping = build_mapping(headers)
            mappings.append({"sheet": sheet.title, **mapping.to_dict()})
            if "original_text" not in mapping.columns and "summary" not in mapping.columns:
                db.execute(
                    """
                    INSERT INTO import_issues (batch_id, severity, sheet_name, row_number, message)
                    VALUES (?, 'error', ?, ?, ?)
                    """,
                    (
                        batch_id,
                        sheet.title,
                        header_index + 1,
                        "没有找到“原文”或“概括”列，这个工作表未导入。请确认第一行是表头。",
                    ),
                )
                errors += 1
                continue
            header_positions = {name: headers.index(column) for name, column in mapping.columns.items()}
            unknown_positions = {
                header: headers.index(header) for header in mapping.unknown_headers if header in headers
            }
            for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
                values = [_cell_text(cell) for cell in row]
                if not any(values):
                    skipped += 1
                    continue
                data_rows += 1
                record = {name: "" for name in CANONICAL_FIELDS}
                for name, position in header_positions.items():
                    if position < len(values):
                        record[name] = clean_cell(values[position])
                record = apply_packed_fields(record)
                extra: dict[str, str] = {}
                for header, position in unknown_positions.items():
                    if position < len(values) and clean_cell(values[position]):
                        extra[header] = clean_cell(values[position])[:4000]
                if not record["original_text"] and not record["summary"]:
                    errors += 1
                    db.execute(
                        """
                        INSERT INTO import_issues (batch_id, severity, sheet_name, row_number, message)
                        VALUES (?, 'error', ?, ?, ?)
                        """,
                        (batch_id, sheet.title, offset, "这一行没有原文，也没有概括，已跳过并记录。"),
                    )
                    continue
                external_id = record["external_entry_id"] or f"row-{sheet.title}-{offset}"
                if external_id in seen_ids:
                    errors += 1
                    db.execute(
                        """
                        INSERT INTO import_issues (batch_id, severity, sheet_name, row_number, message)
                        VALUES (?, 'error', ?, ?, ?)
                        """,
                        (
                            batch_id,
                            sheet.title,
                            offset,
                            f"同一文件里条目 ID“{external_id}”重复，这一行没有覆盖前面的记录。",
                        ),
                    )
                    continue
                seen_ids.add(external_id)
                entry_id = f"{source_id}/{external_id}"
                digest_hash = _content_hash(record)
                duplicate = db.query_one(
                    "SELECT id FROM entries WHERE content_hash = ? AND id <> ?",
                    (digest_hash, entry_id),
                )
                if duplicate and record["original_text"]:
                    warnings += 1
                    db.execute(
                        """
                        INSERT INTO import_issues (batch_id, severity, sheet_name, row_number, message)
                        VALUES (?, 'warning', ?, ?, ?)
                        """,
                        (
                            batch_id,
                            sheet.title,
                            offset,
                            f"原文与已有条目 {duplicate['id']} 相同。本条仍会保存，方便你核对是不是重复导入。",
                        ),
                    )
                kind = speech_kind(record["narrative_level"], record["speaker"], record["relation_to_subject"])
                row_is_demo = is_demo or "演示" in record["material_type"] or "演示资料" in record["source_title"]
                review = map_review_status(record["review_status_raw"])
                token_source = "\n".join(
                    [
                        record["original_text"],
                        record["event_title"],
                        record["context_note"],
                        record["source_title"],
                        record["fact_tags"],
                        record["summary"],
                    ]
                )
                payload = {
                    **record,
                    "speech_kind": kind,
                    "review_status": review,
                    "is_demo": int(row_is_demo),
                    "can_quote": int(bool(record["original_text"])),
                    "content_hash": digest_hash,
                    "token_json": dumps_tokens(tokenize(token_source)),
                    "extra_json": json.dumps(extra, ensure_ascii=False),
                }
                current = db.query_one("SELECT content_hash FROM entries WHERE id = ?", (entry_id,))
                if current and current["content_hash"] == digest_hash:
                    unchanged += 1
                    continue
                action = "updated" if current else "inserted"
                if current:
                    updated += 1
                else:
                    inserted += 1
                _save_entry(
                    db,
                    entry_id=entry_id,
                    source_id=source_id,
                    sheet_name=sheet.title,
                    row_number=offset,
                    payload=payload,
                    timestamp=timestamp,
                    replacing=action == "updated",
                )
                if data_rows % 200 == 0:
                    _touch_batch(db, batch_id, inserted, updated, unchanged, skipped, errors, warnings, data_rows)
    finally:
        workbook.close()

    status = "completed_with_errors" if errors else "completed"
    message = f"读取 {data_rows} 行有效数据：新增 {inserted}，更新 {updated}，未变化 {unchanged}，空行跳过 {skipped}，错误 {errors}，提醒 {warnings}。"
    db.execute(
        """
        UPDATE batches
           SET finished_at = ?, status = ?, inserted = ?, updated = ?, unchanged = ?,
               skipped = ?, error_count = ?, warning_count = ?, row_count = ?,
               message = ?, mapping_json = ?
         WHERE id = ?
        """,
        (
            now_iso(),
            status,
            inserted,
            updated,
            unchanged,
            skipped,
            errors,
            warnings,
            data_rows,
            message,
            json.dumps(mappings, ensure_ascii=False),
            batch_id,
        ),
    )
    return {
        "batch_id": batch_id,
        "source_id": source_id,
        "status": status,
        "message": message,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "errors": errors,
        "warnings": warnings,
        "row_count": data_rows,
        "is_demo": is_demo,
        "mapping": mappings,
    }


def _save_entry(
    db: Database,
    *,
    entry_id: str,
    source_id: str,
    sheet_name: str,
    row_number: int,
    payload: dict[str, Any],
    timestamp: str,
    replacing: bool,
) -> None:
    columns = [
        "source_id",
        "external_entry_id",
        "sheet_name",
        "row_number",
        "author",
        "speaker",
        "speech_kind",
        "narrative_level",
        "information_provider",
        "core_person",
        "relation_to_subject",
        "related_people",
        "person_relations",
        "event_title",
        "summary",
        "original_text",
        "context_note",
        "source_title",
        "collection_no",
        "volume_label",
        "volume",
        "page",
        "paragraph_no",
        "chapter",
        "section",
        "time_label",
        "place",
        "material_type",
        "fact_tags",
        "persona_tags",
        "style_tags",
        "answerable_questions",
        "answer_points",
        "style_evidence",
        "evidence_type",
        "evidence_strength",
        "review_status",
        "review_status_raw",
        "is_demo",
        "can_quote",
        "content_hash",
        "token_json",
        "extra_json",
        "updated_at",
    ]
    values: dict[str, Any] = {
        "source_id": source_id,
        "sheet_name": sheet_name,
        "row_number": row_number,
        "updated_at": timestamp,
        **payload,
    }
    if replacing:
        assignments = ", ".join(f"{column} = ?" for column in columns)
        db.execute(
            f"UPDATE entries SET {assignments} WHERE id = ?",
            tuple(values[column] for column in columns) + (entry_id,),
        )
        return
    insert_columns = ["id", *columns, "created_at"]
    placeholders = ", ".join("?" for _ in insert_columns)
    db.execute(
        f"INSERT INTO entries ({', '.join(insert_columns)}) VALUES ({placeholders})",
        (entry_id, *[values[column] for column in columns], timestamp),
    )


def _touch_batch(
    db: Database,
    batch_id: str,
    inserted: int,
    updated: int,
    unchanged: int,
    skipped: int,
    errors: int,
    warnings: int,
    row_count: int,
) -> None:
    db.execute(
        """
        UPDATE batches
           SET inserted = ?, updated = ?, unchanged = ?, skipped = ?,
               error_count = ?, warning_count = ?, row_count = ?
         WHERE id = ?
        """,
        (inserted, updated, unchanged, skipped, errors, warnings, row_count, batch_id),
    )
