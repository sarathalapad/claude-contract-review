from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from contractreview.contract import Contract
from contractreview.playbook import Playbook

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "data" / "contracts"


@pytest.fixture(scope="session")
def playbook() -> Playbook:
    return Playbook.load()


@pytest.fixture
def saas() -> Contract:
    return Contract.load(CONTRACTS / "saas_agreement.md")


def make_message(
    content: list[dict[str, Any]],
    stop_reason: str,
    usage: dict[str, int] | None = None,
    stop_details: dict[str, Any] | None = None,
) -> BetaMessage:
    """Build a real SDK message object so tests exercise the same attribute
    access the live API responses would."""
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "stop_details": stop_details,
            "usage": usage
            or {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
    )


class FakeStream:
    def __init__(self, message: BetaMessage) -> None:
        self._message = message
        self.text_stream = iter([b.text for b in message.content if b.type == "text"])

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> BetaMessage:
        return self._message


class FakeMessages:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def _next(self, params: dict[str, Any]) -> BetaMessage:
        self.calls.append(copy.deepcopy(params))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def create(self, **params: Any) -> BetaMessage:
        return self._next(params)

    def stream(self, **params: Any) -> FakeStream:
        return FakeStream(self._next(params))


class FakeClient:
    """Stands in for anthropic.Anthropic(): only client.beta.messages is used."""

    def __init__(self, script: list[Any]) -> None:
        self.beta = type("Beta", (), {})()
        self.beta.messages = FakeMessages(script)
