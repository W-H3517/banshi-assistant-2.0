"""显式运行的真实 API 验证，会产生模型调用费用。"""

import asyncio
import json
from datetime import UTC, datetime

import httpx2 as httpx
from pydantic_ai.messages import ToolCallPart

from .config import ROOT, Config
from .documents import Documents
from .logging_setup import setup_logging
from .runtime import create_agent, run_turn
from .storage import Session, Store


async def verify() -> None:
    config = Config.load()
    setup_logging(ROOT / "logs", config.api_key)
    store = Store(ROOT / "data/runtime")
    user_id = str(store.identity().user_id)
    requests = []

    async def check_request(request):
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            assert body["user_id"] == user_id
            assert body["thinking"] == {"type": "disabled"}
            assert body["model"] == config.model
            # 只保存无敏感信息的验证结果，不保存请求头和完整请求体。
            requests.append({"user_id_matches": True, "thinking_disabled": True})

    report = {"time": datetime.now(UTC).isoformat(), "model": config.model, "cases": []}
    async with httpx.AsyncClient(event_hooks={"request": [check_request]}) as client:
        agent = create_agent(config, user_id, Documents(ROOT / "data/fixtures"), http_client=client)
        session = Session(model=config.model)
        cases = [
            ("direct", "你好，请记住本次测试代号是验证7319，简短回复即可。"),
            ("tools", "我去外地参加培训，回来需要哪些报销材料，住宿限额是多少？请查全相关制度。"),
            ("reload", "我刚才告诉你的测试代号是什么？"),
            ("missing", "模拟制度里有火星探险补贴吗？请查询后答复。"),
        ]
        for name, prompt in cases:
            if name == "reload":
                session = Store(ROOT / "data/runtime").load(str(session.id))
                assert str(Store(ROOT / "data/runtime").identity().user_id) == user_id
            chunks = []
            result = await run_turn(agent, session, prompt, store, chunks.append)
            tools = [
                {"name": part.tool_name, "arguments": part.args_as_dict()}
                for message in result.new_messages()
                for part in message.parts if isinstance(part, ToolCallPart)
            ]
            report["cases"].append({
                "name": name, "output": result.output, "tools": tools,
                "stream_events": len(chunks),
            })
            print(f"{name}: completed; tools={len(tools)}; stream_events={len(chunks)}", flush=True)
    report["requests"] = requests
    report["session_id"] = str(session.id)
    path = ROOT / "data/runtime/live-verification.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"验证结果：{path}")


if __name__ == "__main__":
    asyncio.run(verify())
