import asyncio
import logging

from pydantic_ai.exceptions import AgentRunError

from .config import ROOT, Config
from .documents import Documents
from .logging_setup import setup_logging
from .runtime import create_agent, run_turn
from .storage import Session, Store

HELP = "/new 新会话 | /sessions 历史列表 | /load <会话ID> 加载 | /exit 退出"


def main() -> int:
    try:
        config = Config.load()
        logfile = setup_logging(ROOT / "logs", config.api_key)
        store = Store(ROOT / "data" / "runtime")
        identity = store.identity()
        agent = create_agent(config, str(identity.user_id), Documents(ROOT / "data" / "fixtures"))
        session = Session(model=config.model)
        print(f"模型：{config.model}（思考模式关闭）\n日志：{logfile}\n{HELP}")
        # 单个事件循环供 SDK 连接池和所有对话轮次复用。
        with asyncio.Runner() as runner:
            while True:
                try:
                    print(f"\n当前会话：{session.id}")
                    prompt = input("你：").strip()
                    if not prompt:
                        continue
                    if prompt == "/exit":
                        break
                    if prompt == "/new":
                        session = Session(model=config.model)
                        continue
                    if prompt == "/sessions":
                        for saved in store.sessions():
                            print(f"{saved.id}  {saved.updated_at.isoformat()}  {saved.model}")
                        continue
                    if prompt.startswith("/load "):
                        loaded = store.load(prompt.removeprefix("/load ").strip())
                        if loaded.model != config.model:
                            print("历史模型与当前配置不同；跨模型历史尚未验证，请先确认兼容方案。")
                            continue
                        session = loaded
                        continue
                    if prompt.startswith("/"):
                        print(HELP)
                        continue
                    print("助手：", end="", flush=True)
                    runner.run(run_turn(
                        agent, session, prompt, store,
                        lambda text: print(text, end="", flush=True),
                    ))
                    print()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    logging.getLogger(__name__).warning("session=%s interrupted", session.id)
                    print("\n已中断。本轮未完成的内容不保存；此前成功历史保留。/exit 可退出。")
                except (AgentRunError, OSError, ValueError) as exc:
                    # 不打印 SDK 错误正文、请求体或 traceback，避免把凭据带入日志。
                    status = getattr(exc, "status_code", None)
                    logging.getLogger(__name__).error(
                        "session=%s failed type=%s status=%s", session.id, type(exc).__name__, status,
                    )
                    print(f"\n操作失败：{type(exc).__name__}（HTTP 状态：{status}）。"
                          "未完成的本轮不保存，此前历史保留。")
        return 0
    except (ValueError, OSError) as exc:
        # 启动错误只输出类型，不回显可能带敏感值的配置/校验错误。
        print(f"启动失败：{type(exc).__name__}。请检查 .env 密钥配置和本地身份文件。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
