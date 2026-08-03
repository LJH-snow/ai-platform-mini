import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal, Never

import httpx

from app.core.settings import get_settings
from app.exceptions.openai import (
    OpenAIModelNotFoundError,
    OpenAIProviderError,
    OpenAIRequestError,
    OpenAIUnavailableError,
)

logger = logging.getLogger(__name__)

type OpenAIOperation = Literal["chat", "models"]


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._default_model = default_model
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    @property
    def default_model(self) -> str:
        return self._default_model

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = self._build_chat_payload(payload, stream=False)
        data = await self._request_json(
            "POST",
            "chat/completions",
            payload=request_payload,
            operation="chat",
        )
        return self._translate_chat_response(data)

    async def chat_stream(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        request_payload = self._build_chat_payload(payload, stream=True)
        model = request_payload["model"]
        stream_model = model if isinstance(model, str) else self._default_model
        invalid_json_line_count = 0
        max_invalid_json_line_length = 0
        usage: tuple[int | None, int | None] = (None, None)
        terminal_chunk: dict[str, Any] | None = None

        try:
            async with self._client.stream(
                "POST",
                "chat/completions",
                json=request_payload,
            ) as response:
                if response.is_error:
                    await response.aread()
                self._raise_for_status(response, operation="chat")

                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if (
                        not stripped
                        or stripped.startswith(":")
                        or stripped.startswith("event:")
                    ):
                        continue
                    if not stripped.startswith("data:"):
                        continue

                    event_data = stripped.removeprefix("data:").strip()
                    if event_data == "[DONE]":
                        break

                    try:
                        event = json.loads(event_data)
                    except ValueError as exc:
                        invalid_json_line_count += 1
                        max_invalid_json_line_length = max(
                            max_invalid_json_line_length,
                            len(event_data),
                        )
                        if terminal_chunk is not None:
                            raise OpenAIProviderError(
                                "OpenAI stream returned an event after terminal chunk."
                            ) from exc
                        continue
                    if not isinstance(event, dict):
                        raise OpenAIProviderError(
                            "OpenAI returned an unexpected streaming response shape."
                        )

                    usage_only = self._extract_usage_only_event(event)
                    if usage_only is not None:
                        if terminal_chunk is None:
                            raise OpenAIProviderError(
                                "OpenAI stream returned usage before terminal chunk."
                            )
                        usage = usage_only
                        continue

                    if terminal_chunk is not None:
                        raise OpenAIProviderError(
                            "OpenAI stream returned an event after terminal chunk."
                        )

                    event_usage = self._extract_usage(event)
                    if event_usage != (None, None):
                        prompt_tokens, completion_tokens = usage
                        event_prompt_tokens, event_completion_tokens = event_usage
                        usage = (
                            (
                                event_prompt_tokens
                                if event_prompt_tokens is not None
                                else prompt_tokens
                            ),
                            (
                                event_completion_tokens
                                if event_completion_tokens is not None
                                else completion_tokens
                            ),
                        )

                    chunk = self._translate_stream_event(event)
                    if chunk["done"] is True:
                        terminal_chunk = chunk
                    else:
                        yield chunk

            if terminal_chunk is None:
                raise OpenAIProviderError(
                    "OpenAI stream ended without a terminal chunk."
                )

            prompt_tokens, completion_tokens = usage
            if prompt_tokens is not None:
                terminal_chunk["prompt_eval_count"] = prompt_tokens
            if completion_tokens is not None:
                terminal_chunk["eval_count"] = completion_tokens
            yield terminal_chunk
        except httpx.RequestError as exc:
            raise OpenAIUnavailableError("Unable to reach the OpenAI API.") from exc
        finally:
            if invalid_json_line_count:
                logger.warning(
                    "openai_stream_invalid_json",
                    extra={
                        "model": stream_model,
                        "invalid_json_line_count": invalid_json_line_count,
                        "max_invalid_json_line_length": (max_invalid_json_line_length),
                    },
                )

    async def list_models(self) -> dict[str, Any]:
        data = await self._request_json("GET", "models", operation="models")
        entries = data.get("data")
        if not isinstance(entries, list):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected models response shape."
            )

        models: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if isinstance(model_id, str):
                models.append({"name": model_id})
        return {"models": models}

    def _build_chat_payload(
        self,
        payload: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        model = payload.get("model", self._default_model)
        messages = payload.get("messages")
        if not isinstance(model, str) or not isinstance(messages, list):
            raise OpenAIProviderError("OpenAI received an invalid chat payload.")

        request_payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if stream:
            request_payload["stream_options"] = {"include_usage": True}

        options = payload.get("options")
        if isinstance(options, dict):
            temperature = options.get("temperature")
            if isinstance(temperature, int | float) and not isinstance(
                temperature, bool
            ):
                request_payload["temperature"] = temperature

            max_tokens = options.get("num_predict")
            if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
                request_payload["max_completion_tokens"] = max_tokens

        return request_payload

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        operation: OpenAIOperation,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=payload)
        except httpx.RequestError as exc:
            raise OpenAIUnavailableError("Unable to reach the OpenAI API.") from exc

        self._raise_for_status(response, operation=operation)

        try:
            data = response.json()
        except ValueError as exc:
            raise OpenAIProviderError("OpenAI returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise OpenAIProviderError("OpenAI returned an unexpected response shape.")
        return data

    def _translate_chat_response(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        model = data.get("model")
        created = data.get("created")
        choices = data.get("choices")
        if (
            not isinstance(model, str)
            or not isinstance(created, int)
            or isinstance(created, bool)
            or not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected chat response shape."
            )

        choice = choices[0]
        message = choice.get("message")
        finish_reason = choice.get("finish_reason")
        if not isinstance(message, dict):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected chat response shape."
            )

        role = message.get("role")
        if not isinstance(role, str) or (
            finish_reason is not None and not isinstance(finish_reason, str)
        ):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected chat response shape."
            )
        content = self._extract_text(message, allow_empty=False)

        prompt_tokens, completion_tokens = self._extract_usage(data)
        result: dict[str, Any] = {
            "model": model,
            "created_at": self._format_created_at(created),
            "message": {
                "role": role,
                "content": content,
            },
            "done": True,
            "done_reason": finish_reason,
        }
        if prompt_tokens is not None:
            result["prompt_eval_count"] = prompt_tokens
        if completion_tokens is not None:
            result["eval_count"] = completion_tokens
        return result

    def _translate_stream_event(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        model = data.get("model")
        created = data.get("created")
        choices = data.get("choices")
        if (
            not isinstance(model, str)
            or not isinstance(created, int)
            or isinstance(created, bool)
            or not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected streaming response shape."
            )

        choice = choices[0]
        delta = choice.get("delta")
        finish_reason = choice.get("finish_reason")
        if not isinstance(delta, dict):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected streaming response shape."
            )
        if finish_reason is not None and (
            not isinstance(finish_reason, str) or not finish_reason
        ):
            raise OpenAIProviderError(
                "OpenAI returned an invalid streaming finish_reason."
            )

        role_value = delta.get("role")
        role = "assistant" if role_value is None else role_value
        if not isinstance(role, str):
            raise OpenAIProviderError(
                "OpenAI returned an unexpected streaming response shape."
            )
        content = self._extract_text(delta, allow_empty=True)

        chunk: dict[str, Any] = {
            "model": model,
            "created_at": self._format_created_at(created),
            "message": {
                "role": role,
                "content": content,
            },
            "done": finish_reason is not None,
        }
        if finish_reason is not None:
            chunk["done_reason"] = finish_reason
        return chunk

    @staticmethod
    def _extract_usage_only_event(
        data: dict[str, Any],
    ) -> tuple[int, int] | None:
        choices = data.get("choices")
        if choices != []:
            return None

        prompt_tokens, completion_tokens, _ = OpenAIProvider._validate_usage(
            data.get("usage"),
            require_all=True,
            error_message="OpenAI returned invalid streaming usage.",
        )
        if prompt_tokens is None or completion_tokens is None:
            raise OpenAIProviderError("OpenAI returned invalid streaming usage.")
        return prompt_tokens, completion_tokens

    @staticmethod
    def _extract_text(data: dict[str, Any], *, allow_empty: bool) -> str:
        if "content" not in data:
            if "refusal" in data:
                raise OpenAIProviderError(
                    "OpenAI response omitted content before refusal."
                )
            if allow_empty and set(data).issubset({"role"}):
                return ""
            raise OpenAIProviderError(
                "OpenAI response did not contain supported text content or refusal."
            )

        content = data["content"]
        if content is not None:
            if not isinstance(content, str):
                raise OpenAIProviderError(
                    "OpenAI response contained an invalid content type."
                )
            if not allow_empty and not content:
                raise OpenAIProviderError("OpenAI response contained empty text.")
            return content

        refusal = data.get("refusal")
        if refusal is not None:
            if not isinstance(refusal, str):
                raise OpenAIProviderError(
                    "OpenAI response contained an invalid refusal type."
                )
            if not allow_empty and not refusal:
                raise OpenAIProviderError("OpenAI response contained empty text.")
            return refusal

        text_fields = {"role", "content", "refusal"}
        if allow_empty and set(data).issubset(text_fields):
            return ""

        raise OpenAIProviderError(
            "OpenAI response did not contain supported text content or refusal."
        )

    @staticmethod
    def _extract_usage(
        data: dict[str, Any],
    ) -> tuple[int | None, int | None]:
        if "usage" not in data or data["usage"] is None:
            return None, None

        prompt_tokens, completion_tokens, _ = OpenAIProvider._validate_usage(
            data["usage"],
            require_all=False,
            error_message="OpenAI returned invalid usage.",
        )
        return prompt_tokens, completion_tokens

    @staticmethod
    def _validate_usage(
        usage: object,
        *,
        require_all: bool,
        error_message: str,
    ) -> tuple[int | None, int | None, int | None]:
        if not isinstance(usage, dict):
            raise OpenAIProviderError(error_message)

        token_counts: list[int | None] = []
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if key not in usage:
                if require_all:
                    raise OpenAIProviderError(error_message)
                token_counts.append(None)
                continue
            value = usage[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise OpenAIProviderError(error_message)
            token_counts.append(value)

        return token_counts[0], token_counts[1], token_counts[2]

    @staticmethod
    def _format_created_at(created: int) -> str:
        try:
            timestamp = datetime.fromtimestamp(created, tz=UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise OpenAIProviderError(
                "OpenAI returned an invalid created timestamp."
            ) from exc
        return timestamp.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _raise_for_status(
        response: httpx.Response,
        *,
        operation: OpenAIOperation,
    ) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            OpenAIProvider._raise_http_error(
                response,
                operation=operation,
                cause=exc,
            )

    @staticmethod
    def _raise_http_error(
        response: httpx.Response,
        *,
        operation: OpenAIOperation,
        cause: Exception,
    ) -> Never:
        status_code = response.status_code
        if (
            operation == "chat"
            and status_code == httpx.codes.NOT_FOUND
            and OpenAIProvider._extract_error_code(response) == "model_not_found"
        ):
            raise OpenAIModelNotFoundError(
                "OpenAI model was not found (status 404)."
            ) from cause
        raise OpenAIRequestError(
            f"OpenAI request failed with status {status_code}."
        ) from cause

    @staticmethod
    def _extract_error_code(response: httpx.Response) -> str | None:
        try:
            data = response.json()
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        error = data.get("error")
        if not isinstance(error, dict):
            return None
        code = error.get("code")
        return code if isinstance(code, str) else None


def get_openai_provider() -> OpenAIProvider:
    settings = get_settings()
    return OpenAIProvider(
        api_key=settings.openai_api_key.get_secret_value(),
        base_url=settings.openai_base_url,
        default_model=settings.openai_default_model,
        timeout_seconds=settings.openai_timeout_seconds,
    )
