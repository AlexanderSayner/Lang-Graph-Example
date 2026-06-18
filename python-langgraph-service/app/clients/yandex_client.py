import asyncio
import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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
    Lightweight async client for YandexGPT API using Asynchronous Mode.
    Asynchronous mode is exactly 50% cheaper than synchronous mode!
    """

    def __init__(self, api_key: str, folder_id: str):
        self.api_key = api_key
        self.folder_id = folder_id

        # Asynchronous endpoints
        self.completion_async_url = "https://ai.api.cloud.yandex.net/foundationModels/v1/completionAsync"
        self.operations_url = "https://operation.api.cloud.yandex.net/operations"

    async def generate(
            self,
            user_message: str,
            system_message: str = settings.system_prompt,
            model_name: str = "yandexgpt",
            temperature: float = 0.6,
            max_tokens: int = 2000
    ) -> GenerationResult:
        """
        Sends an asynchronous request to YandexGPT (50% cheaper) and polls for the result.
        """
        if not self.api_key:
            raise ValueError("Yandex API Key is not configured.")

        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json"
        }

        # Construct the YandexGPT payload (identical to synchronous)
        payload = {
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

        async with httpx.AsyncClient() as client:
            try:
                # Submit the asynchronous request
                logger.debug(f"Submitting async request to Yandex: {payload}")
                response = await client.post(
                    self.completion_async_url,
                    json=payload,
                    headers=headers,
                    timeout=30.0
                )
                response.raise_for_status()
                operation_data = response.json()
                operation_id = operation_data.get("id")

                if not operation_id:
                    raise RuntimeError("Yandex API did not return an operation ID")

                # Poll the operation endpoint until it's done
                poll_url = f"{self.operations_url}/{operation_id}"


                while True:
                    await asyncio.sleep(2)  # Wait 2 seconds between polls

                    poll_response = await client.get(
                        poll_url,
                        headers=headers,
                        timeout=10.0
                    )
                    poll_response.raise_for_status()
                    poll_data = poll_response.json()

                    if poll_data.get("done"):
                        # Check if the operation failed
                        if "error" in poll_data:
                            raise RuntimeError(f"Yandex async operation failed: {poll_data['error']}")

                        # Extract the result from the "response" field
                        result_data = poll_data.get("response", {})
                        break

                # Parse the final result
                text = result_data["alternatives"][0]["message"]["text"]

                # Extract and parse usage data
                raw_usage = result_data.get("usage", {})
                usage = {
                    "input_tokens": int(raw_usage.get("inputTextTokens", 0)),
                    "completion_tokens": int(raw_usage.get("completionTokens", 0)),
                    "total_tokens": int(raw_usage.get("totalTokens", 0))
                }

                # Return the custom string subclass
                return GenerationResult(text, usage)

            except httpx.HTTPStatusError as e:
                logger.error(f"Yandex API Error: {e.response.status_code} - {e.response.text}")
                raise RuntimeError(f"Yandex API request failed: {e.response.text}")
            except Exception as e:
                logger.error(f"Error calling YandexGPT: {e}")
                raise
