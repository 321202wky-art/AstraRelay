"""鍥涗釜妯″瀷鐨勭粺涓€璋冪敤鍏ュ彛锛孏PT Key 浠呬繚瀛樹簬杩涚▼鍐呫€?"""
import os
from openai import AsyncOpenAI
class AIAdapter:
 def __init__(self,db,browser,memory): self.db=db; self.browser=browser; self.memory=memory; self.gpt_key=os.getenv('OPENAI_API_KEY','')
 async def set_gpt_key(self,key): self.gpt_key=key.strip()
 async def _gpt(self,prompt):
  if not self.gpt_key: raise RuntimeError('璇峰厛鍦ㄨ缃腑濉啓 GPT API Key')
  client=AsyncOpenAI(api_key=self.gpt_key); result=await client.chat.completions.create(model='gpt-4o-mini',messages=[{'role':'user','content':prompt}]); return result.choices[0].message.content
 async def _raw(self,ai,prompt): return await self._gpt(prompt) if ai=='gpt' else await self.browser.send_message(ai,prompt)
 async def reply(self,ai,message,use_memory=True):
  sid=await self.db.ensure_session(ai); context=await self.memory.build_context(ai,message) if use_memory else ''
  prompt=(context+'\n\n褰撳墠鐢ㄦ埛娑堟伅锛?+message) if context else message
  await self.db.add_message(sid,ai,'user',message)
  last_error=None
  for _ in range(2):
   try: answer=await self._raw(ai,prompt); break
   except Exception as exc: last_error=exc
  else: raise last_error
  await self.db.add_message(sid,ai,'assistant',answer)
  async def summarize(p): return await self._raw(ai,p)
  async def fresh():
   if ai!='gpt': await self.browser.new_conversation(ai)
  async def inherit(p):
   if ai!='gpt': await self._raw(ai,p)
  migrated=await self.memory.check_and_migrate(ai,summarize,fresh,inherit)
  return answer,migrated
