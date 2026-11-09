import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .settings import RUNTIME, redact

TERMINAL = {'success', 'failed', 'cancelled', 'interrupted'}


@contextmanager
def connection():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(RUNTIME / 'tasks.sqlite3'), timeout=15) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, scene TEXT NOT NULL, user_id INTEGER NOT NULL,
            user_name TEXT, status TEXT NOT NULL, fields TEXT, result TEXT,
            created REAL, updated REAL, finished REAL, cancel_requested INTEGER DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, message TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS ix_df_log_task ON logs(task_id, id)')
        db.execute('CREATE INDEX IF NOT EXISTS ix_df_task_user ON tasks(user_id, scene, created)')
        yield db


def serialize(row):
    value = dict(row)
    for key in ('fields', 'result'):
        value[key] = json.loads(value[key]) if value[key] else None
    for key in ('created', 'updated', 'finished'):
        value[key] = datetime.fromtimestamp(value[key], timezone(timedelta(hours=8))).isoformat() if value[key] else None
    return value


def create(scene, user_id, user_name, fields):
    task_id = uuid.uuid4().hex
    now = time.time()
    with connection() as db:
        db.execute('INSERT INTO tasks(id,scene,user_id,user_name,status,fields,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                   (task_id, scene, user_id, user_name, 'queued', json.dumps(redact(fields), ensure_ascii=False), now, now))
    return task_id


def reconcile(db):
    db.execute("UPDATE tasks SET status='interrupted', finished=?, result=? WHERE status IN ('queued','running') AND updated < ?",
               (time.time(), json.dumps({'status': False, 'msg': '任务进程中断，请检查业务侧状态后重新执行'}, ensure_ascii=False), time.time() - 60))


def get(task_id, user_id):
    with connection() as db:
        reconcile(db)
        row = db.execute('SELECT * FROM tasks WHERE id=? AND user_id=?', (task_id, user_id)).fetchone()
        return serialize(row) if row else None


def history(user_id, scene=None, page=1, size=20, status=None, started_after=None, started_before=None):
    with connection() as db:
        reconcile(db)
        clauses, params = ['user_id=?'], [user_id]
        for clause, value in [('scene=?', scene), ('status=?', status), ('created>=?', started_after), ('created<=?', started_before)]:
            if value is not None and value != '':
                clauses.append(clause)
                params.append(value)
        where = ' AND '.join(clauses)
        total = db.execute('SELECT COUNT(*) FROM tasks WHERE ' + where, params).fetchone()[0]
        rows = db.execute('SELECT * FROM tasks WHERE ' + where + ' ORDER BY created DESC LIMIT ? OFFSET ?',
                          params + [size, (page - 1) * size]).fetchall()
        return {'list': [serialize(row) for row in rows], 'total': total, 'page': page, 'size': size}


def statistics():
    with connection() as db:
        return dict(db.execute('SELECT scene,COUNT(*) FROM tasks GROUP BY scene').fetchall())


def update(task_id, status=None, result=None):
    with connection() as db:
        if status in TERMINAL:
            db.execute('UPDATE tasks SET status=?,result=?,updated=?,finished=? WHERE id=?',
                       (status, json.dumps(redact(result), ensure_ascii=False), time.time(), time.time(), task_id))
        elif status:
            db.execute('UPDATE tasks SET status=?,updated=? WHERE id=?', (status, time.time(), task_id))
        else:
            db.execute('UPDATE tasks SET updated=? WHERE id=?', (time.time(), task_id))


def log(task_id, message):
    with connection() as db:
        db.execute('INSERT INTO logs(task_id,message) VALUES(?,?)', (task_id, str(message)[:12000]))
        # Bound retained logs without removing the monotonic cursor.
        cutoff = db.execute('SELECT id FROM logs WHERE task_id=? ORDER BY id DESC LIMIT 1 OFFSET 19999', (task_id,)).fetchone()
        if cutoff:
            db.execute('DELETE FROM logs WHERE task_id=? AND id<?', (task_id, cutoff[0]))


def logs(task_id, after=0):
    with connection() as db:
        rows = db.execute('SELECT id,message FROM logs WHERE task_id=? AND id>? ORDER BY id LIMIT 500', (task_id, after)).fetchall()
        return [dict(row) for row in rows]


def cancel(task_id, user_id):
    with connection() as db:
        db.execute("UPDATE tasks SET cancel_requested=1 WHERE id=? AND user_id=? AND status IN ('queued','running')", (task_id, user_id))


def should_cancel(task_id):
    with connection() as db:
        row = db.execute('SELECT cancel_requested FROM tasks WHERE id=?', (task_id,)).fetchone()
        return not row or bool(row[0])
