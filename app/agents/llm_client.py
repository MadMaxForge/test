"""Shared LLM client — calls OpenRouter API (OpenAI-compatible) for all agents."""

import os
import httpx
import json
from typing import Optional


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default models
MODEL_KIMI_K2_5 = "moonshotai/kimi-k2.5"
MODEL_GEMINI_FLASH = "google/gemini-2.5-flash-preview"


def _get_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError("OPENROUTER_API_KEY not set")
    return key


async def chat_completion(
    messages: list[dict],
    model: str = MODEL_KIMI_K2_5,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    """Send a chat completion request to OpenRouter.

    Args:
        messages: List of {'role': ..., 'content': ...} dicts.
        model: Model identifier on OpenRouter.
        temperature: Sampling temperature.
        max_tokens: Max tokens in response.
        json_mode: If True, request JSON output format.

    Returns:
        The assistant's response text.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/MadMaxForge/test",
        "X-Title": "Instagram Agent System",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        raise ValueError(f"No choices in LLM response: {data}")

    return choices[0]["message"]["content"]


async def chat_completion_json(
    messages: list[dict],
    model: str = MODEL_KIMI_K2_5,
    temperature: float = 0.5,
    max_tokens: int = 4096,
) -> dict:
    """Chat completion that returns parsed JSON."""
    raw = await chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )

    # Strip markdown code blocks if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)

    return json.loads(text)


async def describe_image(
    image_base64: str,
    instruction: str,
    model: str = MODEL_GEMINI_FLASH,
) -> str:
    """Use a vision model to describe an image.

    Args:
        image_base64: Base64-encoded image data.
        instruction: What to describe / analyze.
        model: Vision-capable model.

    Returns:
        Text description from the model.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}",
                    },
                },
            ],
        }
    ]

    return await chat_completion(
        messages=messages,
        model=model,
        temperature=0.3,
        max_tokens=2048,
    )
