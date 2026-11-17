import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import json, tempfile
from benchmark import obtain_response
import httpx
from llbench.client import Client, IncompleteResponseError
from llbench.common import config


class RequestPolicyTests(unittest.TestCase):
    def client(self):
        client = Client(config())
        client.c["benchmark"]["retry_delay_seconds"] = 0
        return client

    def test_received_wrong_answer_is_not_retried(self):
        client = self.client()
        client._request = Mock(
            return_value={"answer": "wrong answer", "finish_reason": "stop"}
        )
        result = client.request({})
        self.assertEqual(result["technical_attempts"], 1)
        self.assertEqual(client._request.call_count, 1)

    @patch("llbench.client.dump")
    def test_network_failure_is_retried(self, _dump):
        client = self.client()
        client._request = Mock(
            side_effect=[
                httpx.ReadTimeout("connection interrupted"),
                {"answer": "received"},
            ]
        )
        result = client.request({})
        self.assertEqual(result["technical_attempts"], 2)
        self.assertEqual(len(result["technical_failures"]), 1)
        self.assertEqual(client._request.call_count, 2)

    @patch("llbench.client.dump")
    def test_incomplete_stream_retry_is_bounded(self, _dump):
        client = self.client()
        client._request = Mock(side_effect=IncompleteResponseError("no finish chunk"))
        with self.assertRaises(IncompleteResponseError):
            client.request({})
        self.assertEqual(client._request.call_count, 3)

    def test_configuration_error_is_not_retried(self):
        client = self.client()
        request = httpx.Request("POST", "http://127.0.0.1/v1/chat/completions")
        response = httpx.Response(400, request=request)
        client._request = Mock(
            side_effect=httpx.HTTPStatusError(
                "invalid config", request=request, response=response
            )
        )
        with self.assertRaises(httpx.HTTPStatusError):
            client.request({})
        self.assertEqual(client._request.call_count, 1)

    def test_token_limit_is_not_retried(self):
        client = self.client()
        client._request = Mock(
            return_value={"answer": "partial", "finish_reason": "length"}
        )
        client.request({})
        self.assertEqual(client._request.call_count, 1)


class SavedResponseTests(unittest.TestCase):
    def test_received_response_survives_interrupted_scoring(self):
        with tempfile.TemporaryDirectory() as folder:
            body = {"messages": [{"role": "user", "content": "test"}]}
            raw = {
                "request": body,
                "finish_reason": "length",
                "answer": "partial model answer",
            }
            path = Path(folder) / "response.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            client = Mock()
            self.assertEqual(obtain_response(client, body, path), raw)
            client.request.assert_not_called()
