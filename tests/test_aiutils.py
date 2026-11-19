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


class AIRequestTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
