"""模型协议适配；业务工具与提示词在不同供应商之间共用。"""

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider


def create_model(settings, *, http_client=None):
    if settings.provider == "deepseek":
        provider = DeepSeekProvider(api_key=settings.api_key, http_client=http_client)
        return OpenAIChatModel(settings.model, provider=provider)
    if settings.provider == "ollama":
        # 本机 Ollama 的 OpenAI 兼容接口；不需要密钥。
        provider = OllamaProvider(base_url=settings.ollama_base_url, http_client=http_client)
        return OpenAIChatModel(settings.model, provider=provider)
    provider = OpenAIProvider(api_key=settings.api_key, base_url="https://api.openai.com/v1", http_client=http_client)
    model_type = OpenAIResponsesModel if settings.openai_api == "responses" else OpenAIChatModel
    return model_type(settings.model, provider=provider)


def model_settings(settings, user_id):
    common = {"timeout": 120, "max_retries": 0}
    if settings.provider == "deepseek":
        return {**common, "extra_body": {"user_id": user_id, "thinking": {"type": "disabled"}}}
    if settings.provider == "ollama":
        # Ollama 兼容接口用 reasoning_effort=none 关闭思考（think 字段在该接口不生效）。
        return {**common, "openai_reasoning_effort": "none"}
    result = {**common, "openai_store": False}
    if settings.reasoning_effort is not None:
        result["openai_reasoning_effort"] = settings.reasoning_effort
    return result
