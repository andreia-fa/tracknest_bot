"""Token usage tracking for Anthropic API calls.

Wraps client.messages.create() to capture usage metadata per call and
per session, compute remaining context window capacity, and optionally
inject a usage summary back into the model as context.

Milestone reports are printed automatically at 25%, 50%, and 75% context
usage, showing the top token-heavy calls. A warning is added at 50%.
"""

from __future__ import annotations

import anthropic
from dataclasses import dataclass, field

CONTEXT_WINDOW = 1_000_000  # claude-opus-4-7 context window in tokens
MILESTONES = (25, 50, 75)   # context usage % thresholds for reports


@dataclass
class CallUsage:
    """Token counts from a single API response."""

    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    label: str = ""  # short description extracted from the prompt

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class SessionStats:
    """Cumulative token usage across all calls in a session."""

    calls: list[CallUsage] = field(default_factory=list)

    @property
    def total_input(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_cache_read(self) -> int:
        return sum(c.cache_read_input_tokens for c in self.calls)

    @property
    def total_cache_created(self) -> int:
        return sum(c.cache_creation_input_tokens for c in self.calls)

    @property
    def remaining_context(self) -> int:
        """Remaining context window tokens based on cumulative input usage."""
        return CONTEXT_WINDOW - self.total_input

    @property
    def context_used_pct(self) -> float:
        return (self.total_input / CONTEXT_WINDOW) * 100

    def top_spenders(self, n: int = 3) -> list[tuple[int, CallUsage]]:
        """Return the top n calls by total token cost, each paired with its call number.

        Args:
            n: Number of top calls to return.

        Returns:
            List of (call_number, CallUsage) tuples, sorted highest-cost first.
        """
        indexed = sorted(enumerate(self.calls, 1), key=lambda x: x[1].total_tokens, reverse=True)
        return indexed[:n]

    def milestone_report(self, threshold: int) -> str:
        """Format a milestone report for the given context-usage threshold.

        Args:
            threshold: The percentage threshold that was just crossed (25, 50, or 75).

        Returns:
            A formatted multi-line report string.
        """
        lines = []

        if threshold == 50:
            lines += [
                "⚠️  WARNING: 50% of context window consumed!",
                "   Consider resetting the session soon to avoid hitting the limit.",
                "",
            ]

        lines += [
            f"── Milestone reached: {threshold}% context used ──────────────────",
            "  Most expensive calls so far:",
        ]

        for rank, (call_num, call) in enumerate(self.top_spenders(3), 1):
            label_part = f' "{call.label}"' if call.label else ""
            lines.append(
                f"  #{rank}  call #{call_num}{label_part} — "
                f"{call.total_tokens:,} tokens total  "
                f"(in={call.input_tokens:,}  out={call.output_tokens:,})"
            )

        lines.append("─" * 54)
        return "\n".join(lines)

    def summary(self) -> str:
        """Return a human-readable usage summary for the current session."""
        lines = [
            f"── Token usage (call #{len(self.calls)}) ──────────────────",
            f"  Input:          {self.calls[-1].input_tokens:>8,}  tokens",
            f"  Output:         {self.calls[-1].output_tokens:>8,}  tokens",
        ]
        last = self.calls[-1]
        if last.cache_read_input_tokens:
            lines.append(f"  Cache hit:      {last.cache_read_input_tokens:>8,}  tokens")
        if last.cache_creation_input_tokens:
            lines.append(f"  Cache write:    {last.cache_creation_input_tokens:>8,}  tokens")
        lines += [
            "─" * 50,
            f"  Session input:  {self.total_input:>8,}  / {CONTEXT_WINDOW:,}",
            f"  Context used:   {self.context_used_pct:>7.2f}%",
            f"  Remaining:      {self.remaining_context:>8,}  tokens",
            "─" * 50,
        ]
        return "\n".join(lines)

    def context_injection(self) -> str:
        """Return a compact stats string suitable for injecting into a system prompt."""
        return (
            f"[PM-Agent stats] session_input={self.total_input:,} "
            f"session_output={self.total_output:,} "
            f"cache_hits={self.total_cache_read:,} "
            f"remaining_context={self.remaining_context:,}/{CONTEXT_WINDOW:,} "
            f"({100 - self.context_used_pct:.1f}% free)"
        )


def _extract_label(messages: list[dict]) -> str:
    """Extract a short label from the last user message in a conversation.

    Args:
        messages: Conversation turns in Anthropic message format.

    Returns:
        A string of up to 60 characters representing the prompt content.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content.strip().replace("\n", " ")
        elif isinstance(content, list):
            text = " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip().replace("\n", " ")
        else:
            text = str(content)
        return text[:60] + ("…" if len(text) > 60 else "")
    return ""


class TokenTracker:
    """Wraps the Anthropic client to record token usage after each API call.

    Prints a usage summary after every call and a milestone report (with top
    token-heavy prompts) each time context usage crosses 25%, 50%, or 75%.
    A warning is also printed when 50% is reached.

    Args:
        client: An initialised ``anthropic.Anthropic`` client.
        model: Model ID to use for all calls.
        inject_stats: When True, prepends session token stats to the system
            prompt of each subsequent call so the model can self-optimise.
        print_summary: When True, prints the usage summary to stdout after
            each call.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = "claude-opus-4-7",
        inject_stats: bool = False,
        print_summary: bool = True,
    ) -> None:
        self._client = client
        self.model = model
        self.inject_stats = inject_stats
        self.print_summary = print_summary
        self.session = SessionStats()
        self._milestones_hit: set[int] = set()

    def create(self, messages: list[dict], system: str = "", **kwargs) -> anthropic.types.Message:
        """Call messages.create(), record usage, and return the response.

        Automatically prints a usage summary and fires milestone reports at
        25%, 50%, and 75% context usage.

        Args:
            messages: Conversation turns in Anthropic message format.
            system: Optional system prompt. When inject_stats is True, session
                token stats are prepended to this value automatically.
            **kwargs: Any additional keyword arguments forwarded to
                ``client.messages.create()`` (e.g. ``max_tokens``,
                ``thinking``, ``tools``).

        Returns:
            The raw ``Message`` response from the Anthropic API.
        """
        effective_system = system
        if self.inject_stats and self.session.calls:
            stats_line = self.session.context_injection()
            effective_system = f"{stats_line}\n\n{system}" if system else stats_line

        params: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": kwargs.pop("max_tokens", 8096),
            **kwargs,
        }
        if effective_system:
            params["system"] = effective_system

        response = self._client.messages.create(**params)

        usage = response.usage
        call = CallUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            label=_extract_label(messages),
        )
        self.session.calls.append(call)

        if self.print_summary:
            print(self.session.summary())

        for milestone in MILESTONES:
            if milestone not in self._milestones_hit and self.session.context_used_pct >= milestone:
                self._milestones_hit.add(milestone)
                print(self.session.milestone_report(milestone))

        return response

    def reset_session(self) -> None:
        """Clear cumulative session stats and milestone history (start a new tracking window)."""
        self.session = SessionStats()
        self._milestones_hit = set()
