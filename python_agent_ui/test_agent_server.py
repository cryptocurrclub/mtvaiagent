from agent_server import _is_retryable_rpc_error


def test_detects_already_known_error():
    assert _is_retryable_rpc_error("already known") is True
    assert _is_retryable_rpc_error("nonce too low") is True
    assert _is_retryable_rpc_error("replacement transaction underpriced") is True
    assert _is_retryable_rpc_error("other error") is False
