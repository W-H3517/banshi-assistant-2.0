import asyncio
import json
import logging
from contextlib import suppress
from copy import deepcopy

from fastapi import HTTPException
from pydantic_ai import DeferredToolRequests, DeferredToolResults
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessagesTypeAdapter,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.usage import RunUsage, UsageLimits
from sqlalchemy import select, update

from .agent import create_agent
from .db import Conversation, now

logger = logging.getLogger(__name__)


async def owned(db, conversation_id, user_id):
    async with db.sessions() as session:
        conversation = await session.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id,
        ))
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    return conversation


def detail(conversation):
    return {key: getattr(conversation, key) for key in (
        "id", "title", "updated_at", "status", "transcript", "pending", "error",
    )}


class ChatService:
    def __init__(self, db, settings, agent_factory=create_agent, instrumentation=None):
        self.db, self.settings, self.agent_factory = db, settings, agent_factory
        self.tasks = {}
        self.instrumentation = instrumentation

    async def start(self, user, conversation_id, prompt, answers):
        conversation = await owned(self.db, conversation_id, user.id)
        if conversation.status == "running":
            raise HTTPException(409, "当前会话正在生成")
        if conversation.model != self.settings.model:
            raise HTTPException(409, "会话模型与配置不同，请新建会话")
        if conversation.pending:
            if prompt or set(answers) != {item["id"] for item in conversation.pending}:
                raise HTTPException(422, "请回答当前全部问题后继续")
            if not all(answer.strip() for answer in answers.values()):
                raise HTTPException(422, "回答不能为空")
        elif not prompt.strip() or answers:
            raise HTTPException(422, "请输入消息；当前没有待回答的工具问题")
        if not self.settings.api_key:
            raise HTTPException(503, "模型密钥尚未配置，资料库仍可使用")
        async with self.db.sessions() as session:
            reserved = await session.execute(update(Conversation).where(
                Conversation.id == conversation_id, Conversation.user_id == user.id,
                Conversation.status == conversation.status,
                Conversation.updated_at == conversation.updated_at,
            ).values(status="running", error=""))
            await session.commit()
            if reserved.rowcount != 1:
                raise HTTPException(409, "会话状态已更新，请刷新后重试")
        queue = asyncio.Queue()
        task = asyncio.create_task(self.run(user, conversation, prompt, answers, queue))
        self.tasks[conversation_id] = task
        return self.stream(conversation_id, task, queue)

    async def stream(self, conversation_id, task, queue):
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            if self.tasks.get(conversation_id) is task:
                self.tasks.pop(conversation_id)

    async def run(self, user, conversation, prompt, answers, queue):
        transcript = deepcopy(conversation.transcript)
        assistant = {"role": "assistant", "text": "", "tools": [], "sources": []}
        if answers:
            for message in transcript:
                for tool in message.get("tools", []):
                    if tool["id"] in answers:
                        tool["status"] = "done"
            answer_text = "\n".join(
                f"{item['question']}：{answers[item['id']]}" for item in conversation.pending
            )
            transcript.append({"role": "user", "text": answer_text})
        else:
            transcript.append({"role": "user", "text": prompt})

        async def events(ctx, stream):
            async for event in stream:
                text = None
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    text = event.part.content
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    text = event.delta.content_delta
                elif isinstance(event, FunctionToolCallEvent):
                    tool = {"id": event.tool_call_id, "name": event.part.tool_name,
                            "status": "running"}
                    assistant["tools"].append(tool)
                    queue.put_nowait({"type": "tool", **tool})
                elif isinstance(event, FunctionToolResultEvent):
                    for tool in assistant["tools"]:
                        if tool["id"] == event.tool_call_id:
                            tool["status"] = "done"
                            queue.put_nowait({"type": "tool", **tool})
                    if event.part.tool_name == "search_documents":
                        content = event.part.content
                        if isinstance(content, list):
                            for doc in content:
                                source = {"id": doc["id"], "title": doc["title"]}
                                if source not in assistant["sources"]:
                                    assistant["sources"].append(source)
                                    queue.put_nowait({"type": "source", **source})
                if text:
                    assistant["text"] += text
                    queue.put_nowait({"type": "delta", "text": text})

        try:
            agent = self.agent_factory(self.settings, user.id, self.db)
            usage = RunUsage(requests=conversation.requests if answers else 0)
            result = await agent.run(
                None if answers else prompt,
                message_history=ModelMessagesTypeAdapter.validate_python(conversation.history),
                deferred_tool_results=DeferredToolResults(calls=answers) if answers else None,
                event_stream_handler=events, usage=usage, usage_limits=UsageLimits(request_limit=8),
                conversation_id=conversation.id,
                metadata={"conversation_id": conversation.id, "user_id": user.id},
                capabilities=[Instrumentation(settings=self.instrumentation)]
                if self.instrumentation else [],
            )
            pending = []
            if isinstance(result.output, DeferredToolRequests):
                pending = [{"id": call.tool_call_id, **call.args_as_dict()}
                           for call in result.output.calls]
                for tool in assistant["tools"]:
                    if tool["name"] == "ask_question":
                        tool["status"] = "waiting"
            elif not assistant["text"]:
                assistant["text"] = result.output
            transcript.append(assistant)
            async with self.db.sessions() as session:
                await session.execute(update(Conversation).where(
                    Conversation.id == conversation.id,
                ).values(
                    history=json.loads(result.all_messages_json()), transcript=transcript,
                    pending=pending, status="waiting" if pending else "idle", error="",
                    requests=usage.requests if pending else 0, updated_at=now(),
                    title=prompt[:30] if conversation.title == "新对话" else conversation.title,
                ))
                await session.commit()
            queue.put_nowait({"type": "done"})
            logger.info("conversation=%s completed requests=%s", conversation.id, usage.requests)
        except asyncio.CancelledError:
            await self.fail(conversation, "已停止生成，未完成内容未写入历史。", "interrupted")
            queue.put_nowait({"type": "error", "message": "已停止生成"})
        except Exception as exc:  # noqa: BLE001 -- SSE 边界必须落盘失败状态，不回显敏感异常正文。
            logger.error("conversation=%s failed type=%s", conversation.id, type(exc).__name__)
            message = f"生成失败（{type(exc).__name__}），已保存的历史保持不变。"
            await self.fail(conversation, message, "failed")
            queue.put_nowait({"type": "error", "message": message})
        finally:
            queue.put_nowait(None)

    async def fail(self, conversation, message, status):
        async with self.db.sessions() as session:
            await session.execute(update(Conversation).where(
                Conversation.id == conversation.id,
            ).values(status="waiting" if conversation.pending else status, error=message))
            await session.commit()

    async def stop(self, conversation_id):
        task = self.tasks.get(conversation_id)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def close(self):
        for conversation_id in list(self.tasks):
            await self.stop(conversation_id)
