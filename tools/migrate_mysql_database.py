import os
import sys
from typing import Dict, List


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
        "target_db": load_env_value(env_path, "DBNAME", "argus"),
        "source_db": os.environ.get("ARGUS_SOURCE_DB", "pity"),
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
        charset="utf8mb4",
        autocommit=False,
    )
    summary: List[str] = []
    try:
        with conn.cursor() as cursor:
            source_db = config["source_db"]
            target_db = config["target_db"]
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")

            cursor.execute(f"SHOW DATABASES LIKE %s", (source_db,))
            if cursor.fetchone() is None:
                print(f"source database not found: {source_db}", file=sys.stderr)
                return 3

            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {quote_name(target_db)} "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )

            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME ASC",
                (source_db,),
            )
            table_names = [row[0] for row in cursor.fetchall()]
            if not table_names:
                print(f"no tables found in source database: {source_db}")
                conn.commit()
                return 0

            for table_name in table_names:
                quoted_table = quote_name(table_name)
                cursor.execute(
                    "SELECT COUNT(1) FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (target_db, table_name),
                )
                table_exists = int(cursor.fetchone()[0] or 0) > 0
                if not table_exists:
                    cursor.execute(f"SHOW CREATE TABLE {quote_name(source_db)}.{quoted_table}")
                    create_stmt = cursor.fetchone()[1]
                    create_stmt = create_stmt.replace(
                        f"CREATE TABLE {quoted_table}",
                        f"CREATE TABLE {quote_name(target_db)}.{quoted_table}",
                        1,
                    )
                    cursor.execute(create_stmt)

                cursor.execute(f"SELECT COUNT(1) FROM {quote_name(target_db)}.{quoted_table}")
                target_count = int(cursor.fetchone()[0] or 0)
                if target_count > 0:
                    summary.append(f"{table_name}: skipped_existing_rows={target_count}")
                    continue

                cursor.execute(
                    f"INSERT INTO {quote_name(target_db)}.{quoted_table} "
                    f"SELECT * FROM {quote_name(source_db)}.{quoted_table}"
                )
                inserted = int(cursor.rowcount or 0)
                summary.append(f"{table_name}: inserted_rows={inserted}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            reset_conn = pymysql.connect(
                host=config["host"],
                port=int(config["port"]),
                user=config["user"],
                password=config["password"],
                charset="utf8mb4",
                autocommit=True,
            )
            with reset_conn.cursor() as reset_cursor:
                reset_cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            reset_conn.close()
        except Exception:
            pass
        conn.close()

    print("database migration completed")
    for item in summary:
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
