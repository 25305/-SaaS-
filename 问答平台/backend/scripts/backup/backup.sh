#!/usr/bin/env bash
# 安全备份：开发期 SQLite 在线一致性快照；生产 MySQL 单事务导出。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
MODE="${BACKUP_MODE:-sqlite}"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
if [[ "$MODE" == "sqlite" ]]; then
  DB_FILE="${SQLITE_PATH:-$ROOT/qa.db}"
  [[ -f "$DB_FILE" ]] || { echo "数据库文件不存在: $DB_FILE" >&2; exit 1; }
  TARGET="$BACKUP_DIR/smartqa_saas_${STAMP}.sqlite3"
  PYTHON_BIN="${PYTHON_BIN:-python}"
  "$PYTHON_BIN" -c 'import sqlite3,sys
src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()' "$DB_FILE" "$TARGET"
  echo "备份完成: $TARGET"
elif [[ "$MODE" == "mysql" ]]; then
  : "${DB_HOST:?请设置 DB_HOST}" "${DB_PORT:?请设置 DB_PORT}" "${DB_USER:?请设置 DB_USER}" "${DB_NAME:?请设置 DB_NAME}" "${DB_PASSWORD:?请设置 DB_PASSWORD}"
  TARGET="$BACKUP_DIR/smartqa_saas_${STAMP}.sql.gz"
  TMP="$TARGET.tmp"
  trap 'rm -f "$TMP"' EXIT
  # 不在命令行传密码；避免出现在 ps、shell 历史、错误日志。
  MYSQL_PWD="$DB_PASSWORD" mysqldump --single-transaction --routines --triggers \
    -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" | gzip -c > "$TMP"
  gzip -t "$TMP"
  mv "$TMP" "$TARGET"
  trap - EXIT
  echo "备份完成: $TARGET"
else
  echo "不支持的 BACKUP_MODE: $MODE (sqlite/mysql)" >&2
  exit 1
fi
