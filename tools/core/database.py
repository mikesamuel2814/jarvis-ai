"""
Jarvis v3 Database Tools (8 tools)
Category: database | Rank: R2-R3 | Scope: READ, LOCAL, PRIVILEGED
"""

import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from ..decorator import jarvis_tool
from ..result import ToolResult


@jarvis_tool(
    name="psql_query",
    description="Run PostgreSQL query (query, database, user)",
    params={"query": {"type": "string", "required": True}, "database": {"type": "string", "required": True}, "user": {"type": "string", "required": False, "default": "postgres"}},
    rank="R2", scope="READ", category="database", tags=["postgres", "sql", "query"]
)
def psql_query(query: str, database: str, user: str = "postgres") -> ToolResult:
    try:
        cmd = ["sudo", "-u", user, "psql", "-d", database, "-c", query]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="psql_query")
        return ToolResult.ok(output=output.strip(), tool_name="psql_query")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="psql_query")


@jarvis_tool(
    name="mysql_query",
    description="Run MySQL query (query, database, user)",
    params={"query": {"type": "string", "required": True}, "database": {"type": "string", "required": True}, "user": {"type": "string", "required": False, "default": "root"}},
    rank="R2", scope="READ", category="database", tags=["mysql", "sql", "query"]
)
def mysql_query(query: str, database: str, user: str = "root") -> ToolResult:
    try:
        cmd = ["mysql", "-u", user, database, "-e", query]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="mysql_query")
        return ToolResult.ok(output=output.strip(), tool_name="mysql_query")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="mysql_query")


@jarvis_tool(
    name="mongo_query",
    description="Run MongoDB query (query, database, collection)",
    params={"query": {"type": "string", "required": True}, "database": {"type": "string", "required": True}, "collection": {"type": "string", "required": True}},
    rank="R2", scope="READ", category="database", tags=["mongodb", "nosql", "query"]
)
def mongo_query(query: str, database: str, collection: str) -> ToolResult:
    try:
        full_query = f"use {database}; db.{collection}.{query}"
        result = subprocess.run(["mongosh", "--eval", full_query], capture_output=True, text=True, timeout=30)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="mongo_query")
        return ToolResult.ok(output=output.strip(), tool_name="mongo_query")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="mongo_query")


@jarvis_tool(
    name="redis_cli",
    description="Run Redis command (command)",
    params={"command": {"type": "string", "required": True}},
    rank="R2", scope="READ", category="database", tags=["redis", "cli", "cache"]
)
def redis_cli(command: str) -> ToolResult:
    try:
        cmd = ["redis-cli"] + command.split()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="redis_cli")
        return ToolResult.ok(output=output.strip(), tool_name="redis_cli")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="redis_cli")


@jarvis_tool(
    name="sqlite_query",
    description="Run SQLite query (query, database_path)",
    params={"query": {"type": "string", "required": True}, "database_path": {"type": "string", "required": True}},
    rank="R2", scope="READ", category="database", tags=["sqlite", "sql", "query"]
)
def sqlite_query(query: str, database_path: str) -> ToolResult:
    try:
        conn = sqlite3.connect(database_path)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.close()
        data = {"columns": columns, "rows": rows, "row_count": len(rows)}
        lines = [" | ".join(columns)] if columns else []
        for row in rows:
            lines.append(" | ".join(str(c) for c in row))
        output = "\n".join(lines)
        return ToolResult.ok(output=output, data=data, tool_name="sqlite_query")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="sqlite_query")


@jarvis_tool(
    name="db_backup",
    description="Backup database (type, database, output_path)",
    params={"db_type": {"type": "string", "required": True}, "database": {"type": "string", "required": True}, "output_path": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="database", tags=["backup", "database"]
)
def db_backup(db_type: str, database: str, output_path: str) -> ToolResult:
    try:
        if db_type.lower() == "postgres":
            cmd = ["pg_dump", "-Fc", database, "-f", output_path]
        elif db_type.lower() == "mysql":
            cmd = ["mysqldump", database, ">", output_path]
        elif db_type.lower() == "sqlite":
            cmd = ["sqlite3", database, f".backup '{output_path}'"]
        else:
            return ToolResult.fail(error=f"Unsupported backup type: {db_type}", tool_name="db_backup")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="db_backup")
        return ToolResult.ok(output=f"Backup saved to {output_path}", tool_name="db_backup")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="db_backup")


@jarvis_tool(
    name="db_restore",
    description="Restore database (type, database, backup_path)",
    params={"db_type": {"type": "string", "required": True}, "database": {"type": "string", "required": True}, "backup_path": {"type": "string", "required": True}},
    rank="R3", scope="PRIVILEGED", category="database", tags=["restore", "database"]
)
def db_restore(db_type: str, database: str, backup_path: str) -> ToolResult:
    try:
        if db_type.lower() == "postgres":
            cmd = ["pg_restore", "-d", database, backup_path]
        elif db_type.lower() == "mysql":
            cmd = ["mysql", database, "<", backup_path]
        elif db_type.lower() == "sqlite":
            cmd = ["sqlite3", database, f".restore '{backup_path}'"]
        else:
            return ToolResult.fail(error=f"Unsupported restore type: {db_type}", tool_name="db_restore")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="db_restore")
        return ToolResult.ok(output=f"Database {database} restored from {backup_path}", tool_name="db_restore")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="db_restore")


@jarvis_tool(
    name="db_migrate",
    description="Run database migrations (type, directory)",
    params={"db_type": {"type": "string", "required": True}, "directory": {"type": "string", "required": True}},
    rank="R2", scope="LOCAL", category="database", tags=["migration", "database"]
)
def db_migrate(db_type: str, directory: str) -> ToolResult:
    try:
        if db_type.lower() == "alembic":
            cmd = ["alembic", "upgrade", "head"]
        elif db_type.lower() == "django":
            cmd = ["python3", "manage.py", "migrate"]
        elif db_type.lower() == "flask":
            cmd = ["flask", "db", "upgrade"]
        elif db_type.lower() == "prisma":
            cmd = ["prisma", "migrate", "deploy"]
        else:
            return ToolResult.fail(error=f"Unsupported migration type: {db_type}", tool_name="db_migrate")
        result = subprocess.run(cmd, cwd=directory, capture_output=True, text=True, timeout=120)
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return ToolResult.fail(error=output.strip(), tool_name="db_migrate")
        return ToolResult.ok(output=output.strip(), tool_name="db_migrate")
    except Exception as exc:
        return ToolResult.fail(error=str(exc), tool_name="db_migrate")
