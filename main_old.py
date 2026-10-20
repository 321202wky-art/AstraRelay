"""AI 群聊记忆系统服务入口。"""
import asyncio, json, logging, socket
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from config import DB_PATH, STATIC_DIR, AI_CONFIG, HOST, PORT, MIGRATION_THRESHOLD, LOG_DIR
from database import Database
from browser_controller import BrowserController
from memory_manager import MemoryManager
from ai_adapter import AIAdapter

handler=RotatingFileHandler(LOG_DIR/'chat.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
logging.basicConfig(level=logging.INFO,handlers=[handler,logging.StreamHandler()],format='%(asctime)s %(levelname)s %(name)s %(message)s'); log=logging.getLogger(__name__)
class Hub:
 def __init__(self): self.clients=set()
 async def broadcast(self,data):
  for ws in list(self.clients):
   try: await ws.send_json(data)
   except Exception: self.clients.discard(ws)
hub=Hub(); db=Database(DB_PATH); browser=BrowserController(db); memory=MemoryManager(db,MIGRATION_THRESHOLD); adapter=AIAdapter(db,browser,memory)
@asynccontextmanager
async def lifespan(app):
 await db.initialize(); yield; await browser.close()
app=FastAPI(title='AI 群聊记忆系统',lifespan=lifespan); app.mount('/static',StaticFiles(directory=STATIC_DIR),name='static')
class ChatRequest(BaseModel): ai_name:str|None=None; message:str; use_memory:bool=True
class Settings(BaseModel): gpt_api_key:str=''; migration_threshold:int|None=None
def local_ip():
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
 try: s.connect(('8.8.8.8',80)); return s.getsockname()[0]
 finally: s.close()
@app.get('/')
async def home(): return FileResponse(STATIC_DIR/'index.html')
@app.post('/api/chat')
async def chat(req:ChatRequest):
 targets=[req.ai_name] if req.ai_name else list(AI_CONFIG)
 targets=[a for a in targets if a in AI_CONFIG]
 if not targets: raise HTTPException(400,'未知 AI')
 await hub.broadcast({'type':'message','message':{'ai_name':'user','role':'user','content':req.message}})
 async def invoke(ai):
  await hub.broadcast({'type':'thinking','ai_name':ai,'value':True})
  try:
   answer,migrated=await adapter.reply(ai,req.message,req.use_memory); payload={'ai_name':ai,'role':'assistant','content':answer}; await hub.broadcast({'type':'message','message':payload})
   if migrated: await hub.broadcast({'type':'notice','content':f"{AI_CONFIG[ai]['label']} 已开启新对话并继承了记忆"})
   return payload
  except Exception as e:
   log.exception('聊天失败'); error={'ai_name':ai,'role':'assistant','content':f'⚠️ {e}'}; await hub.broadcast({'type':'message','message':error}); return error
  finally: await hub.broadcast({'type':'thinking','ai_name':ai,'value':False})
 results=await asyncio.gather(*(invoke(a) for a in targets)); return {'messages':results}
@app.get('/api/history')
async def history(limit:int=300): return await db.get_messages(limit=min(limit,1000))
@app.get('/api/memory/{ai_name}')
async def memories(ai_name:str,query:str=''): return await db.get_memories(ai_name,query)
@app.get('/api/status')
async def status():
 return {'ais':[{**{'name':name},**cfg,'status':('configured' if name=='gpt' and adapter.gpt_key else browser.status.get(name,'stopped')),'rounds':await browser.get_rounds(name) if name!='gpt' else 0,'logged_in':name=='gpt' and bool(adapter.gpt_key) or name in browser.pages} for name,cfg in AI_CONFIG.items()], 'ip':local_ip(), 'threshold':memory.threshold}
@app.post('/api/settings')
async def settings(data:Settings):
 if data.gpt_api_key: await adapter.set_gpt_key(data.gpt_api_key)
 if data.migration_threshold and data.migration_threshold>0: memory.threshold=data.migration_threshold
 return {'ok':True}
@app.post('/api/ai/{ai_name}/start')
async def start_ai(ai_name:str):
 if ai_name not in AI_CONFIG or ai_name=='gpt': raise HTTPException(400,'该 AI 无需浏览器启动')
 await browser.init(ai_name); return {'ok':True,'message':'浏览器已打开，请完成登录后关闭提示页即可'}
@app.get('/api/export')
async def export_data(): return JSONResponse(await db.export_all(),headers={'Content-Disposition':'attachment; filename=ai-memories.json'})
@app.websocket('/ws')
async def websocket(ws:WebSocket):
 await ws.accept(); hub.clients.add(ws)
 try:
  while True: await ws.receive_text()
 except WebSocketDisconnect: hub.clients.discard(ws)
if __name__=='__main__':
 ip=local_ip(); mobile_url=f'http://{ip}:{PORT}'
 print(f'\nAI 群聊记忆系统已启动\n电脑：http://127.0.0.1:{PORT}\n手机：{mobile_url}\n')
 try:
  import qrcode
  qr=qrcode.QRCode(border=1); qr.add_data(mobile_url); qr.make(fit=True); print('手机扫码访问：'); qr.print_ascii(invert=True)
 except Exception: log.warning('二维码生成失败',exc_info=True)
 import uvicorn; uvicorn.run(app,host=HOST,port=PORT)
