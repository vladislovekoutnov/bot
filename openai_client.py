import os
import asyncio
from openai import OpenAI

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

class LLM:
    def __init__(self) -> None:
        self.client = OpenAI()
        self.model = OPENAI_MODEL

    async def chat(self, system: str, user: str) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_chat, system, user)

    def _sync_chat(self, system: str, user: str) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0.7,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"(ошибка LLM: {e})"