import os
import sys
from typing import Dict, List, Tuple


def load_env_value(path: str, key: str, default: str = "") -> str:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fp:
        for raw_line in fp:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            current_key, value = line.split("=", 1)
            if current_key.strip() != key:
                continue
            return value.strip().strip('"').strip("'")
    return default


def build_config() -> Dict[str, str]:
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "conf", "dev.env")
    return {
        "host": load_env_value(env_path, "MYSQL_HOST", "127.0.0.1"),
        "port": load_env_value(env_path, "MYSQL_PORT", "3306"),
        "user": load_env_value(env_path, "MYSQL_USER", "root"),
        "password": load_env_value(env_path, "MYSQL_PWD", ""),
        "database": load_env_value(env_path, "DBNAME", "argus"),
    }


def quote_name(name: str) -> str:
    return f"`{str(name).replace('`', '``')}`"


def main() -> int:
    config = build_config()
    try:
        import pymysql
    except ImportError:
        print("pymysql is required", file=sys.stderr)
        return 2

    conn = pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["user"],
        password=config["password"],
        database=config["database"],
        charset="utf8mb4",
        autocommit=False,
    )
    summary: List[str] = []
    try:
        with conn.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME LIKE 'pity\\_%%' "
                "ORDER BY TABLE_NAME ASC",
                (config["database"],),
            )
            source_tables = [row[0] for row in cursor.fetchall()]
            if not source_tables:
                print("no pity_* tables found")
                conn.commit()
                return 0

            rename_pairs: List[Tuple[str, str]] = []
            for source_name in source_tables:
                target_name = source_name.replace("pity_", "argus_", 1)
                rename_pairs.append((source_name, target_name))

            for source_name, target_name in rename_pairs:
                cursor.execute(
                    "SELECT COUNT(1) FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (config["database"], target_name),
                )
                target_exists = int(cursor.fetchone()[0] or 0) > 0
                if target_exists:
                    cursor.execute(f"SELECT COUNT(1) FROM {quote_name(target_name)}")
                    target_count = int(cursor.fetchone()[0] or 0)
                    if target_count > 0:
                        summary.append(f"{source_name} -> {target_name}: skipped_target_has_rows={target_count}")
                        continue
                    cursor.execute(f"DROP TABLE {quote_name(target_name)}")
                    summary.append(f"{target_name}: dropped_empty_target")

                cursor.execute(f"RENAME TABLE {quote_name(source_name)} TO {quote_name(target_name)}")
                summary.append(f"{source_name} -> {target_name}: renamed")

            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("table rename completed")
    for item in summary:
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
