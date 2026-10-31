from typing import Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from .base import Message, ModelBackend


T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(ValueError):
    pass


def complete_structured(
    backend: ModelBackend,
    messages: Sequence[Message],
    schema: type[T],
    *,
    reasoning_effort: str,
    max_completion_tokens: int,
) -> T:
    conversation = list(messages)
    budget = max_completion_tokens
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
        },
    }

    for attempt in range(2):
        result = backend.complete(
            conversation,
            reasoning_effort=reasoning_effort,
            max_completion_tokens=budget,
            response_format=response_format,
        )

        if result.finish_reason == "length":
            problem = "The response exceeded the token budget."
            budget *= 2
        elif result.finish_reason != "stop":
            raise StructuredOutputError(
                f"Unexpected finish reason: {result.finish_reason}"
            )
        else:
            try:
                return schema.model_validate_json(result.content, strict=True)
            except ValidationError as error:
                problem = error.json(include_input=False, include_url=False)

        if attempt == 0:
            conversation.append({
                "role": "user",
                "content": (
                    "Your previous response could not be accepted. "
                    "Return a complete JSON object matching the schema. "
                    f"Problem: {problem[:2000]}"
                ),
            })

    raise StructuredOutputError(
        f"No valid structured response after two attempts: {problem[:2000]}"
    )