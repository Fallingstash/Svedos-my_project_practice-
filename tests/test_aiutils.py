import unittest
from types import SimpleNamespace
from unittest.mock import patch

from openai import OpenAIError

from utils import aiutils
from utils.aiutils import AIRequestError
from utils.config import AI_UNAVAILABLE_MESSAGE, MODEL


class ProviderError(OpenAIError):
    def __init__(self, status_code):
        super().__init__("provider error")
        self.status_code = status_code
        self.body = {
            "error": {
                "code": "model_unavailable",
                "message": "model unavailable",
            }
        }


def response_with_content(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ]
    )


class SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as error:
            raise AttributeError(key) from error

    def __setattr__(self, key, value):
        self[key] = value


class AIRequestTests(unittest.TestCase):
    def test_chat_messages_preserve_full_history_order(self):
        history = [
            {"role": "user", "content": "oldest " + "x" * 8000},
            {"role": "assistant", "content": "middle"},
            {"role": "user", "content": "latest"},
        ]

        messages = aiutils.get_chat_messages("system", "question", history)

        self.assertEqual(messages[1]["content"], history[0]["content"])
        self.assertEqual(
            [message["content"] for message in messages[1:-1]],
            [message["content"] for message in history],
        )
        self.assertEqual(messages[-1]["content"], "question")

    def test_primary_model_is_tried_before_fallback(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: response_with_content(
                        kwargs["model"]
                    )
                )
            )
        )
        with patch.object(aiutils, "get_client", return_value=client), patch.object(
            aiutils,
            "MODEL_FALLBACKS",
            ["fallback-model"],
        ):
            answer = aiutils.request_ai_response("system", "question")

        self.assertEqual(answer, MODEL)

    def test_model_error_falls_back_and_exhaustion_is_an_error(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs["model"])
            if kwargs["model"] == MODEL:
                raise ProviderError(404)
            return response_with_content("fallback answer")

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch.object(aiutils, "get_client", return_value=client), patch.object(
            aiutils,
            "MODEL_FALLBACKS",
            ["fallback-model"],
        ):
            answer = aiutils.request_ai_response("system", "question")

        self.assertEqual(answer, "fallback answer")
        self.assertEqual(calls, [MODEL, "fallback-model"])

    def test_empty_responses_do_not_look_like_successful_answers(self):
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: response_with_content("")
                )
            )
        )
        with patch.object(aiutils, "get_client", return_value=client), patch.object(
            aiutils,
            "MODEL_FALLBACKS",
            [],
        ):
            with self.assertRaisesRegex(AIRequestError, AI_UNAVAILABLE_MESSAGE):
                aiutils.request_ai_response("system", "question")

    def test_attached_metrics_remain_in_user_prompt(self):
        class State:
            def get(self, key, default=None):
                return ["Громкость (LUFS)"] if key == "attachedmetrics" else default

        with patch.object(aiutils.st, "session_state", State()):
            _, prompt = aiutils.get_strict_prompt(
                question="Что улучшить?",
                segments_list=[],
                genre_name="Рэп",
            )

        self.assertIn("Громкость (LUFS)", prompt)

    def test_auth_errors_do_not_try_fallback_models(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs["model"])
            raise ProviderError(401)

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        with patch.object(aiutils, "get_client", return_value=client), patch.object(
            aiutils,
            "MODEL_FALLBACKS",
            ["fallback-model"],
        ):
            with self.assertRaises(AIRequestError):
                aiutils.request_ai_response("system", "question")

        self.assertEqual(calls, [MODEL])

    def test_context_compaction_keeps_original_history_and_persists_boundary(self):
        state = SessionState(context_summary="", context_summary_message_count=0)
        history = [
            {"role": "user", "content": f"decision-{index} " + "x" * 350}
            for index in range(24)
        ]
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: response_with_content(
                        "summary of preserved decisions"
                    )
                )
            )
        )

        with patch.object(aiutils.st, "session_state", state), patch.object(
            aiutils,
            "_message_token_count",
            side_effect=lambda message: len(message["content"]),
        ):
            messages, summary = aiutils._messages_with_context_summary(
                client,
                "system",
                "current question",
                history,
                5000,
            )

        self.assertTrue(summary)
        self.assertEqual(len(history), 24)
        self.assertGreater(state.context_summary_message_count, 0)
        self.assertIn("summary of preserved decisions", messages[1]["content"])
        self.assertEqual(messages[-1]["content"], "current question")

    def test_response_metadata_uses_provider_model_and_reported_usage(self):
        state = SessionState(context_summary="", context_summary_message_count=0)
        response = response_with_content("answer")
        response.model = "provider/actual-model"
        response.usage = SimpleNamespace(prompt_tokens=51, completion_tokens=12)
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )

        with patch.object(aiutils.st, "session_state", state), patch.object(
            aiutils, "get_client", return_value=client
        ), patch.object(aiutils, "_model_context_window", return_value=8192):
            answer = aiutils.request_ai_response("system", "question")

        self.assertEqual(answer, "answer")
        self.assertEqual(state.last_ai_metadata["model"], "provider/actual-model")
        self.assertEqual(state.last_ai_metadata["prompt_tokens"], 51)
        self.assertEqual(state.last_ai_metadata["completion_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
