"""只自动化用户已登录的官方网页；选择器均有回退。"""
import asyncio, logging
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from config import AI_CONFIG, DATA_DIR
log=logging.getLogger(__name__)
SELECTORS={
 "deepseek":{"input":["textarea","[role='textbox'][contenteditable='true']","div[contenteditable='true']","[contenteditable='true']"],"send":["button[type='submit']","button[data-testid*='send']","[data-testid*='send']"],"new":["[data-testid*='new-chat']","[data-testid*='new']","[aria-label*='new']"],"answer":["[data-testid*='message-content']",".ds-markdown","[class*='markdown']"]},
 "kimi":{"input":["textarea","[contenteditable='true']"],"send":["button:has-text('发送')","button[type='submit']"],"new":["button:has-text('新建对话')","[aria-label*='新建']"],"answer":[".markdown","[class*='markdown']"]},
 "doubao":{"input":["textarea.semi-input-textarea","textarea:not([aria-hidden='true'])","[role='textbox']","div[contenteditable]","[contenteditable='true']"],"send":["button[type='submit']","button[data-testid*='send']","[data-testid*='send']"],"new":["[data-testid*='new']","[aria-label*='new']"],"answer":[".md-box-root","[data-testid*='message-content']","[class*='markdown']","[class*='answer']"]}}
class BrowserController:
 def __init__(self, db): self.db=db; self.pw=None; self.contexts={}; self.pages={}; self.status={a:"stopped" for a in SELECTORS}
 async def init(self, ai_name):
  existing=self.pages.get(ai_name)
  if existing and not existing.is_closed():
   # 已打开时仅切回前台；用户关闭窗口后 is_closed() 会触发下面的重建。
   await existing.bring_to_front(); return
  if existing:
   log.info("检测到 %s 的登录窗口已关闭，正在重新创建",ai_name)
   self.pages.pop(ai_name,None); self.contexts.pop(ai_name,None); self.status[ai_name]="stopped"
  self.pw=self.pw or await async_playwright().start()
  context=await self.pw.chromium.launch_persistent_context(str(DATA_DIR/f"browser_{ai_name}"),headless=False,viewport={"width":1280,"height":850})
  cookies=await self.db.get_cookie(ai_name)
  if cookies: await context.add_cookies(cookies)
  page=context.pages[0] if context.pages else await context.new_page()
  # 先登记并置前窗口；个别站点首屏加载很慢，不能让登录入口一直卡住。
  self.contexts[ai_name]=context; self.pages[ai_name]=page; self.status[ai_name]="ready"
  await page.bring_to_front()
  try:
   await page.goto(AI_CONFIG[ai_name]['url'],wait_until="commit",timeout=15_000)
  except Exception as exc:
   log.warning("%s 页面导航未及时完成，但登录窗口已打开：%s",ai_name,exc)
  await page.bring_to_front()
 async def _find(self,page,selectors,timeout_ms=8_000):
  # SPA 会在导航提交后才异步渲染编辑器，短暂轮询而非只检查一次。
  for _ in range(max(1, timeout_ms // 50) + 1):
   for selector in selectors:
    try:
     for loc in reversed(await page.locator(selector).all()):
      if await loc.is_visible(): return loc
    except Exception: pass
   await page.wait_for_timeout(50)
  raise RuntimeError("页面选择器均失效，请在 browser_controller.py 的 SELECTORS 中更新")
 async def _answer_texts(self,page,selectors):
  """Use only the first populated answer container to avoid duplicate fallback matches."""
  for selector in selectors:
   try:
    texts=[text.strip() for text in await page.locator(selector).all_text_contents() if text.strip()]
    if texts: return texts
   except Exception: pass
  return []
 async def _wait_for_new_answer(self,page,selectors,previous_count,max_attempts=30,delay=1):
  """Wait for a new answer and for two observations of the same streamed text."""
  last_text=""; stable_rounds=0
  for _ in range(max_attempts):
   answers=await self._answer_texts(page,selectors)
   if len(answers)>previous_count:
    current=answers[-1]
    if current==last_text:
     stable_rounds+=1
     if stable_rounds>=1: return current
    else:
     last_text=current; stable_rounds=0
   await asyncio.sleep(delay)
  raise TimeoutError("AI response did not finish within 30 seconds")
 async def send_message(self,ai_name,message):
  await self.init(ai_name); page=self.pages[ai_name]; cfg=SELECTORS[ai_name]
  try:
   box=await self._find(page,cfg['input']); await box.fill(message)
   try: await (await self._find(page,cfg['send'])).click()
   except RuntimeError: await box.press("Enter")
   before=len(await self._answer_texts(page,cfg['answer']))
   answer=await self._wait_for_new_answer(page,cfg['answer'],before)
   self.status[ai_name]="ready"
   return answer
   raise TimeoutError("AI 在30秒内未完成回复")
  except Exception as exc: self.status[ai_name]="error"; log.exception("%s 回复失败",ai_name); raise exc
 async def new_conversation(self,ai_name):
  await self.init(ai_name); await (await self._find(self.pages[ai_name],SELECTORS[ai_name]['new'])).click(); await self.pages[ai_name].wait_for_timeout(800)
 async def get_rounds(self,ai_name):
  active=await self.db.get_active_session(ai_name); return (active or {}).get('message_count',0)//2
 async def save_cookies(self,ai_name):
  if ai_name in self.contexts: await self.db.set_cookie(ai_name,await self.contexts[ai_name].cookies())
 async def close(self):
  for ai,ctx in list(self.contexts.items()): await self.save_cookies(ai); await ctx.close()
  if self.pw: await self.pw.stop()
