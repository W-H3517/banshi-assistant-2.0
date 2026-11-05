import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent_demo.storage import Session, Store


def test_identity_is_stable_and_corruption_does_not_replace_it(tmp_path):
    store = Store(tmp_path)
    assert store.identity() == Store(tmp_path).identity()
    identity_path = tmp_path / "identity.json"
    identity_path.write_text('{"user_id":"broken"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        store.identity()
    assert json.loads(identity_path.read_text()) == {"user_id": "broken"}


def test_history_round_trip_keeps_tool_pairs_and_separates_sessions(tmp_path):
    store = Store(tmp_path)
    session = Session(model="deepseek-flash", messages=[
        ModelRequest(parts=[UserPromptPart("差旅材料？")]),
        ModelResponse(parts=[ToolCallPart("read_document", {"document_id": "travel"}, "call1")]),
        ModelRequest(parts=[ToolReturnPart("read_document", "模拟原文", "call1")]),
        ModelResponse(parts=[TextPart("见 travel T2。")]),
    ])
    store.save(session)
    restored = Store(tmp_path).load(str(session.id))
    assert restored == session
    second = Session(model="deepseek-flash")
    store.save(second)
    assert Store(tmp_path).load(str(second.id)).messages == []
    assert len(store.sessions()) == 2
    with pytest.raises(ValueError):
        store.load("../../.env")
