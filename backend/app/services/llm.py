from typing import AsyncIterator, Protocol

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from backend.app.config import settings


class LLMProvider(Protocol):
    async def generate(self, system: str, messages: list[dict]) -> str: ...
    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]: ...


class OpenAIProvider:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    async def generate(self, system: str, messages: list[dict]) -> str:
        all_messages = [{"role": "system", "content": system}] + messages
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=0,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        all_messages = [{"role": "system", "content": system}] + messages
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=0,
            max_tokens=4096,
            stream=True,
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicProvider:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model

    async def generate(self, system: str, messages: list[dict]) -> str:
        response = await self.client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=4096,
            temperature=0,
        )
        return response.content[0].text

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        async with self.client.messages.stream(
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=4096,
            temperature=0,
        ) as stream:
            async for text in stream.text_stream:
                yield text


def get_llm_provider() -> LLMProvider:
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when using Anthropic provider")
        return AnthropicProvider()
    else:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when using OpenAI provider")
        return OpenAIProvider()
