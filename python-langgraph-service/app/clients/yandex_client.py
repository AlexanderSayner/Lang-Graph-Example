import asyncio
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class AsyncOperationTimeout(RuntimeError):
    """Raised when a Yandex async (deferred) operation does not finish within the deadline."""


class GenerationResult(str):
    """
    Preserves the string interface while adding usage metadata.
    """

    def __new__(cls, text, usage=None):
        instance = super().__new__(cls, text)
        instance.usage = usage or {}
        return instance


class YandexGPTClient:
    """
    Async client for the YandexGPT API.

    Two completion modes are supported:

    * ``sync``  (default) – the synchronous ``/completion`` endpoint. Latency is predictable
      (seconds), which is what interactive chat and graph execution need.
    * ``async`` – the deferred ``/completionAsync`` endpoint. It is ~50% cheaper, but it is a
      *batch* API: requests are queued and an operation may stay pending for an arbitrary
      amount of time. Use it only for background workloads (`YC_COMPLETION_MODE=async`).

    In ``async`` mode polling is bounded by ``YC_ASYNC_MAX_WAIT_SECONDS``, so a stuck operation
    surfaces as an error (or falls back to ``sync``) instead of hanging the graph forever.
    """

    SYNC_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    ASYNC_URL = "https://ai.api.cloud.yandex.net/foundationModels/v1/completionAsync"
    OPERATIONS_URL = "https://operation.api.cloud.yandex.net/operations"

    def __init__(self, api_key: str, folder_id: str, mode: str | None = None):
        self.api_key = api_key
        self.folder_id = folder_id
        self.mode = (mode or settings.YC_COMPLETION_MODE or "sync").strip().lower()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json"
        }

    def _payload(self, user_message: str, system_message: str, model_name: str,
                 temperature: float, max_tokens: int) -> dict:
        return {
            "modelUri": f"gpt://{self.folder_id}/{model_name}",
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens)
            },
            "messages": [
                {
                    "role": "system",
                    "text": system_message
                },
                {
                    "role": "user",
                    "text": user_message
                }
            ]
        }

    @staticmethod
    def _parse_result(result_data: dict) -> GenerationResult:
        # The synchronous endpoint wraps the payload in a "result" object, while the
        # operation response of the async endpoint is already unwrapped.
        if "alternatives" not in result_data and isinstance(result_data.get("result"), dict):
            result_data = result_data["result"]

        text = result_data["alternatives"][0]["message"]["text"]

        raw_usage = result_data.get("usage", {})
        usage = {
            "input_tokens": int(raw_usage.get("inputTextTokens", 0)),
            "completion_tokens": int(raw_usage.get("completionTokens", 0)),
            "total_tokens": int(raw_usage.get("totalTokens", 0))
        }
        return GenerationResult(text, usage)

    async def generate(
            self,
            user_message: str,
            system_message: str = settings.system_prompt,
            model_name: str = "yandexgpt",
            temperature: float = 0.6,
            max_tokens: int = 2000
    ) -> GenerationResult:
        """
        Generates a completion using the configured completion mode.
        """
        if not self.api_key:
            raise ValueError("Yandex API Key is not configured.")

        payload = self._payload(user_message, system_message, model_name, temperature, max_tokens)

        if self.mode == "async":
            try:
                return await self._generate_async(payload)
            except AsyncOperationTimeout as e:
                if not settings.YC_ASYNC_FALLBACK_TO_SYNC:
                    raise
                logger.warning(
                    f"Yandex async operation exceeded {settings.YC_ASYNC_MAX_WAIT_SECONDS:g}s "
                    f"({e}); falling back to the synchronous endpoint."
                )

        return await self._generate_sync(payload)

    async def _generate_sync(self, payload: dict) -> GenerationResult:
        async with httpx.AsyncClient() as client:
            logger.debug(f"Submitting sync request to Yandex: {payload}")
            response = await client.post(
                self.SYNC_URL,
                json=payload,
                headers=self._headers(),
                timeout=settings.YC_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return self._parse_result(response.json())

    async def _generate_async(self, payload: dict) -> GenerationResult:
        """
        Sends an asynchronous (deferred) request to YandexGPT and polls the operation
        until it completes or the configured deadline is reached.
        """
        headers = self._headers()
        interval = max(settings.YC_ASYNC_POLL_INTERVAL_SECONDS, 0.0)
        deadline = time.monotonic() + settings.YC_ASYNC_MAX_WAIT_SECONDS

        async with httpx.AsyncClient() as client:
            logger.debug(f"Submitting async request to Yandex: {payload}")
            response = await client.post(
                self.ASYNC_URL,
                json=payload,
                headers=headers,
                timeout=30.0
            )
            response.raise_for_status()
            operation_id = response.json().get("id")

            if not operation_id:
                raise RuntimeError("Yandex API did not return an operation ID")

            poll_url = f"{self.OPERATIONS_URL}/{operation_id}"
            polls = 0

            while True:
                if time.monotonic() >= deadline:
                    raise AsyncOperationTimeout(
                        f"operation {operation_id} still pending after "
                        f"{settings.YC_ASYNC_MAX_WAIT_SECONDS:g}s ({polls} polls)"
                    )

                await asyncio.sleep(interval)
                polls += 1

                poll_response = await client.get(
                    poll_url,
                    headers=headers,
                    timeout=10.0
                )
                poll_response.raise_for_status()
                poll_data = poll_response.json()

                if poll_data.get("done"):
                    if "error" in poll_data:
                        raise RuntimeError(f"Yandex async operation failed: {poll_data['error']}")

                    result_data = poll_data.get("response", {})
                    logger.debug(f"Yandex async operation {operation_id} finished after {polls} polls")
                    return self._parse_result(result_data)

                if polls % 10 == 0:
                    logger.info(
                        f"Yandex async operation {operation_id} still pending "
                        f"({polls} polls, {settings.YC_ASYNC_MAX_WAIT_SECONDS:g}s limit)"
                    )

