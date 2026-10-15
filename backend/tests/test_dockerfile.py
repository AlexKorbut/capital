"""The backend Dockerfile must stay buildable. `docker build` is not run in CI, and a push to
main deploys, so the one shape that breaks it silently is guarded here: a literal backslash-n
typed instead of a real line continuation (backslash + newline)."""
from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DOCKERFILE = BACKEND / "Dockerfile"
BACKSLASH = chr(92)
INSTRUCTIONS = {
    "FROM", "ARG", "ENV", "RUN", "WORKDIR", "COPY", "ADD", "USER", "EXPOSE", "HEALTHCHECK",
    "CMD", "ENTRYPOINT", "LABEL", "VOLUME", "SHELL", "STOPSIGNAL", "ONBUILD",
}


def _logical_instructions(text: str) -> list[str]:
    """Docker's own reading: a line ending in a backslash continues on the next line, and
    comment lines inside a continuation are dropped."""
    out: list[str] = []
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if current and stripped.startswith("#"):
            continue
        if not current and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith(BACKSLASH):
            current += stripped[:-1].rstrip() + " "
            continue
        out.append((current + stripped).strip())
        current = ""
    if current:
        out.append(current.strip())
    return out


def _runs() -> list[str]:
    return [i for i in _logical_instructions(DOCKERFILE.read_text(encoding="utf-8")) if i.split()[0] == "RUN"]


def test_every_logical_line_starts_with_a_dockerfile_instruction():
    """A broken continuation leaves a stray "&& mkdir ..." line that Docker rejects."""
    for instruction in _logical_instructions(DOCKERFILE.read_text(encoding="utf-8")):
        assert instruction.split()[0].upper() in INSTRUCTIONS, instruction


def test_no_run_instruction_holds_a_literal_backslash_n():
    for run in _runs():
        assert BACKSLASH + "n" not in run, run
        assert BACKSLASH not in run, run  # continuations are consumed; nothing may be left over


def test_the_user_line_is_three_commands_with_the_uploads_folder_made_before_the_chown():
    [run] = [r for r in _runs() if "useradd" in r]

    commands = [part.strip() for part in run[len("RUN "):].split("&&")]

    assert len(commands) == 3, commands
    assert commands[0].startswith("useradd ") and "appuser" in commands[0]
    assert commands[1] == "mkdir -p /app/uploads"
    assert commands[2] == "chown -R appuser:appuser /app"


def test_the_volume_folder_exists_before_the_runtime_user_takes_over():
    instructions = _logical_instructions(DOCKERFILE.read_text(encoding="utf-8"))
    mkdir_at = next(i for i, text in enumerate(instructions) if "mkdir -p /app/uploads" in text)
    user_at = next(i for i, text in enumerate(instructions) if text.split()[0] == "USER")

    assert mkdir_at < user_at
