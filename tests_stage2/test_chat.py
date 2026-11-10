import asyncio
import json
from types import SimpleNamespace

import httpx2
import pytest
from fastapi import HTTPException
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from sqlalchemy import event, select, update

from backend.agent import create_agent
from backend.chat import ChatService, owned
from backend.config import Settings
from backend.db import Conversation, Database, User
from tests_stage2.test_app import sse, tool


async def wait(awaitable):
    return await asyncio.wait_for(awaitable, timeout=10)


async def drain(stream):
    return [frame async for frame in stream]


async def slow_run(*args, **kwargs):
    await asyncio.Event().wait()


async def answer(*args, **kwargs):
    messages = [*kwargs["message_history"], ModelResponse(parts=[TextPart("完成")])]
    return SimpleNamespace(output="完成", all_messages_json=lambda:
                           ModelMessagesTypeAdapter.dump_json(messages))


@pytest.fixture
async def chat(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="test-key")
    db = Database(settings)
    await db.initialize()
    async with db.sessions.begin() as session:
        user = await session.scalar(select(User).where(User.username == "alice"))
        conversation = Conversation(user_id=user.id, model=settings.model)
        session.add(conversation)
    service = ChatService(db, settings, lambda *args: SimpleNamespace(run=slow_run))
    try:
        yield SimpleNamespace(db=db, settings=settings, user=user,
                              conversation=conversation, service=service)
    finally:
        await wait(service.close())
        await db.engine.dispose()


async def change(chat, **values):
    async with chat.db.sessions.begin() as session:
        await session.execute(update(Conversation).where(
            Conversation.id == chat.conversation.id,
        ).values(**values))


async def current(chat):
    return await owned(chat.db, chat.conversation.id, chat.user.id)


async def test_atomic_reservation_across_independent_sessions(chat):
    # 不经过本地启动锁，直接用 20 个服务实例争抢同一 SQLite 行。
    contenders = [ChatService(chat.db, chat.settings) for _ in range(20)]
    results = await wait(asyncio.gather(*[
        service.reserve(chat.user, chat.conversation.id, "问题", {})
        for service in contenders
    ], return_exceptions=True))
    assert sum(isinstance(result, Conversation) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409
               for result in results) == 19
    assert (await current(chat)).status == "running"


async def test_service_allows_only_one_active_turn_per_conversation(chat):
    results = await wait(asyncio.gather(*[
        chat.service.start(chat.user, chat.conversation.id, "问题", {}) for _ in range(10)
    ], return_exceptions=True))
    streams = [result for result in results if not isinstance(result, Exception)]
    assert len(streams) == 1
    assert all(result.status_code == 409 for result in results if isinstance(result, Exception))
    await wait(chat.service.stop(chat.conversation.id))
    await wait(drain(streams[0]))
    assert (await current(chat)).status == "interrupted"
    assert not chat.service.tasks


async def test_reservation_reads_latest_history_after_update(chat, monkeypatch):
    stale = await current(chat)
    completed = [{"role": "assistant", "text": "另一轮刚保存的回答"}]
    history = json.loads(ModelMessagesTypeAdapter.dump_json([
        ModelResponse(parts=[TextPart(completed[0]["text"])])]))
    await change(chat, transcript=completed, history=history, updated_at=stale.updated_at)

    async def stale_read(*args):
        return stale

    monkeypatch.setattr("backend.chat.owned", stale_read)
    statements = []

    @event.listens_for(chat.db.engine.sync_engine, "before_cursor_execute")
    def capture(connection, cursor, statement, parameters, context, executemany):
        if "conversations" in statement:
            statements.append(statement.lstrip().split()[0])

    async def inspect_history(*args, **kwargs):
        assert json.loads(ModelMessagesTypeAdapter.dump_json(kwargs["message_history"])) == history
        return await answer(*args, **kwargs)

    chat.service.agent_factory = lambda *args: SimpleNamespace(run=inspect_history)
    stream = await chat.service.start(chat.user, chat.conversation.id, "继续", {})
    frames = await wait(drain(stream))
    assert any('"type": "done"' in frame for frame in frames)
    saved = await current(chat)
    assert statements[:2] == ["UPDATE", "SELECT"]
    assert saved.transcript[0] == completed[0]
    assert saved.history[0] == history[0]
    assert saved.status == "idle"


@pytest.mark.parametrize("case,expected", [
    ("blank_prompt", 422), ("unexpected_answers", 422), ("missing_answer", 422),
    ("blank_answer", 422), ("extra_answer", 422), ("bypass_question", 422),
    ("wrong_model", 409), ("missing_key", 503),
])
async def test_invalid_input_rolls_back_reservation(chat, case, expected):
    pending = [{"id": "q1", "question": "问题一"}, {"id": "q2", "question": "问题二"}]
    values = {"status": "failed", "error": "保留原错误"}
    prompt, answers = "问题", {}
    if case in {"missing_answer", "blank_answer", "extra_answer", "bypass_question"}:
        values.update(status="waiting", pending=pending, requests=3)
        prompt, answers = "", {"q1": "是", "q2": "否"}
        if case == "missing_answer":
            answers.pop("q2")
        elif case == "blank_answer":
            answers["q2"] = "  "
        elif case == "extra_answer":
            answers["fake"] = "是"
        else:
            prompt = "跳过问题"
    elif case == "blank_prompt":
        prompt = "  "
    elif case == "unexpected_answers":
        answers = {"q1": "是"}
    elif case == "wrong_model":
        values["model"] = "other-model"
    else:
        chat.service.settings = Settings(data_dir=chat.settings.data_dir, api_key="")
    await change(chat, **values)
    with pytest.raises(HTTPException) as caught:
        await chat.service.start(chat.user, chat.conversation.id, prompt, answers)
    assert caught.value.status_code == expected
    saved = await current(chat)
    assert all(getattr(saved, key) == value for key, value in values.items())
    assert not chat.service.tasks


@pytest.mark.parametrize("status", ["idle", "running", "waiting"])
async def test_reservation_preserves_user_isolation(chat, status):
    await change(chat, status=status)
    with pytest.raises(HTTPException) as caught:
        await chat.service.start(SimpleNamespace(id="another-user"),
                                 chat.conversation.id, "问题", {})
    assert caught.value.status_code == 404
    assert (await current(chat)).status == status


@pytest.mark.parametrize("action", ["stop", "disconnect"])
async def test_stop_or_disconnect_during_startup_releases_reservation(chat, monkeypatch, action):
    committed, release = asyncio.Event(), asyncio.Event()
    reserve = chat.service.reserve

    async def delayed_reserve(*args):
        conversation = await reserve(*args)
        committed.set()
        await release.wait()
        return conversation

    monkeypatch.setattr(chat.service, "reserve", delayed_reserve)
    starting = asyncio.create_task(chat.service.start(chat.user, chat.conversation.id, "问题", {}))
    await wait(committed.wait())
    assert (await current(chat)).status == "running"
    if action == "stop":
        stopping = asyncio.create_task(chat.service.stop(chat.conversation.id))
        await asyncio.sleep(0)
        assert not stopping.done()
    else:
        starting.cancel()
    release.set()
    if action == "stop":
        stream = await wait(starting)
        await wait(stopping)
        await wait(drain(stream))
    else:
        with pytest.raises(asyncio.CancelledError):
            await wait(starting)
    assert (await current(chat)).status == "interrupted"
    assert not chat.service.tasks


async def test_worker_creation_failure_releases_reservation(chat, monkeypatch):
    create_task = asyncio.create_task

    def fail_worker(coroutine, **kwargs):
        if coroutine.cr_code.co_name == "run":
            raise RuntimeError("cannot start worker")
        return create_task(coroutine, **kwargs)

    monkeypatch.setattr(asyncio, "create_task", fail_worker)
    with pytest.raises(RuntimeError, match="cannot start worker"):
        await chat.service.start(chat.user, chat.conversation.id, "问题", {})
    saved = await current(chat)
    assert saved.status == "failed" and not saved.history
    assert not chat.service.tasks


async def test_stream_disconnect_finishes_cleanup_before_retry(chat, monkeypatch):
    writing, release, reading = asyncio.Event(), asyncio.Event(), asyncio.Event()
    write_result = chat.service.write_result

    async def delayed_write(*args):
        writing.set()
        await release.wait()
        await write_result(*args)

    monkeypatch.setattr(chat.service, "write_result", delayed_write)
    stream = await chat.service.start(chat.user, chat.conversation.id, "问题", {})

    async def consume():
        reading.set()
        await drain(stream)

    consumer = asyncio.create_task(consume())
    await wait(reading.wait())
    consumer.cancel()
    try:
        await wait(writing.wait())
        assert not consumer.done()
        with pytest.raises(HTTPException) as caught:
            await chat.service.start(chat.user, chat.conversation.id, "重发", {})
        assert caught.value.status_code == 409
        stopping = asyncio.create_task(chat.service.stop(chat.conversation.id))
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await wait(consumer)
    await wait(stopping)
    assert (await current(chat)).status == "interrupted"
    assert not chat.service.tasks


async def test_closing_old_stream_does_not_cancel_new_turn(chat):
    chat.service.agent_factory = lambda *args: SimpleNamespace(run=answer)
    old_stream = await chat.service.start(chat.user, chat.conversation.id, "第一轮", {})
    await wait(asyncio.gather(*list(chat.service.tasks.values())))
    chat.service.agent_factory = lambda *args: SimpleNamespace(run=slow_run)
    new_stream = await chat.service.start(chat.user, chat.conversation.id, "第二轮", {})
    active = chat.service.tasks[chat.conversation.id]
    await wait(drain(old_stream))
    assert chat.service.tasks[chat.conversation.id] is active
    assert not active.done() and (await current(chat)).status == "running"
    await wait(chat.service.stop(chat.conversation.id))
    await wait(drain(new_stream))
    assert (await current(chat)).transcript[-1]["text"] == "完成"


@pytest.mark.parametrize("outcome", ["success", "failure", "stop"])
@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
async def test_finalization_survives_repeated_stop_and_blocks_next_turn(
    chat, monkeypatch, outcome, boundary,
):
    writing, release = asyncio.Event(), asyncio.Event()
    write_result = chat.service.write_result

    async def delayed_write(conversation_id, values):
        if conversation_id != chat.conversation.id:
            return await write_result(conversation_id, values)
        if boundary == "after_commit":
            await write_result(conversation_id, values)
        writing.set()
        await release.wait()
        if boundary == "before_commit":
            await write_result(conversation_id, values)

    async def fail_run(*args, **kwargs):
        raise RuntimeError("test failure")

    monkeypatch.setattr(chat.service, "write_result", delayed_write)
    run = {"success": answer, "failure": fail_run, "stop": slow_run}[outcome]
    chat.service.agent_factory = lambda *args: SimpleNamespace(run=run)
    stream = await chat.service.start(chat.user, chat.conversation.id, "问题", {})
    stops = []
    if outcome == "stop":
        stops.append(asyncio.create_task(chat.service.stop(chat.conversation.id)))
    try:
        await wait(writing.wait())
        with pytest.raises(HTTPException) as caught:
            await chat.service.start(chat.user, chat.conversation.id, "不能提前发送", {})
        assert caught.value.status_code == 409
        stops.extend(asyncio.create_task(chat.service.stop(chat.conversation.id)) for _ in range(3))
        # 收尾不会占住其他会话；它们仍可启动和停止。
        async with chat.db.sessions.begin() as session:
            other = Conversation(user_id=chat.user.id, model=chat.settings.model)
            session.add(other)
        chat.service.agent_factory = lambda *args: SimpleNamespace(run=slow_run)
        other_stream = await wait(chat.service.start(chat.user, other.id, "独立问题", {}))
        await wait(chat.service.stop(other.id))
        await wait(drain(other_stream))
    finally:
        release.set()
    await wait(asyncio.gather(*stops))
    frames = await wait(drain(stream))
    saved = await current(chat)
    assert saved.status == {"success": "idle", "failure": "failed", "stop": "interrupted"}[outcome]
    assert bool(saved.history) == (outcome == "success")
    assert any(('"type": "done"' if outcome == "success" else '"type": "error"') in frame
               for frame in frames)
    assert not chat.service.tasks
    # 确认收尾完成后真正可续聊，而不只是返回了正确的状态码。
    chat.service.agent_factory = lambda *args: SimpleNamespace(run=answer)
    stream = await chat.service.start(chat.user, chat.conversation.id, "下一轮", {})
    await wait(drain(stream))
    assert (await current(chat)).status == "idle"


async def test_question_restart_stop_and_retry_preserve_pending(chat):
    pending = [{"id": "q1", "question": "是否外地？"}]
    await change(chat, status="running", pending=pending, requests=2)
    await chat.db.initialize()
    assert (await current(chat)).status == "interrupted"
    stream = await chat.service.start(chat.user, chat.conversation.id, "", {"q1": "是"})
    assert (await current(chat)).status == "running"
    await wait(chat.service.stop(chat.conversation.id))
    await wait(drain(stream))
    saved = await current(chat)
    assert saved.status == "waiting" and saved.pending == pending and saved.requests == 2
    assert saved.history == [] and saved.transcript == []
    chat.service.agent_factory = lambda *args: SimpleNamespace(run=answer)
    stream = await chat.service.start(chat.user, chat.conversation.id, "", {"q1": "是"})
    await wait(drain(stream))
    saved = await current(chat)
    assert saved.status == "idle" and not saved.pending and saved.requests == 0


async def test_search_question_resume_search_remain_reserved(chat):
    searching, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def search(*args):
        searching.set()
        await release.wait()
        return {"items": [], "rule_cards": []}

    chat.db.knowledge = SimpleNamespace(search_bundle=search)

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) in {1, 3}:
            return tool("search_documents", {"query": "培训"}, f"search{len(requests)}")
        if len(requests) == 2:
            return tool("ask_question", {"question": "是否外地？"}, "ask1")
        return sse({"role": "assistant", "content": "完成"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = OpenAIChatModel(chat.settings.model, provider=DeepSeekProvider(
            api_key="test-key", http_client=client))
        chat.service.agent_factory = lambda settings, user_id, db: create_agent(
            settings, user_id, db, model=model)
        for prompt, answers in [("培训报销", {}), ("", {"ask1": "是"})]:
            searching.clear()
            release.clear()
            stream = await chat.service.start(chat.user, chat.conversation.id, prompt, answers)
            try:
                await wait(searching.wait())
                assert (await current(chat)).status == "running"
                with pytest.raises(HTTPException) as caught:
                    await chat.service.start(chat.user, chat.conversation.id, prompt, answers)
                assert caught.value.status_code == 409
            finally:
                release.set()
            await wait(drain(stream))
            saved = await current(chat)
            assert saved.status == ("idle" if answers else "waiting")
            assert bool(saved.pending) != bool(answers)
            assert not chat.service.tasks
        assert len(requests) == 4
        assert any(message.get("tool_call_id") == "ask1" and message["content"] == "是"
                   for message in requests[2]["messages"])
        assert (await current(chat)).transcript[-1]["text"] == "完成"
