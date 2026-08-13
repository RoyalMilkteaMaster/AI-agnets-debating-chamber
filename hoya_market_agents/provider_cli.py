"""Where the three provider executables live, according to this WSL shell.

ADR 0009 makes WSL2 Ubuntu the only formal Runtime, so a provider is whatever
the *current* ``PATH`` resolves ``codex``, ``claude`` and ``agy`` to — never a
path frozen into this repository at one developer's home directory. Resolving
is the whole job: nothing here installs, updates, logs in or reads a credential.

A command this shell cannot find is a fact worth stating early. A dispatch that
goes ahead anyway burns a full research timeout before anyone learns the CLI was
never there, which costs the seat its recovery window; so callers ask for
:func:`require_provider_cli` and get a stable
:data:`PROVIDER_CLI_MISSING` failure the moment the answer is "nowhere".

``which`` is the injectable seam. An offline test decides exactly what this WSL
shell can see, and no test has to depend on what happens to be installed.
"""

import shutil
from types import MappingProxyType

CODEX_COMMAND = "codex"
CLAUDE_COMMAND = "claude"
ANTIGRAVITY_COMMAND = "agy"

PROVIDER_CODEX = "codex"
PROVIDER_CLAUDE = "claude"
PROVIDER_ANTIGRAVITY = "antigravity"

#: provider 家族 → 它在 WSL ``PATH`` 上的命令名。這是 roster ``provider`` 欄的投影，
#: 也是全系統唯一一處把「第七席叫 antigravity」對應到「命令叫 agy」的地方。
PROVIDER_COMMANDS = MappingProxyType(
    {
        PROVIDER_CODEX: CODEX_COMMAND,
        PROVIDER_CLAUDE: CLAUDE_COMMAND,
        PROVIDER_ANTIGRAVITY: ANTIGRAVITY_COMMAND,
    }
)

#: Spec R-008 的穩定 failure code；scheduler 以同名常數收下它。
PROVIDER_CLI_MISSING = "provider_cli_missing"


class ProviderCliMissing(RuntimeError):
    """This WSL shell has no executable for one provider."""

    failure_code = PROVIDER_CLI_MISSING

    def __init__(self, provider, command):
        self.provider = provider
        self.command = command
        super().__init__(
            "WSL PATH 上找不到 {} 的 Provider CLI：{}".format(provider, command)
        )


def provider_command(provider):
    """Return the command name one provider family is installed under."""
    try:
        return PROVIDER_COMMANDS[provider]
    except KeyError as exc:
        raise KeyError("未知 provider：{}".format(provider)) from exc


def resolve_provider_cli(provider, which=None):
    """Return the absolute path this WSL ``PATH`` resolves, or ``None``."""
    which = which or shutil.which
    return which(provider_command(provider))


def provider_cli_argv0(provider, which=None):
    """The executable to spawn: the resolved path, else the bare command.

    Falling back to the bare name keeps a missing CLI inside the adapters' own
    process-failure contract instead of turning it into an exception from a
    place that has no failure vocabulary. Callers that want the early, stable
    refusal ask :func:`require_provider_cli` first.
    """
    return resolve_provider_cli(provider, which) or provider_command(provider)


def require_provider_cli(provider, which=None):
    """Return the resolved path, or refuse with a stable ``provider_cli_missing``."""
    path = resolve_provider_cli(provider, which)
    if path is None:
        raise ProviderCliMissing(provider, provider_command(provider))
    return path
