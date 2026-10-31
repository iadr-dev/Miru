"""A shell script that follows redirects with curl must pin the transfer to HTTPS.

`curl -L` follows redirects. Without `--proto '=https' --proto-redir '=https'` a redirect can send
the download to plain HTTP or another protocol. SonarCloud rule shell:S6506 fails the quality
gate on this, so it is checked here, before a push: this suite runs in CI and in the pre-commit
hook. A call that does not follow redirects (a fixed https URL, a loopback probe) is not
affected."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SKIPPED_DIRS = {".git", ".venv", "node_modules", ".next", "worktrees", "__pycache__"}
_CURL = re.compile(r"(?:^|&&|\|\||[;|(`]|\$\(|\b(?:then|do|else)\b)\s*curl\b")
_COMMAND_END = re.compile(r"&&|\|\||[;|)]")


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Source lines with backslash continuations joined, keyed by the first line number."""
    joined: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        if not pending:
            start = number
        if raw.rstrip().endswith("\\"):
            pending.append(raw.rstrip()[:-1])
            continue
        pending.append(raw)
        joined.append((start, " ".join(part.strip() for part in pending)))
        pending = []
    if pending:
        joined.append((start, " ".join(part.strip() for part in pending)))
    return joined


def _tokens(arguments: str) -> list[str]:
    try:
        return shlex.split(arguments)
    except ValueError:
        return arguments.split()


def _follows_redirects(tokens: list[str]) -> bool:
    return any(
        token in {"--location", "--location-trusted"}
        or (token.startswith("-") and not token.startswith("--") and "L" in token[1:])
        for token in tokens
    )


def _pins_https(tokens: list[str], option: str) -> bool:
    return any(
        token == option and index + 1 < len(tokens) and tokens[index + 1] == "=https"
        for index, token in enumerate(tokens)
    )


def unpinned_redirect_lines(text: str) -> list[int]:
    """Line numbers of curl calls that follow redirects without pinning both the request and the
    redirects to HTTPS."""
    flagged: list[int] = []
    for number, line in _logical_lines(text):
        if line.lstrip().startswith("#"):
            continue
        for match in _CURL.finditer(line):
            tail = line[match.end() :]
            end = _COMMAND_END.search(tail)
            tokens = _tokens(tail[: end.start()] if end else tail)
            if _follows_redirects(tokens) and not (
                _pins_https(tokens, "--proto") and _pins_https(tokens, "--proto-redir")
            ):
                flagged.append(number)
    return flagged


def _shell_scripts() -> list[Path]:
    found: list[Path] = []
    for directory, subdirs, files in os.walk(REPO_ROOT):
        subdirs[:] = [name for name in subdirs if name not in _SKIPPED_DIRS]
        found.extend(Path(directory) / name for name in files if name.endswith(".sh"))
    return sorted(found)


def test_every_redirect_following_curl_in_a_shell_script_pins_https() -> None:
    scripts = _shell_scripts()
    assert scripts, "no shell scripts found: the scan root is wrong"
    flagged = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in scripts
        for number in unpinned_redirect_lines(path.read_text(encoding="utf-8"))
    ]
    assert not flagged, (
        "curl follows redirects without `--proto '=https' --proto-redir '=https'`: "
        f"{flagged} (SonarCloud shell:S6506)"
    )


@pytest.mark.parametrize(
    "script",
    [
        'curl -fsSL -o "$OUT" "$URL"',
        'curl --location "$URL"',
        'curl -L "$URL"',
        'curl --proto "=https" -fsSL "$URL"',
        'curl --proto-redir "=https" -fsSL "$URL"',
        "curl -fsSL \\\n  -o out \\\n  https://example.com/x",
        'x="$(curl -sSL https://example.com/x)"',
    ],
)
def test_the_check_flags_a_redirect_following_curl_that_is_not_fully_pinned(script: str) -> None:
    assert unpinned_redirect_lines(script) == [1]


@pytest.mark.parametrize(
    "script",
    [
        "curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL -o out https://example.com/x",
        "curl -fsSL \\\n  --proto '=https' \\\n  --proto-redir '=https' \\\n  https://example.com/x",
        'curl -sS -m 20 "https://api.telegram.org/bot${TOKEN}/setWebhook"',
        "curl -fs http://127.0.0.1:8765/livez",
        '# curl -fsSL "$URL" in a comment',
        'echo "run curl -L to follow redirects"',
    ],
)
def test_the_check_passes_pinned_calls_and_calls_that_do_not_follow_redirects(
    script: str,
) -> None:
    assert unpinned_redirect_lines(script) == []
