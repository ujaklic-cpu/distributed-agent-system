from shared.hashing import plan_hash, tool_call_hash


def test_plan_hash_stable_for_key_order():
    assert plan_hash({"b": 2, "a": 1}) == plan_hash({"a": 1, "b": 2})


def test_tool_hash_changes_with_command():
    first = tool_call_hash("shell", {"command": "echo 1"})
    second = tool_call_hash("shell", {"command": "echo 2"})
    assert first != second
