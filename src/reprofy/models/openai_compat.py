from time import perf_counter
from typing import Any, Sequence

from openai import OpenAI

from.base import Completion, Message

class OpenAICompatBackend:
    def __init__(self, client: OpenAI, *, backend: str, model: str) -> None:
        self.client = client
        self.backend = backend
        self.model = model
        
    def complete(
        self, 
        messages: Sequence[Message], 
        *, 
        reasoning_effort: str, 
        max_completion_tokens: int, 
        response_format: dict[str, any] | None = None
    ) -> Completion:
        options: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "reasoning_effort": reasoning_effort,
            "max_completion_tokens": max_completion_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if response_format is not None:
            options["response_format"] = response_format
        
        content: list[str] = []
        reasoning: list[str] = []
        finish_reason = None
        usage = None
        returned_model = self.model
        started = perf_counter()
        
        with self.client.chat.completions.create(**options) as stream:
            for chunk in stream:
                returned_model = chunk.model or returned_model
                if chunk.model is not None:
                    usage = chunk.usage
                
                for choice in chunk.choices:
                    if choice.delta.content:
                        content.append(choice.delta.content)
                    detail = getattr(choice.delta, "reasoning_content", None)
                    if detail:
                        reasoning.append(detail)
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason

        if finish_reason is None:
            raise RuntimeError("Model stream ended without a finish reason")

        token_details = usage.completion_tokens_details if usage else None
        return Completion(
            content="".join(content),
            reasoning="".join(reasoning) or None,
            finish_reason=finish_reason,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            reasoning_tokens=(
                token_details.reasoning_tokens if token_details else None
            ),
            backend=self.backend,
            model=returned_model,
            latency_seconds=perf_counter() - started,
        )
        
        
