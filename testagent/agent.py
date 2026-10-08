"""The agent: mini-swe-agent's DefaultAgent plus a hard token budget."""

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import FormatError, LimitsExceeded


def response_usage(message: dict) -> tuple[int, int]:
    """Prompt and completion tokens recorded in a model message, or (0, 0)."""
    response = message.get("extra", {}).get("response") or {}
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if not usage:
        return 0, 0
    if not isinstance(usage, dict):
        usage = {"prompt_tokens": getattr(usage, "prompt_tokens", 0), "completion_tokens": getattr(usage, "completion_tokens", 0)}
    return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


class TestingAgent(DefaultAgent):
    """DefaultAgent that stops once the token budget for a problem is used up."""

    __test__ = False

    def __init__(self, model, env, *, token_limit: int = 0, **kwargs):
        super().__init__(model, env, **kwargs)
        self.token_limit = token_limit
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def _count(self, message: dict) -> None:
        prompt, completion = response_usage(message)
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def query(self) -> dict:
        if 0 < self.token_limit <= self.total_tokens:
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": "TokenLimitExceeded",
                    "extra": {"exit_status": "TokenLimitExceeded", "submission": ""},
                }
            )
        try:
            message = super().query()
        except FormatError as error:
            self._count(error.messages[0])
            raise
        self._count(message)
        return message

    def serialize(self, *extra_dicts) -> dict:
        usage = {
            "info": {
                "model_stats": {
                    "prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                    "total_tokens": self.total_tokens,
                    "token_limit": self.token_limit,
                }
            }
        }
        return super().serialize(usage, *extra_dicts)
