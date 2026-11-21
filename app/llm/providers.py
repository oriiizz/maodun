"""模型只负责把已经选中的证据说成口头回答。没有证据时不会调用这里。"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from app.config import Settings
from app.llm.errors import ModelCallError, notice_for_http


class ChatModel(Protocol):
    provider_id: str

    def configured(self) -> bool:
        ...

    def missing_notice(self) -> str:
        ...

    def complete(self, messages: list[dict[str, str]]) -> str:
        ...


def _message_text(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict) and (error.get("message") or error.get("code")):
        raise ModelCallError(f"模型服务返回错误：{error.get('message') or error.get('code')}。本次没有生成人物回答。")
    if payload.get("code") and payload.get("message") and "choices" not in payload:
        raise ModelCallError(f"模型服务返回错误：{payload.get('message')}。本次没有生成人物回答。")
    choices = payload.get("choices") or []
    if not choices:
        raise ModelCallError("模型没有返回可用内容。本次没有生成人物回答。")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict):
                texts.append(str(part.get("text") or ""))
            else:
                texts.append(str(part))
        content = "".join(texts)
    if not isinstance(content, str) or not content.strip():
        raise ModelCallError("模型返回了空内容。本次没有生成人物回答。")
    return content.strip()


class _HttpChat:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.request_timeout_seconds)

    def _post(self, url: str, api_key: str, payload: dict[str, Any], service: str) -> str:
        try:
            response = self.client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ModelCallError(
                f"调用{service}超时（超过 {self.settings.request_timeout_seconds:g} 秒）。请稍后重试，本次没有采用不完整的回答。"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelCallError(f"连接{service}失败。请检查网络和代理后再试，本次没有生成人物回答。") from exc
        if response.status_code >= 400:
            raise ModelCallError(notice_for_http(response.status_code, response.text, service))
        try:
            body = response.json()
        except ValueError as exc:
            raise ModelCallError(f"{service}返回的内容无法解析。本次没有生成人物回答。") from exc
        return _message_text(body)


class DeepSeekChatModel(_HttpChat):
    """文字问答只调用 DeepSeek 官方接口。"""

    provider_id = "deepseek"

    def configured(self) -> bool:
        return self.settings.deepseek_configured

    def missing_notice(self) -> str:
        return "尚未配置 DeepSeek。请在项目根目录的 .env 填写 DEEPSEEK_API_KEY。当前不会编造人物回答。"

    def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.configured():
            raise ModelCallError(self.missing_notice())
        url = self.settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        return self._post(
            url,
            self.settings.deepseek_api_key,
            {
                "model": self.settings.deepseek_model,
                "messages": messages,
                "temperature": 0.2,
                "thinking": {"type": "disabled"},
            },
            "DeepSeek",
        )
