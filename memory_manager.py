"""会话摘要、检索与迁移。摘要可由适配器注入的模型函数生成。"""
import json, re
class MemoryManager:
    def __init__(self, db, migration_threshold=30): self.db=db; self.threshold=migration_threshold
    def structure(self, summary):
        words=[w for w in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}",summary) if w not in {"用户","对话","重要","问题"}]
        return {"key_topics": ",".join(dict.fromkeys(words[:8])), "important_facts": [], "user_preferences": {}, "unresolved_issues": []}
    async def get_relevant_memories(self, ai_name, query, limit=3): return await self.db.get_memories(ai_name,query,limit)
    async def build_context(self, ai_name, current_message, limit=3):
        memories=await self.get_relevant_memories(ai_name,current_message,limit)
        if not memories: return ""
        blocks=[f"摘要：{m['summary']}\n关键词：{m['key_topics']}" for m in memories]
        return "以下是长期记忆，仅在相关时使用：\n"+"\n---\n".join(blocks)
    async def generate_summary(self, ai_name, session_id, summarizer=None):
        messages=await self.db.session_messages(session_id)
        transcript="\n".join(f"{m['role']}：{m['content']}" for m in messages)
        prompt="请用300字以内总结我们刚才的全部对话。必须包含：1.关键事实 2.用户偏好 3.未解决的问题 4.重要数据。\n\n"+transcript
        return (await summarizer(prompt))[:300] if summarizer else transcript[-280:]
    async def check_and_migrate(self, ai_name, summarizer, new_conversation, inherit):
        active=await self.db.get_active_session(ai_name)
        if not active or active['message_count'] < self.threshold*2: return None
        old=active['session_id']; summary=await self.generate_summary(ai_name,old,summarizer); meta=self.structure(summary)
        await self.db.save_memory(ai_name,old,summary,meta['key_topics'],meta['important_facts'],meta['user_preferences'],meta['unresolved_issues'])
        await new_conversation(); new=await self.db.create_session(ai_name)
        inheritance="这是上一段对话的记忆摘要，请继承并在后续对话中参考：\n"+summary
        await inherit(inheritance); await self.db.add_message(new,ai_name,"assistant",inheritance)
        return {"ai_name":ai_name,"summary":summary,"session_id":new}
