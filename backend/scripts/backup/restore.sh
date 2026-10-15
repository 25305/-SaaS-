#!/usr/bin/env bash
# 恢复策略：默认只能恢复到新文件，保护线上库；生产恢复须显式确认。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ $# -ge 1 ]] || { echo "用法: restore.sh <备份文件> [恢复目标SQLite文件]" >&2; exit 2; }
SRC="$1"
[[ -f "$SRC" ]] || { echo "备份不存在: $SRC" >&2; exit 1; }
MODE="${BACKUP_MODE:-sqlite}"
if [[ "$MODE" == "sqlite" ]]; then
  TARGET="${2:-$ROOT/backups/restored_$(date +%Y%m%d_%H%M%S).sqlite3}"
  [[ "$TARGET" != "${SQLITE_PATH:-$ROOT/qa.db}" ]] || {
    [[ "${ALLOW_RESTORE_PRODUCTION:-}" == "YES" ]] || {
      echo "拒绝覆盖运行中的数据库；先停服，并设置 ALLOW_RESTORE_PRODUCTION=YES 确认" >&2; exit 1;
    }
  }
  [[ ! -e "$TARGET" ]] || { echo "拒绝覆盖已有文件: $TARGET" >&2; exit 1; }
  PYTHON_BIN="${PYTHON_BIN:-python}"
  "$PYTHON_BIN" -c 'import sqlite3,sys
src=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True)
result=src.execute("PRAGMA quick_check").fetchone()[0]
if result != "ok": raise SystemExit("备份完整性校验失败: "+result)
dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()' "$SRC" "$TARGET"
  echo "恢复完成: $TARGET"
elif [[ "$MODE" == "mysql" ]]; then
  [[ "${ALLOW_RESTORE_PRODUCTION:-}" == "YES" ]] || {
    echo "拒绝向 MySQL 执行恢复；请先完成备份、停服并设置 ALLOW_RESTORE_PRODUCTION=YES" >&2; exit 1;
  }
  : "${DB_HOST:?请设置 DB_HOST}" "${DB_PORT:?请设置 DB_PORT}" "${DB_USER:?请设置 DB_USER}" "${DB_NAME:?请设置 DB_NAME}" "${DB_PASSWORD:?请设置 DB_PASSWORD}"
  gzip -t "$SRC"
  gunzip -c "$SRC" | MYSQL_PWD="$DB_PASSWORD" mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME"
  echo "恢复完成: $SRC -> MySQL $DB_NAME"
else
  echo "不支持的 BACKUP_MODE: $MODE (sqlite/mysql)" >&2
  exit 1
fi
