"""Legacy asynchronous SQLite database module retained during MySQL migration."""
import json
import uuid

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,ai_name TEXT,role TEXT,content TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS memory_summaries (id INTEGER PRIMARY KEY AUTOINCREMENT,ai_name TEXT,session_id TEXT,summary TEXT,key_topics TEXT,important_facts TEXT,user_preferences TEXT,unresolved_issues TEXT,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,session_end_at DATETIME);
CREATE TABLE IF NOT EXISTS ai_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,ai_name TEXT,session_id TEXT UNIQUE,start_time DATETIME DEFAULT CURRENT_TIMESTAMP,message_count INTEGER DEFAULT 0,is_active BOOLEAN DEFAULT 1);
CREATE TABLE IF NOT EXISTS cookies (id INTEGER PRIMARY KEY AUTOINCREMENT,ai_name TEXT UNIQUE,cookie_json TEXT,updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memories_ai ON memory_summaries(ai_name, created_at DESC);
"""


class Database:
    """Original SQLite API, preserved for reference only."""

    def __init__(self, path):
        self.path = str(path)

    async def _conn(self):
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=5000")
        return connection

    async def initialize(self):
        connection = await self._conn()
        try:
            await connection.executescript(SCHEMA)
            await connection.commit()
        finally:
            await connection.close()

    async def create_session(self, ai_name):
        session_id = f"{ai_name}-{uuid.uuid4().hex[:12]}"
        connection = await self._conn()
        try:
            await connection.execute("UPDATE ai_sessions SET is_active=0 WHERE ai_name=?", (ai_name,))
            await connection.execute("INSERT INTO ai_sessions(ai_name,session_id) VALUES(?,?)", (ai_name, session_id))
            await connection.commit()
            return session_id
        finally:
            await connection.close()

    async def get_active_session(self, ai_name):
        connection = await self._conn()
        try:
            row = await (await connection.execute(
                "SELECT * FROM ai_sessions WHERE ai_name=? AND is_active=1 ORDER BY id DESC LIMIT 1",
                (ai_name,),
            )).fetchone()
            return dict(row) if row else None
        finally:
            await connection.close()

    async def ensure_session(self, ai_name):
        active = await self.get_active_session(ai_name)
        return active["session_id"] if active else await self.create_session(ai_name)

    async def add_message(self, session_id, ai_name, role, content):
        connection = await self._conn()
        try:
            await connection.execute(
                "INSERT INTO messages(session_id,ai_name,role,content) VALUES(?,?,?,?)",
                (session_id, ai_name, role, content),
            )
            await connection.execute("UPDATE ai_sessions SET message_count=message_count+1 WHERE session_id=?", (session_id,))
            await connection.commit()
        finally:
            await connection.close()

    async def get_messages(self, session_id=None, limit=500):
        connection = await self._conn()
        try:
            query = "SELECT * FROM messages" + (" WHERE session_id=?" if session_id else "") + " ORDER BY id DESC LIMIT ?"
            params = (session_id, limit) if session_id else (limit,)
            rows = await (await connection.execute(query, params)).fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            await connection.close()

    async def session_messages(self, session_id):
        return await self.get_messages(session_id, 1000)

    async def save_memory(self, ai, session_id, summary, topics="", facts=None, prefs=None, issues=None):
        connection = await self._conn()
        try:
            await connection.execute(
                "INSERT INTO memory_summaries(ai_name,session_id,summary,key_topics,important_facts,user_preferences,unresolved_issues,session_end_at) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (ai, session_id, summary[:300], topics, json.dumps(facts or [], ensure_ascii=False), json.dumps(prefs or {}, ensure_ascii=False), json.dumps(issues or [], ensure_ascii=False)),
            )
            await connection.commit()
        finally:
            await connection.close()

    async def get_memories(self, ai, query="", limit=100):
        connection = await self._conn()
        try:
            if query:
                words = [word for word in query.replace("，", " ").replace(",", " ").split() if len(word) > 1]
                clauses = " OR ".join("summary LIKE ? OR key_topics LIKE ?" for _ in words) or "1=0"
                params = [ai] + [value for word in words for value in (f"%{word}%", f"%{word}%")] + [limit]
                rows = await (await connection.execute(f"SELECT * FROM memory_summaries WHERE ai_name=? AND ({clauses}) ORDER BY id DESC LIMIT ?", params)).fetchall()
                if not rows:
                    rows = await (await connection.execute("SELECT * FROM memory_summaries WHERE ai_name=? ORDER BY id DESC LIMIT ?", (ai, limit))).fetchall()
            else:
                rows = await (await connection.execute("SELECT * FROM memory_summaries WHERE ai_name=? ORDER BY id DESC LIMIT ?", (ai, limit))).fetchall()
            return [dict(row) for row in rows]
        finally:
            await connection.close()

    async def set_cookie(self, ai, data):
        connection = await self._conn()
        try:
            await connection.execute("INSERT INTO cookies(ai_name,cookie_json,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(ai_name) DO UPDATE SET cookie_json=excluded.cookie_json,updated_at=CURRENT_TIMESTAMP", (ai, json.dumps(data, ensure_ascii=False)))
            await connection.commit()
        finally:
            await connection.close()

    async def get_cookie(self, ai):
        connection = await self._conn()
        try:
            row = await (await connection.execute("SELECT cookie_json FROM cookies WHERE ai_name=?", (ai,))).fetchone()
            return json.loads(row[0]) if row else []
        finally:
            await connection.close()

    async def export_all(self):
        return {"messages": await self.get_messages(limit=100000), "memories": {ai: await self.get_memories(ai, limit=100000) for ai in ("deepseek", "kimi", "doubao", "gpt")}}
