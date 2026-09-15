"""The one call out to OpenAI, behind a seam tests can replace.

Kept apart from the report's facts and checks so the provider is one file:
the router depends on `get_report_writer`, and a test overrides it with a
stand-in that never touches the network.

The key comes from the backend's environment and is never stored, logged or
sent to the browser. Requests are made with `store=False`, so OpenAI does not
keep them for later retrieval.
"""
import json
from typing import Protocol

import openai
from openai import AsyncOpenAI

from app.config import get_openai_settings
from app.services import report as rpt

# The gpt-5 family and the o-series reason before they answer: they take a
# reasoning effort and refuse a temperature. Older chat models are the other
# way round, so the parameter is only sent where it is understood.
REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
# reasoning tokens count against this too, so it is generous for a short report
MAX_OUTPUT_TOKENS = 12000
TIMEOUT_SECONDS = 120


class ReportError(Exception):
    """A failed call, with the HTTP status and the sentence the screen shows."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ReportWriter(Protocol):
    model: str

    async def write(self, facts: dict) -> dict: ...


class OpenAIReportWriter:
    def __init__(self, api_key: str, model: str):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=1)

    async def write(self, facts: dict) -> dict:
        extra = ({"reasoning": {"effort": "low"}}
                 if self.model.startswith(REASONING_PREFIXES) else {})
        try:
            response = await self._client.responses.create(
                model=self.model,
                instructions=rpt.INSTRUCTIONS,
                input="Facts (JSON):\n" + json.dumps(facts, ensure_ascii=False, indent=1),
                text={"format": {"type": "json_schema", "name": "qa_report",
                                 "schema": rpt.REPORT_SCHEMA, "strict": True}},
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
                **extra,
            )
        except openai.AuthenticationError:
            raise ReportError(502, "OpenAI rejected the API key. Check OPENAI_API_KEY "
                                   "in the backend's .env and restart the backend.") from None
        except openai.PermissionDeniedError:
            raise ReportError(502, f"This API key is not allowed to use {self.model!r}. "
                                   "Pick another OPENAI_MODEL in the backend's .env.") from None
        except openai.NotFoundError:
            raise ReportError(502, f"OpenAI does not recognise the model {self.model!r}. "
                                   "Check OPENAI_MODEL in the backend's .env.") from None
        except openai.RateLimitError:
            raise ReportError(429, "OpenAI's rate limit or quota was reached. Try again in "
                                   "a minute, or check the account's billing.") from None
        except (openai.APITimeoutError, openai.APIConnectionError):
            raise ReportError(504, "Could not reach OpenAI. Check the server's internet "
                                   "connection and try again.") from None
        except openai.BadRequestError as e:
            raise ReportError(502, f"OpenAI refused the request: {e.message}") from None
        except openai.APIError as e:
            raise ReportError(502, f"OpenAI returned an error: {e.message}") from None

        if getattr(response, "status", "completed") == "incomplete":
            raise ReportError(502, "The report was cut off before it finished. Try again.")
        try:
            return json.loads(response.output_text)
        except (TypeError, json.JSONDecodeError):
            raise ReportError(502, "The model's reply was not a readable report. "
                                   "Try again.") from None


def get_report_writer() -> ReportWriter | None:
    """The writer in force, or None when no key is configured."""
    settings = get_openai_settings()
    if not settings.openai_api_key:
        return None
    return OpenAIReportWriter(settings.openai_api_key, settings.openai_model)
