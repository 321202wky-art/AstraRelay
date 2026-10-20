import asyncio
from pathlib import Path


def test_database_stores_message_and_active_session(tmp_path: Path):
    from database import Database

    async def scenario():
        db = Database(tmp_path / "chat.db")
        await db.initialize()
        session = await db.create_session("deepseek")
        await db.add_message(session, "deepseek", "user", "你好")
        messages = await db.get_messages(session)
        active = await db.get_active_session("deepseek")
        assert messages[0]["content"] == "你好"
        assert active["session_id"] == session

    asyncio.run(scenario())


def test_memory_context_contains_relevant_summary(tmp_path: Path):
    from database import Database
    from memory_manager import MemoryManager

    async def scenario():
        db = Database(tmp_path / "chat.db")
        await db.initialize()
        await db.save_memory("kimi", "old", "用户喜欢简洁的 Python 答案", "Python,偏好", ["偏好简洁"], {"style": "简洁"}, [])
        manager = MemoryManager(db, migration_threshold=30)
        context = await manager.build_context("kimi", "请继续讲 Python", limit=3)
        assert "用户喜欢简洁" in context
        assert "Python" in context

    asyncio.run(scenario())


def test_browser_init_keeps_login_window_available_when_navigation_times_out(monkeypatch, tmp_path: Path):
    """登录窗口已创建时，页面加载慢不能让接口一直卡住或显示 stopped。"""
    import browser_controller as module
    from browser_controller import BrowserController

    class FakePage:
        def __init__(self): self.front = False
        async def bring_to_front(self): self.front = True
        async def goto(self, *args, **kwargs): raise TimeoutError("Kimi navigation is slow")
    class FakeContext:
        def __init__(self, page): self.pages = [page]
    class FakeChromium:
        def __init__(self, context): self.context = context
        async def launch_persistent_context(self, *args, **kwargs): return self.context
    class FakePlaywright:
        def __init__(self, context): self.chromium = FakeChromium(context)
    class FakeStarter:
        def __init__(self, pw): self.pw = pw
        async def start(self): return self.pw
    class FakeDB:
        async def get_cookie(self, _): return []

    page = FakePage()
    monkeypatch.setattr(module, "async_playwright", lambda: FakeStarter(FakePlaywright(FakeContext(page))))
    controller = BrowserController(FakeDB())
    async def scenario():
        await controller.init("kimi")
        assert controller.pages["kimi"] is page
        assert controller.status["kimi"] == "ready"
        assert page.front is True
    asyncio.run(scenario())


def test_browser_init_reopens_a_window_after_user_closed_the_previous_one(monkeypatch):
    import browser_controller as module
    from browser_controller import BrowserController

    class Page:
        def __init__(self, closed=False): self.closed = closed
        def is_closed(self): return self.closed
        async def bring_to_front(self): pass
        async def goto(self, *args, **kwargs): pass
    class Context:
        def __init__(self, page): self.pages = [page]
    class Chromium:
        def __init__(self, context): self.context = context; self.launches = 0
        async def launch_persistent_context(self, *args, **kwargs): self.launches += 1; return self.context
    class Playwright:
        def __init__(self, chromium): self.chromium = chromium
    class Starter:
        def __init__(self, pw): self.pw = pw
        async def start(self): return self.pw
    class DB:
        async def get_cookie(self, _): return []

    replacement = Page(); chromium = Chromium(Context(replacement))
    monkeypatch.setattr(module, "async_playwright", lambda: Starter(Playwright(chromium)))
    controller = BrowserController(DB()); controller.pages['kimi'] = Page(closed=True); controller.contexts['kimi'] = object()
    async def scenario():
        await controller.init('kimi')
        assert controller.pages['kimi'] is replacement
        assert chromium.launches == 1
    asyncio.run(scenario())


def test_deepseek_has_generic_contenteditable_input_fallback():
    from browser_controller import SELECTORS

    assert "div[contenteditable='true']" in SELECTORS["deepseek"]["input"]


def test_wait_for_new_answer_ignores_old_answer_and_waits_for_stream_to_settle():
    from browser_controller import BrowserController

    class Locator:
        def __init__(self, page): self.page = page
        async def all_text_contents(self):
            value = self.page.states[min(self.page.calls, len(self.page.states) - 1)]
            self.page.calls += 1
            return value
    class Page:
        def __init__(self):
            self.states = [["旧回答"], ["旧回答", "新回答"], ["旧回答", "新回答（完整）"], ["旧回答", "新回答（完整）"], ["旧回答", "新回答（完整）"]]
            self.calls = 0
        def locator(self, _): return Locator(self)

    controller = BrowserController(None)
    async def scenario():
        result = await controller._wait_for_new_answer(Page(), [".answer"], previous_count=1, max_attempts=5, delay=0)
        assert result == "新回答（完整）"
    asyncio.run(scenario())


def test_find_skips_hidden_matching_element():
    from browser_controller import BrowserController

    class Item:
        def __init__(self, visible): self.visible = visible
        async def is_visible(self): return self.visible
        async def count(self): return 1
    class Locator:
        def __init__(self): self.last = Item(False)
        async def all(self): return [Item(False), Item(True)]
        async def count(self): return 2
    class Page:
        def locator(self, _): return Locator()

    async def scenario():
        found = await BrowserController(None)._find(Page(), ["textarea"])
        assert await found.is_visible() is True
    asyncio.run(scenario())


def test_doubao_has_role_textbox_fallback():
    from browser_controller import SELECTORS

    assert "[role='textbox']" in SELECTORS["doubao"]["input"]


def test_doubao_targets_the_current_visible_semi_textarea():
    """新版豆包实际使用 semi-input-textarea，而不是 role=textbox。"""
    from browser_controller import SELECTORS

    assert "textarea.semi-input-textarea" in SELECTORS["doubao"]["input"]


def test_find_waits_for_a_late_rendered_input():
    """单页应用在导航提交后会延迟渲染输入框，不能只检查一次。"""
    from browser_controller import BrowserController

    class Item:
        async def is_visible(self):
            return True

    class Locator:
        def __init__(self, page):
            self.page = page
        async def all(self):
            return [Item()] if self.page.polls >= 2 else []

    class Page:
        def __init__(self):
            self.polls = 0
        def locator(self, _):
            return Locator(self)
        async def wait_for_timeout(self, _):
            self.polls += 1

    async def scenario():
        found = await BrowserController(None)._find(Page(), ["textarea"], timeout_ms=100)
        assert await found.is_visible() is True

    asyncio.run(scenario())


def test_doubao_targets_current_markdown_reply_container():
    """新版豆包已完成的助手回复位于 md-box-root 内。"""
    from browser_controller import SELECTORS

    assert ".md-box-root" in SELECTORS["doubao"]["answer"]


def test_successful_browser_reply_marks_ai_ready():
    """旧错误不应让已成功回复的 AI 仍显示为离线。"""
    from browser_controller import BrowserController

    class Box:
        async def fill(self, _):
            pass
        async def click(self):
            pass

    controller = BrowserController(None)
    controller.status["doubao"] = "error"

    async def init(_):
        controller.pages["doubao"] = object()
    async def find(*_):
        return Box()
    async def answers(*_):
        return []
    async def wait(*_):
        return "ok"

    controller.init = init
    controller._find = find
    controller._answer_texts = answers
    controller._wait_for_new_answer = wait

    async def scenario():
        assert await controller.send_message("doubao", "ping") == "ok"
        assert controller.status["doubao"] == "ready"

    asyncio.run(scenario())


def test_gpt_is_not_an_enabled_group_chat_member():
    """GPT 关闭后，广播消息不得包含它。"""
    from config import AI_CONFIG

    assert "gpt" not in AI_CONFIG
