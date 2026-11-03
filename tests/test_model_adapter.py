from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from reprofy.models.openai_compat import OpenAICompatBackend


def chunk(*, content=None, reasoning=None, finish=None, usage=None, model="fixture"):
    return SimpleNamespace(
        model=model,
        usage=usage,
        choices=[] if content is None and finish is None else [SimpleNamespace(
            delta=SimpleNamespace(content=content, reasoning_content=reasoning),
            finish_reason=finish,
        )],
    )


def complete(chunks):
    client = Mock()
    client.chat.completions.create.return_value = nullcontext(iter(chunks))
    result = OpenAICompatBackend(client, backend="fake", model="fixture").complete(
        [{"role": "user", "content": "Test"}],
        reasoning_effort="low",
        max_completion_tokens=100,
    )
    return result


@pytest.mark.parametrize("usage_model", [None, "fixture"])
def test_usage_survives_a_later_chunk_without_usage(usage_model):
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=20,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=15),
    )
    result = complete([
        chunk(content="Hello", reasoning="reasoning"),
        chunk(finish="stop"),
        chunk(usage=usage, model=usage_model),
        chunk(),
    ])
    assert result.content == "Hello"
    assert result.reasoning == "reasoning"
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens, result.reasoning_tokens) == (10, 20, 15)


def test_absent_usage_stays_unknown():
    result = complete([chunk(content="Hello", finish="stop")])
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
    assert result.reasoning_tokens is None


def test_stream_without_finish_reason_is_rejected():
    with pytest.raises(RuntimeError, match="without a finish reason"):
        complete([chunk(content="Partial")])
