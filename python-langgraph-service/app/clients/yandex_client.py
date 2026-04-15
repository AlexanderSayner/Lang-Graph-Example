import logging

import httpx

logger = logging.getLogger(__name__)


class YandexGPTClient:
    """
    Lightweight async client for YandexGPT API.
    Documentation: https://cloud.yandex.ru/docs/yandexgpt/api-ref/grpc/
    REST Endpoint: https://llm.api.cloud.yandex.net/foundationModels/v1/completion
    """

    def __init__(self, api_key: str, folder_id: str):
        self.api_key = api_key
        self.folder_id = folder_id
        # Yandex GPT REST endpoint
        self.base_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    async def generate(
            self,
            user_message: str,
            system_message: str = "Ты умный помощник.",
            model_name: str = "yandexgpt",
            temperature: float = 0.6,
            max_tokens: int = 2000
    ) -> str:
        """
        Sends a request to YandexGPT and returns the text result.
        """
        if not self.api_key:
            raise ValueError("Yandex API Key is not configured.")

        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json"
        }

        # Construct the YandexGPT payload
        payload = {
            "modelUri": f"gpt://{self.folder_id}/{model_name}",
            "completionOptions": {
                "stream": False,
                "temperature": temperature,
                "maxTokens": str(max_tokens)  # API expects string sometimes
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
                print(f"DEBUG: Sending request to Yandex: {payload}")
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers=headers,
                    timeout=30.0  # Generous timeout for LLMs
                )
                response.raise_for_status()

                data = response.json()
                # Parse the specific Yandex response structure
                return data["result"]["alternatives"][0]["message"]["text"]

            except httpx.HTTPStatusError as e:
                logger.error(f"Yandex API Error: {e.response.status_code} - {e.response.text}")
                raise RuntimeError(f"Yandex API request failed: {e.response.text}")
            except Exception as e:
                logger.error(f"Error calling YandexGPT: {e}")
                raise
