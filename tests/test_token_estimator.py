from app.quota.token_estimator import estimate_prompt_tokens


def test_estimate_prompt_tokens_counts_utf8_bytes_and_message_overhead() -> None:
    estimate = estimate_prompt_tokens([("user", "hello"), ("assistant", "你好")])

    assert estimate >= len(b"hello") + len("你好".encode())


def test_estimate_prompt_tokens_counts_each_message() -> None:
    one_message = estimate_prompt_tokens([("user", "hello")])
    two_messages = estimate_prompt_tokens([("user", "hello"), ("assistant", "")])

    assert two_messages > one_message
