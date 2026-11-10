"""模型协议适配；业务工具与提示词在不同供应商之间共用。"""

from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider


def create_model(settings, *, http_client=None):
    if settings.provider == "deepseek":
        provider = DeepSeekProvider(api_key=settings.api_key, http_client=http_client)
        return OpenAIChatModel(settings.model, provider=provider)
    provider = OpenAIProvider(api_key=settings.api_key, base_url="https://api.openai.com/v1", http_client=http_client)
    model_type = OpenAIResponsesModel if settings.openai_api == "responses" else OpenAIChatModel
    return model_type(settings.model, provider=provider)


def model_settings(settings, user_id):
    common = {"timeout": 120, "max_retries": 0}
    if settings.provider == "deepseek":
        return {**common, "extra_body": {"user_id": user_id, "thinking": {"type": "disabled"}}}
    result = {**common, "openai_store": False}
    if settings.reasoning_effort is not None:
        result["openai_reasoning_effort"] = settings.reasoning_effort
    return result
