"""Refuse real subscription CLIs while the Ticket 07 offline suite runs."""

import os
import shlex
import subprocess
from pathlib import Path


if os.environ.get("HOYA_OFFLINE_PROVIDER_GUARD") == "1":
    _REAL_PROVIDER_PATHS = {
        str((Path.home() / ".local" / "bin" / name).resolve())
        for name in ("codex", "claude", "agy")
    }

    def _tokens(command):
        if isinstance(command, (list, tuple)):
            return [str(item) for item in command]
        if isinstance(command, (str, bytes, os.PathLike)):
            try:
                return shlex.split(os.fsdecode(command))
            except ValueError:
                return [os.fsdecode(command)]
        return []

    def _refuse(command, *, shell=False):
        tokens = _tokens(command)
        candidates = list(tokens)
        if shell or (tokens and Path(tokens[0]).name in ("bash", "sh") and "-c" in tokens):
            scripts = tokens if shell else tokens[tokens.index("-c") + 1:tokens.index("-c") + 2]
            candidates.extend(token for script in scripts for token in _tokens(script))
        for token in candidates:
            try:
                resolved = str(Path(token).expanduser().resolve())
            except OSError:
                resolved = token
            if resolved in _REAL_PROVIDER_PATHS:
                raise FileNotFoundError(
                    "offline acceptance refused real provider CLI: {}".format(
                        Path(resolved).name
                    )
                )

    _ORIGINAL_POPEN = subprocess.Popen

    class _GuardedPopen(_ORIGINAL_POPEN):
        def __init__(self, args, *positional, **keywords):
            _refuse(args, shell=keywords.get("shell") is True)
            super().__init__(args, *positional, **keywords)

    subprocess.Popen = _GuardedPopen

    _original_system = os.system

    def _guarded_system(command):
        _refuse(command, shell=True)
        return _original_system(command)

    os.system = _guarded_system

    def _guard_exec(name):
        original = getattr(os, name, None)
        if original is None:
            return

        def guarded(*args):
            command = args[1] if name.startswith("spawn") and len(args) > 1 else args[0]
            argv = args[2] if name.startswith("spawn") and len(args) > 2 else (args[1] if len(args) > 1 else ())
            _refuse([command, *_tokens(argv)])
            return original(*args)

        setattr(os, name, guarded)

    for _name in (
        "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    ):
        _guard_exec(_name)
