"""Ticket 01: 三個 Bash 入口與兩個 Windows 捷徑。

這裡驗的是使用者真的會碰到的東西，而不是腳本裡寫了什麼字：

**入口本身。** 三支 Bash 都通過 ``bash -n``，都從自己的位置推 Code Root 與同層
Data Root，都不寫死任何磁碟、Windows 使用者或 Linux 使用者。

**啟停的行為。** ``START-HERE.sh`` 與 ``STOP-HERE.sh`` 對著真的 loopback listener
跑真的一遍：owned 只開瀏覽器、foreign 零啟動零 shutdown、``active_run=true`` 在沒有
終端機時零 POST。用兩個環境變數當接縫（換 python、換開瀏覽器的命令），所以「沒有
啟動第二台」是「那支假 python 沒有被要求跑 webapp」，不是靠猜的。

**捷徑。** installer 在一個暫存資料夾（不是使用者真的桌面）上跑兩次，檢查最後
精確只有兩個 .lnk、Target 與 Arguments 正確、舊名稱捷徑只在確認得出來是本專案時
才被清掉。別人的 .lnk 原封不動。

PowerShell 不在時整組捷徑測試 skip：這一層量的是 Windows 那一側，在沒有 Windows 的
機器上假裝通過比不跑還糟。
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_runtime_control import CannedListener  # noqa: E402

CODE_ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINTS = ("setup-wsl.sh", "START-HERE.sh", "STOP-HERE.sh")
POWERSHELL_SCRIPTS = ("scripts/install-shortcuts.ps1", "scripts/wsl-shortcut.ps1")

OWNED = {
    "app": "hoya-market-agents-webapp",
    "runtime_owner": "wsl",
    "instance": "instance-under-test",
    "active_run": False,
}


def without_comments(source):
    """PowerShell source with its ``<# … #>`` blocks and ``#`` lines removed.

    A rule the file only *mentions* — "this script does not read /health" — is
    not the rule being implemented here, and a scan that cannot tell the two
    apart makes writing the reason down the thing that breaks the test.
    """
    kept = []
    inside = False
    for line in source.lstrip("﻿").splitlines():
        stripped = line.strip()
        if stripped.startswith("<#"):
            inside = True
        if inside:
            if "#>" in stripped:
                inside = False
            continue
        if stripped.startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def powershell():
    """The Windows PowerShell this machine has, or ``None`` when it has none."""
    found = shutil.which("powershell.exe")
    if found:
        return found
    fallback = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    return str(fallback) if fallback.is_file() else None


# -- the three entry points as files ----------------------------------------


class EntryPointFileTest(unittest.TestCase):
    """They exist, they parse, and they carry nobody's home directory."""

    def source(self, name):
        return (CODE_ROOT / name).read_text(encoding="utf-8")

    def test_the_three_entry_points_are_in_the_code_root(self):
        for name in ENTRY_POINTS:
            self.assertTrue((CODE_ROOT / name).is_file(), name)

    def test_each_entry_point_is_executable(self):
        for name in ENTRY_POINTS:
            mode = (CODE_ROOT / name).stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, name)

    def test_each_entry_point_parses(self):
        for name in ENTRY_POINTS:
            done = subprocess.run(
                ["bash", "-n", str(CODE_ROOT / name)],
                capture_output=True, text=True,
            )
            self.assertEqual(0, done.returncode, "{}: {}".format(name, done.stderr))

    def test_no_entry_point_hard_codes_a_machine_a_drive_or_a_person(self):
        forbidden = ("/home/leslie", "workstationD", "/mnt/d", "/mnt/c/Users",
                     "C:\\Users", "D:\\", "Anaconda", "anaconda")
        for name in ENTRY_POINTS + POWERSHELL_SCRIPTS:
            text = self.source(name)
            for needle in forbidden:
                self.assertNotIn(needle, text, "{} 出現 {}".format(name, needle))

    def test_each_entry_point_derives_the_code_root_from_its_own_location(self):
        for name in ENTRY_POINTS:
            self.assertIn("BASH_SOURCE", self.source(name), name)

    def test_start_and_setup_name_the_sibling_data_root(self):
        for name in ("setup-wsl.sh", "START-HERE.sh"):
            self.assertIn("AI-agnets-debating-chamber_data", self.source(name), name)

    def test_setup_never_runs_an_installer_itself(self):
        """It may *print* an install command. Running one is a different verb.

        The behavioural half of this is :class:`SetupRunTest`, which puts
        recorders on ``PATH`` and proves none of them was reached.
        """
        body = "\n".join(
            line for line in self.source("setup-wsl.sh").splitlines()
            if not line.lstrip().startswith("#")
        )

        for forbidden in ("-m venv", "virtualenv", "$(curl", "`curl"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_the_retired_windows_native_entry_points_are_gone(self):
        for name in ("start-webapp.ps1", "stop-webapp.ps1", "webapp-common.ps1"):
            self.assertFalse((CODE_ROOT / "scripts" / name).exists(), name)

    def test_the_shortcut_wrapper_carries_no_ownership_rule_and_no_provider(self):
        """It hides a window and calls Bash. Everything else would be a second copy."""
        body = without_comments(self.source("scripts/wsl-shortcut.ps1"))

        for forbidden in ("ConvertFrom-Json", "/health", "expect_instance",
                          "expect_runtime", "Invoke-WebRequest", "TcpClient",
                          "python3", "codex", "claude", "agy"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_the_shortcut_wrapper_calls_wsl_and_the_root_entry_points(self):
        text = self.source("scripts/wsl-shortcut.ps1")

        self.assertIn("wsl.exe", text)
        self.assertIn("START-HERE.sh", text)
        self.assertIn("STOP-HERE.sh", text)


class ReadmeTest(unittest.TestCase):
    """One path, in order, with nobody's machine and no internal vocabulary in it."""

    def setUp(self):
        self.readme = (CODE_ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_hero_image_and_its_reference_are_both_still_there(self):
        self.assertIn("![AI agnets debating chamber](docs/assets/readme-hero.png)",
                      self.readme)
        self.assertTrue((CODE_ROOT / "docs/assets/readme-hero.png").is_file())

    def test_the_teaching_order_is_the_one_the_spec_fixes(self):
        steps = (
            "wsl --install -d Ubuntu",
            "重新開機",
            "git clone",
            "bash setup-wsl.sh",
            "https://chatgpt.com/codex/install.sh",
            "./START-HERE.sh",
            "MobaXterm",
        )
        found = [self.readme.index(step) for step in steps]

        self.assertEqual(sorted(found), found, steps)

    def test_every_command_block_says_which_machine_it_is_for(self):
        blocks = self.readme.split("```")[1::2]

        self.assertTrue(blocks)
        for block in blocks:
            self.assertTrue(
                "[Windows]" in block or "[WSL／Ubuntu]" in block, block[:80]
            )

    def test_no_developer_machine_and_no_internal_vocabulary_survives(self):
        for forbidden in ("D:\\workstationD", "/mnt/d", "/home/leslie", "Anaconda",
                          "anaconda", "READY", "preflight", "Codex Task",
                          "competition_ready", "verify-run", "--provider-mode"):
            self.assertNotIn(forbidden, self.readme, forbidden)

    def test_the_git_remote_it_teaches_is_the_official_one(self):
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(CODE_ROOT), capture_output=True, text=True,
        )
        if remote.returncode != 0:
            self.skipTest("這個工作樹讀不到 git remote")
        self.assertIn(remote.stdout.strip(), self.readme)

    def test_the_three_provider_commands_come_from_their_official_documentation(self):
        for command, source in (
            ("curl -fsSL https://chatgpt.com/codex/install.sh | sh",
             "https://developers.openai.com/codex/cli"),
            ("curl -fsSL https://claude.ai/install.sh | bash",
             "https://code.claude.com/docs/en/quickstart"),
            ("curl -fsSL https://antigravity.google/cli/install.sh | bash",
             "https://antigravity.google/docs/cli/install"),
        ):
            self.assertIn(command, self.readme, command)
            self.assertIn(source, self.readme, source)

    def test_it_names_where_the_log_and_the_runs_are(self):
        self.assertIn("logs/webapp.jsonl", self.readme)
        self.assertIn("8765", self.readme)


# -- what the two entry points do --------------------------------------------


class EntryPointRunFixture:
    """Run a real entry point against a real listener, with two seams replaced.

    ``HOYA_PYTHON`` is a recorder that runs the real interpreter for the
    ownership module and refuses everything else, so "沒有啟動第二台" is a line
    that is absent from a file rather than an absence someone asserted.
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.python_log = self.tmp / "python-calls.txt"
        self.browser_log = self.tmp / "browser-calls.txt"
        self.fake_python = self.write_script("fake-python", """
            printf '%s\\n' "$*" >> "{log}"
            case "$*" in
                *runtime_control*) exec {real} "$@" ;;
            esac
            exit 0
        """.format(log=self.python_log, real=sys.executable))
        self.fake_browser = self.write_script("fake-browser", """
            printf '%s\\n' "$1" >> "{log}"
        """.format(log=self.browser_log))

    def write_script(self, name, body):
        path = self.tmp / name
        path.write_text("#!/usr/bin/env bash\n" + body.strip() + "\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def listening(self, payload=None, **overrides):
        body = json.dumps(dict(payload or OWNED, **overrides))
        listener = CannedListener(body=body)
        self.addCleanup(listener.close)
        return listener

    def run_entry(self, name, port, *arguments, stdin=subprocess.DEVNULL):
        environment = dict(os.environ)
        environment.update({
            "HOYA_PYTHON": str(self.fake_python),
            "HOYA_OPEN_URL": str(self.fake_browser),
            "HOYA_PORT": str(port),
        })
        return subprocess.run(
            [str(CODE_ROOT / name), *arguments],
            capture_output=True, text=True, env=environment, stdin=stdin, timeout=60,
        )

    def python_calls(self):
        if not self.python_log.is_file():
            return []
        return self.python_log.read_text(encoding="utf-8").splitlines()

    def started_a_server(self):
        return [line for line in self.python_calls() if "hoya_market_agents webapp" in line]

    def browser_calls(self):
        if not self.browser_log.is_file():
            return []
        return self.browser_log.read_text(encoding="utf-8").splitlines()


class StartHereTest(EntryPointRunFixture, unittest.TestCase):
    """Reuse an owned listener; refuse a foreign one without touching it."""

    def test_an_owned_listener_is_reused_and_only_the_browser_is_opened(self):
        listener = self.listening()

        done = self.run_entry("START-HERE.sh", listener.port)

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual([], self.started_a_server())
        self.assertEqual(
            ["http://127.0.0.1:{}/".format(listener.port)], self.browser_calls()
        )

    def test_a_foreign_listener_stops_the_start_with_one_line_and_no_browser(self):
        listener = self.listening(runtime_owner="windows")

        done = self.run_entry("START-HERE.sh", listener.port)

        self.assertNotEqual(0, done.returncode)
        self.assertEqual([], self.started_a_server())
        self.assertEqual([], self.browser_calls())
        self.assertIn("沒有啟動", done.stdout)

    def test_a_listener_that_is_not_this_app_is_refused_the_same_way(self):
        listener = self.listening(app="something-else")

        done = self.run_entry("START-HERE.sh", listener.port)

        self.assertNotEqual(0, done.returncode)
        self.assertEqual([], self.started_a_server())

    def test_a_start_never_changes_port_when_the_port_is_taken(self):
        listener = self.listening(app="something-else")

        done = self.run_entry("START-HERE.sh", listener.port)

        self.assertNotIn("--port", "\n".join(self.started_a_server()))
        self.assertIn(str(listener.port), done.stdout + done.stderr)

    def test_a_free_port_makes_it_reach_for_the_webapp(self):
        """The fake interpreter exits at once, so this ends without waiting."""
        listener = self.listening()
        port = listener.port
        listener.close()

        done = self.run_entry("START-HERE.sh", port)

        self.assertEqual(1, len(self.started_a_server()), self.python_calls())
        self.assertNotEqual(0, done.returncode)

    def test_the_data_root_it_hands_the_webapp_is_the_sibling_one(self):
        listener = self.listening()
        port = listener.port
        listener.close()

        self.run_entry("START-HERE.sh", port)

        expected = str(CODE_ROOT.parent / "AI-agnets-debating-chamber_data")
        self.assertIn(expected, "\n".join(self.started_a_server()))


class StopHereTest(EntryPointRunFixture, unittest.TestCase):
    """Stop the instance that just answered, and nothing else, ever."""

    def posts(self, listener):
        return [request for request in listener.requests if request[0] == "POST"]

    def test_an_owned_idle_listener_is_stopped_with_both_precondition_fields(self):
        listener = self.listening()

        done = self.run_entry("STOP-HERE.sh", listener.port)

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, len(self.posts(listener)))
        self.assertEqual(
            {"expect_runtime": ["wsl"], "expect_instance": ["instance-under-test"]},
            parse_qs(self.posts(listener)[0][2]),
        )

    def test_a_foreign_listener_is_never_posted_to(self):
        listener = self.listening(runtime_owner="windows")

        done = self.run_entry("STOP-HERE.sh", listener.port)

        self.assertNotEqual(0, done.returncode)
        self.assertEqual([], self.posts(listener))
        self.assertIn("沒有關閉", done.stdout)

    def test_a_free_port_is_reported_rather_than_treated_as_a_failure(self):
        listener = self.listening()
        port = listener.port
        listener.close()

        done = self.run_entry("STOP-HERE.sh", port)

        self.assertEqual(0, done.returncode, done.stderr)

    def test_an_active_run_is_not_stopped_when_nobody_can_be_asked(self):
        listener = self.listening(active_run=True)

        done = self.run_entry("STOP-HERE.sh", listener.port)

        self.assertNotEqual(0, done.returncode)
        self.assertEqual([], self.posts(listener))

    def test_an_active_run_with_no_terminal_exits_with_its_own_code(self):
        """A code the hidden Windows wrapper can tell apart from every failure.

        "有分析在跑，我這裡問不到人" is not "關不掉", and a wrapper that could not
        tell the two apart would either never ask or ask after a real failure.
        The number is the contract between this script and ``wsl-shortcut.ps1``.
        """
        listener = self.listening(active_run=True)

        done = self.run_entry("STOP-HERE.sh", listener.port)

        self.assertEqual(10, done.returncode)
        self.assertEqual([], self.posts(listener))

    def test_a_foreign_listener_does_not_produce_the_confirmation_code(self):
        """Otherwise the shortcut would offer to interrupt somebody else's program."""
        listener = self.listening(runtime_owner="windows")

        done = self.run_entry("STOP-HERE.sh", listener.port)

        self.assertNotEqual(10, done.returncode)
        self.assertNotEqual(0, done.returncode)

    def test_an_active_run_is_stopped_once_it_has_been_agreed_to(self):
        listener = self.listening(active_run=True)

        done = self.run_entry("STOP-HERE.sh", listener.port, "--yes")

        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, len(self.posts(listener)))

    def test_an_agreed_stop_carries_the_consent_the_server_re_checks_against(self):
        """``--yes`` is the whole reason the server may interrupt a run."""
        listener = self.listening(active_run=True)

        self.run_entry("STOP-HERE.sh", listener.port, "--yes")

        self.assertEqual(
            {"expect_runtime": ["wsl"], "expect_instance": ["instance-under-test"],
             "allow_active_run": ["yes"]},
            parse_qs(self.posts(listener)[0][2]),
        )

    def test_a_stop_nobody_was_asked_about_carries_no_consent(self):
        """An idle probe is not agreement; it is the state a second ago."""
        listener = self.listening()

        self.run_entry("STOP-HERE.sh", listener.port)

        self.assertNotIn("allow_active_run", parse_qs(self.posts(listener)[0][2]))

    def test_declining_at_the_prompt_sends_nothing_at_all(self):
        listener = self.listening(active_run=True)

        self.run_with_answer("STOP-HERE.sh", listener.port, "n\n")

        self.assertEqual([], self.posts(listener))

    def test_agreeing_at_the_prompt_is_what_puts_consent_in_the_body(self):
        listener = self.listening(active_run=True)

        self.run_with_answer("STOP-HERE.sh", listener.port, "y\n")

        self.assertEqual(
            ["yes"], parse_qs(self.posts(listener)[0][2])["allow_active_run"]
        )

    def test_declining_the_question_leaves_the_server_running(self):
        listener = self.listening(active_run=True)

        done = self.run_with_answer("STOP-HERE.sh", listener.port, "n\n")

        self.assertEqual([], self.posts(listener))
        self.assertIn("已取消", done.stdout)

    def test_agreeing_to_the_question_sends_exactly_one_stop(self):
        listener = self.listening(active_run=True)

        self.run_with_answer("STOP-HERE.sh", listener.port, "y\n")

        self.assertEqual(1, len(self.posts(listener)))

    def run_with_answer(self, name, port, answer):
        """Answer the confirmation on a pseudo-terminal, as a person would."""
        import pty

        primary, secondary = pty.openpty()
        try:
            environment = dict(os.environ)
            environment.update({
                "HOYA_PYTHON": str(self.fake_python),
                "HOYA_OPEN_URL": str(self.fake_browser),
                "HOYA_PORT": str(port),
            })
            os.write(primary, answer.encode("utf-8"))
            return subprocess.run(
                [str(CODE_ROOT / name)],
                capture_output=True, text=True, env=environment,
                stdin=secondary, timeout=60,
            )
        finally:
            os.close(primary)
            os.close(secondary)

    def test_an_unknown_argument_is_refused_before_anything_is_probed(self):
        listener = self.listening()

        done = self.run_entry("STOP-HERE.sh", listener.port, "--force")

        self.assertEqual(2, done.returncode)
        self.assertEqual([], listener.requests)


# -- the two Windows shortcuts ------------------------------------------------


@unittest.skipIf(powershell() is None, "這台機器沒有 Windows PowerShell")
class ShortcutInstallerTest(unittest.TestCase):
    """A real installer run, on a desktop this test made up."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.desktop = self.tmp / "Desktop"
        self.legacy = self.tmp / "OldWorkspace"
        self.desktop.mkdir()
        self.legacy.mkdir()
        self.powershell = powershell()

    def windows(self, path):
        return subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
        ).stdout.strip()

    def install(self, distro="Ubuntu", code_root="/home/somebody/project"):
        return subprocess.run(
            [
                self.powershell, "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", self.windows(CODE_ROOT / "scripts" / "install-shortcuts.ps1"),
                "-ShortcutScript", self.windows(CODE_ROOT / "scripts" / "wsl-shortcut.ps1"),
                "-Distro", distro,
                "-CodeRoot", code_root,
                "-DesktopPath", self.windows(self.desktop),
                "-LegacyShortcutDir", self.windows(self.legacy),
            ],
            capture_output=True, text=True, errors="replace", timeout=180,
        )

    def links(self, directory):
        return sorted(path.name for path in directory.iterdir() if path.suffix == ".lnk")

    def read_link(self, path):
        script = (
            "$s = New-Object -ComObject WScript.Shell; "
            "$l = $s.CreateShortcut('{}'); "
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "Write-Output $l.TargetPath; Write-Output $l.Arguments; "
            "Write-Output $l.WindowStyle"
        ).format(self.windows(path))
        done = subprocess.run(
            [self.powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=120,
            check=True,
        )
        target, arguments, window = done.stdout.strip().splitlines()[:3]
        return target, arguments, window

    def write_link(self, path, arguments, target="C:\\Windows\\System32\\cmd.exe"):
        script = (
            "$s = New-Object -ComObject WScript.Shell; "
            "$l = $s.CreateShortcut('{}'); "
            "$l.TargetPath = '{}'; "
            "$l.Arguments = '{}'; $l.Save()"
        ).format(self.windows(path), target, arguments)
        subprocess.run(
            [self.powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=120,
            check=True,
        )

    def write_owned_link(self, path, script_under_legacy, extra=" -Shortcut"):
        """A shortcut this project can *prove* is its own.

        Three things make it provable and all three are checked: it runs
        ``powershell.exe``, its ``-File`` is a rooted path, and that path is one
        of this project's own entry scripts sitting under a directory the
        installer was told about. A fixture that satisfies only the file name is
        the thing :meth:`test_a_shortcut_whose_script_lives_somewhere_else_is_not_ours`
        keeps out.
        """
        full = "{}\\{}".format(self.windows(self.legacy), script_under_legacy)
        self.write_link(
            path,
            '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass '
            '-File \"{}\"{}'.format(full, extra),
            target=self.powershell_windows_path(),
        )

    def powershell_windows_path(self):
        return "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"

    def foreign_powershell(self):
        """A ``powershell.exe`` that is not the one this installer runs.

        Same file name, same directory layout, different machine-wide location.
        It exists so the target check has to compare the whole path: a shortcut
        pointing at a ``powershell.exe`` somebody dropped in a temp directory is
        the shape a hijack actually takes.
        """
        fake = self.tmp / "ForeignWindows" / "System32" / "WindowsPowerShell" / "v1.0"
        fake.mkdir(parents=True, exist_ok=True)
        binary = fake / "powershell.exe"
        binary.write_bytes(b"")
        return self.windows(binary)

    def sibling_arguments(self, action="start"):
        """A sibling project's wrapper, under the very same parent directory.

        This is the shape the parent-prefix rule could not tell from our own: same
        parent, same ``scripts\\wsl-shortcut.ps1`` layout, same system PowerShell,
        the same ``-Action`` a real shortcut carries. The only thing that makes it
        somebody else's is *which project directory it is in* — so nothing short
        of naming the exact allowed paths can keep our installer off it.
        """
        sibling = self.legacy / "SomebodyElsesProject" / "scripts"
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "wsl-shortcut.ps1").write_text("# theirs\n", encoding="utf-8")
        full = "{}\\wsl-shortcut.ps1".format(self.windows(sibling))
        return (
            '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass '
            '-File \"{}\" -Action {} -Distro \"Ubuntu\" -CodeRoot \"/home/me/theirs\"'
        ).format(full, action)

    def convincing_arguments(self, action="start"):
        """Arguments that pass every check except the one about the target."""
        full = "{}\\scripts\\wsl-shortcut.ps1".format(self.windows(self.legacy))
        return (
            '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass '
            '-File \"{}\" -Action {} -Distro \"Ubuntu\" -CodeRoot \"/home/me/p\"'
        ).format(full, action)

    def test_the_desktop_ends_up_with_exactly_the_two_project_shortcuts(self):
        done = self.install()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(sorted(["開啟辯論室.lnk", "關閉辯論室.lnk"]), self.links(self.desktop))

    def test_running_it_twice_adds_nothing(self):
        self.install()
        first = self.links(self.desktop)
        details = [self.read_link(self.desktop / name) for name in first]

        self.install()

        self.assertEqual(first, self.links(self.desktop))
        self.assertEqual(
            details, [self.read_link(self.desktop / name) for name in first]
        )

    def test_each_shortcut_runs_powershell_on_the_one_shared_wrapper(self):
        self.install(distro="Ubuntu-24.04", code_root="/home/somebody/project")

        for name, action in (("開啟辯論室.lnk", "start"), ("關閉辯論室.lnk", "stop")):
            target, arguments, window = self.read_link(self.desktop / name)
            self.assertTrue(target.lower().endswith("powershell.exe"), target)
            self.assertIn("wsl-shortcut.ps1", arguments)
            self.assertIn("-Action {}".format(action), arguments)
            self.assertIn('-Distro "Ubuntu-24.04"', arguments)
            self.assertIn('-CodeRoot "/home/somebody/project"', arguments)
            self.assertIn("-WindowStyle Hidden", arguments)
            self.assertEqual("7", window)

    def test_the_old_wsl_prefixed_shortcuts_are_removed_from_the_desktop(self):
        for name in ("WSL 開啟辯論室.lnk", "WSL 關閉辯論室.lnk"):
            self.write_owned_link(self.desktop / name, "scripts\\wsl-open-webapp.ps1")

        done = self.install()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(sorted(["開啟辯論室.lnk", "關閉辯論室.lnk"]), self.links(self.desktop))

    def test_the_old_workspace_shortcuts_are_removed_when_they_are_ours(self):
        for name in ("開啟辯論室.lnk", "關閉辯論室.lnk",
                     "WSL 開啟辯論室.lnk", "WSL 關閉辯論室.lnk"):
            self.write_owned_link(self.legacy / name, "scripts\\start-webapp.ps1")

        self.install()

        self.assertEqual([], self.links(self.legacy))

    def test_every_windows_entry_this_project_ever_shipped_counts_as_ours(self):
        """The retired entry names, at the two places this project ever put them."""
        older = {
            "開啟辯論室.lnk": "START-HERE.ps1",
            "關閉辯論室.lnk": "STOP-HERE.ps1",
            "WSL 開啟辯論室.lnk": "scripts\\wsl-open-webapp.ps1",
            "WSL 關閉辯論室.lnk": "scripts\\stop-webapp.ps1",
        }
        for name, script in older.items():
            self.write_owned_link(self.legacy / name, script)

        self.install()

        self.assertEqual([], self.links(self.legacy))

    def test_a_shortcut_that_is_not_ours_is_left_alone_even_under_a_name_we_use(self):
        """Name is the first question. What it runs is the one that decides."""
        stranger = self.desktop / "WSL 開啟辯論室.lnk"
        self.write_link(stranger, "-File C:\\somebody\\else\\thing.ps1")

        self.install()

        self.assertTrue(stranger.is_file())

    # -- ownership is proved, not guessed from a file name ------------------

    def test_a_shortcut_whose_script_lives_somewhere_else_is_not_ours(self):
        """The base name is this project's. The path it names is not.

        A ``.lnk`` running ``C:\\somebody\\else\\wsl-shortcut.ps1`` is somebody
        else's shortcut that happens to have chosen the same file name, and the
        only way to tell is the whole path.
        """
        stranger = self.legacy / "WSL 開啟辯論室.lnk"
        self.write_link(
            stranger,
            '-NoProfile -File "C:\\somebody\\else\\wsl-shortcut.ps1" -Action start',
            target=self.powershell_windows_path(),
        )

        self.install()

        self.assertTrue(stranger.is_file())

    def test_a_shortcut_that_does_not_run_powershell_is_not_ours(self):
        """Every entry this project ever shipped is started by ``powershell.exe``."""
        stranger = self.legacy / "WSL 關閉辯論室.lnk"
        full = "{}\\scripts\\wsl-shortcut.ps1".format(self.windows(self.legacy))
        self.write_link(stranger, '-File "{}" -Action stop'.format(full),
                        target="C:\\Windows\\System32\\cmd.exe")

        self.install()

        self.assertTrue(stranger.is_file())

    def test_a_wrapper_shortcut_without_an_action_is_not_ours(self):
        """This project never writes ``wsl-shortcut.ps1`` without ``-Action``."""
        stranger = self.legacy / "開啟辯論室.lnk"
        full = "{}\\scripts\\wsl-shortcut.ps1".format(self.windows(self.legacy))
        self.write_link(stranger, '-File "{}"'.format(full),
                        target=self.powershell_windows_path())

        self.install()

        self.assertTrue(stranger.is_file())

    # -- a fixed name this installer cannot prove is its own ----------------

    def test_a_foreign_shortcut_under_a_fixed_name_is_never_overwritten(self):
        """Writing over it would destroy something this installer cannot identify."""
        stranger = self.desktop / "開啟辯論室.lnk"
        self.write_link(stranger, "-File C:\\somebody\\else\\thing.ps1")
        before = self.read_link(stranger)

        self.install()

        self.assertEqual(before, self.read_link(stranger))

    def test_a_foreign_shortcut_under_a_fixed_name_fails_the_install(self):
        self.write_link(self.desktop / "關閉辯論室.lnk",
                        "-File C:\\somebody\\else\\thing.ps1")

        done = self.install()

        self.assertNotEqual(0, done.returncode)

    def test_a_refused_install_writes_no_shortcut_at_all(self):
        """Fail closed means neither name is written, not one of the two."""
        self.write_link(self.desktop / "關閉辯論室.lnk",
                        "-File C:\\somebody\\else\\thing.ps1")

        self.install()

        self.assertEqual(["關閉辯論室.lnk"], self.links(self.desktop))

    def test_a_refused_install_removes_no_legacy_shortcut_either(self):
        self.write_owned_link(self.legacy / "WSL 開啟辯論室.lnk",
                              "scripts\\wsl-open-webapp.ps1")
        self.write_link(self.desktop / "開啟辯論室.lnk",
                        "-File C:\\somebody\\else\\thing.ps1")

        self.install()

        self.assertEqual(["WSL 開啟辯論室.lnk"], self.links(self.legacy))

    # -- the target is a whole path, not a file name -------------------------

    def test_a_fixed_name_running_another_powershell_is_not_ours(self):
        """``powershell.exe`` is a file name. Which one ran is the whole path.

        Everything else about this shortcut is convincing — an absolute ``-File``
        under a directory the installer was told about, one of this project's own
        script names, the ``-Action`` the wrapper always carries. The only thing
        wrong with it is the binary it starts, and that has to be enough.
        """
        stranger = self.desktop / "開啟辯論室.lnk"
        self.write_link(stranger, self.convincing_arguments(),
                        target=self.foreign_powershell())

        done = self.install()

        self.assertEqual(3, done.returncode, done.stdout + done.stderr)

    def test_that_shortcut_is_left_byte_for_byte_alone(self):
        stranger = self.desktop / "開啟辯論室.lnk"
        self.write_link(stranger, self.convincing_arguments(),
                        target=self.foreign_powershell())
        before_bytes = stranger.read_bytes()
        before_fields = self.read_link(stranger)

        self.install()

        self.assertEqual(before_bytes, stranger.read_bytes())
        self.assertEqual(before_fields, self.read_link(stranger))

    def test_that_refusal_writes_nothing_and_deletes_nothing(self):
        """Fail closed reaches both halves: no second shortcut, no cleanup."""
        self.write_link(self.desktop / "開啟辯論室.lnk", self.convincing_arguments(),
                        target=self.foreign_powershell())
        self.write_owned_link(self.legacy / "WSL 開啟辯論室.lnk",
                              "scripts\\wsl-open-webapp.ps1")

        self.install()

        self.assertEqual(["開啟辯論室.lnk"], self.links(self.desktop))
        self.assertEqual(["WSL 開啟辯論室.lnk"], self.links(self.legacy))

    def test_a_legacy_entry_running_another_powershell_is_kept(self):
        """The same rule, pointed the other way: it is not ours, so it stays."""
        stranger = self.legacy / "WSL 關閉辯論室.lnk"
        self.write_link(stranger, self.convincing_arguments(action="stop"),
                        target=self.foreign_powershell())
        before = self.read_link(stranger)

        done = self.install()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(stranger.is_file())
        self.assertEqual(before, self.read_link(stranger))

    def test_the_installers_own_powershell_still_counts_as_ours(self):
        """The check is equality, not suspicion: our own path must still pass."""
        self.write_link(self.legacy / "WSL 開啟辯論室.lnk",
                        self.convincing_arguments(),
                        target=self.powershell_windows_path())

        self.install()

        self.assertEqual([], self.links(self.legacy))

    # -- a sibling project under the same parent is somebody else ------------

    def test_a_legacy_entry_belonging_to_a_sibling_project_is_kept(self):
        """Sharing a parent directory is not evidence of sharing a project."""
        stranger = self.legacy / "WSL 開啟辯論室.lnk"
        self.write_link(stranger, self.sibling_arguments(),
                        target=self.powershell_windows_path())
        before = self.read_link(stranger)

        done = self.install()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertTrue(stranger.is_file())
        self.assertEqual(before, self.read_link(stranger))

    def test_a_fixed_name_pointing_at_a_sibling_project_fails_closed(self):
        stranger = self.desktop / "開啟辯論室.lnk"
        self.write_link(stranger, self.sibling_arguments(),
                        target=self.powershell_windows_path())
        before_bytes = stranger.read_bytes()
        before_fields = self.read_link(stranger)

        done = self.install()

        self.assertNotEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(before_bytes, stranger.read_bytes())
        self.assertEqual(before_fields, self.read_link(stranger))

    def test_a_sibling_refusal_writes_nothing_and_deletes_nothing(self):
        self.write_link(self.desktop / "開啟辯論室.lnk", self.sibling_arguments(),
                        target=self.powershell_windows_path())
        self.write_owned_link(self.legacy / "WSL 開啟辯論室.lnk",
                              "scripts\\wsl-open-webapp.ps1")

        self.install()

        self.assertEqual(["開啟辯論室.lnk"], self.links(self.desktop))
        self.assertEqual(["WSL 開啟辯論室.lnk"], self.links(self.legacy))

    def test_a_script_in_a_sibling_of_the_code_root_is_not_ours_either(self):
        """The Code Root's neighbours are not the Code Root."""
        stranger = self.legacy / "WSL 關閉辯論室.lnk"
        neighbour = self.tmp / "NextDoor" / "scripts"
        neighbour.mkdir(parents=True, exist_ok=True)
        (neighbour / "stop-webapp.ps1").write_text("# theirs\n", encoding="utf-8")
        self.write_link(
            stranger,
            '-NoProfile -File "{}\\stop-webapp.ps1"'.format(self.windows(neighbour)),
            target=self.powershell_windows_path(),
        )

        self.install()

        self.assertTrue(stranger.is_file())

    def test_the_shortcuts_this_installer_wrote_are_provably_its_own(self):
        """Which is what makes a second run an overwrite rather than a refusal."""
        self.install()

        done = self.install()

        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(sorted(["開啟辯論室.lnk", "關閉辯論室.lnk"]), self.links(self.desktop))

    def test_nothing_else_on_that_desktop_is_touched(self):
        other = self.desktop / "使用者自己的東西.lnk"
        self.write_link(other, "-File C:\\somebody\\else\\thing.ps1")
        note = self.desktop / "note.txt"
        note.write_text("keep me", encoding="utf-8")

        self.install()

        self.assertTrue(other.is_file())
        self.assertEqual("keep me", note.read_text(encoding="utf-8"))


@unittest.skipIf(powershell() is None, "這台機器沒有 Windows PowerShell")
@unittest.skipIf(not os.environ.get("WSL_DISTRO_NAME"), "讀不到 WSL_DISTRO_NAME")
class ShortcutConfirmationTest(EntryPointRunFixture, unittest.TestCase):
    """關閉捷徑遇到進行中的分析時，那個 Yes/No 是真的問得出來的。

    捷徑是隱藏視窗，所以互動 Bash 的提問沒有人看得到。這一層驗的是替代路徑：
    ``STOP-HERE.sh`` 用一個可辨識的退出碼說「有分析在跑，我這裡問不到人」，薄殼
    看到那個碼才彈一個最小的 Yes/No，而且只有 Yes 會重新呼叫同一支
    ``STOP-HERE.sh --yes``。薄殼自己不 POST、不讀 /health、不解析 JSON。

    這裡跑的是真的 ``wsl.exe``、真的 ``wsl-shortcut.ps1`` 與真的 ``STOP-HERE.sh``，
    對著一個假的 listener。沒有真的 webapp，也沒有碰到任何真的桌面。
    """

    #: The answer is handed over on the command line, never through the
    #: environment. That is not a preference: this process is inside WSL and the
    #: wrapper runs on Windows, so an environment variable only crosses if it is
    #: also listed in ``WSLENV`` — and the run where it was not listed opened a
    #: real dialog box on a real desktop and sat there waiting for a person.
    ANSWERS = ("yes", "no")

    def run_shortcut(self, port, answer):
        # Fail fast, before anything is started: a test that cannot say what the
        # answer is must not be allowed to ask a human for it.
        assert answer in self.ANSWERS, (
            "確認框的答案必須明確給 yes 或 no，否則這個測試會彈出真的對話框"
        )
        environment = dict(os.environ)
        environment.update({
            "HOYA_PORT": str(port),
            # 這一個非跨進 WSL 不可，否則 STOP-HERE.sh 會去問真的 8765。沒有旗標＝
            # 原樣傳過去；``/p`` 會被當成路徑翻譯，而這是一個 port。
            "WSLENV": "HOYA_PORT",
        })
        return subprocess.run(
            [
                powershell(), "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", self.windows(CODE_ROOT / "scripts" / "wsl-shortcut.ps1"),
                "-Action", "stop",
                "-Distro", os.environ["WSL_DISTRO_NAME"],
                "-CodeRoot", str(CODE_ROOT),
                "-ConfirmAnswer", answer,
            ],
            capture_output=True, text=True, errors="replace",
            env=environment, timeout=120,
        )

    def test_the_wrapper_refuses_an_answer_it_was_not_given(self):
        """``-ConfirmAnswer`` takes yes or no. Anything else stops it at the door.

        This is the guard that keeps an automated run off a dialog box: the
        parameter is validated by PowerShell itself, so a typo is a non-zero exit
        rather than a window nobody is there to close.
        """
        done = subprocess.run(
            [
                powershell(), "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", self.windows(CODE_ROOT / "scripts" / "wsl-shortcut.ps1"),
                "-Action", "stop",
                "-Distro", os.environ["WSL_DISTRO_NAME"],
                "-CodeRoot", str(CODE_ROOT),
                "-ConfirmAnswer", "maybe",
            ],
            capture_output=True, text=True, errors="replace", timeout=120,
        )

        self.assertNotEqual(0, done.returncode)

    def test_the_answer_never_travels_through_the_environment(self):
        """Because an environment variable is exactly what failed to arrive once."""
        source = (CODE_ROOT / "scripts" / "wsl-shortcut.ps1").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("$env:HOYA_SHORTCUT_CONFIRM", source)
        self.assertIn("$ConfirmAnswer", source)

    def windows(self, path):
        return subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
        ).stdout.strip()

    def posts(self, listener):
        return [request for request in listener.requests if request[0] == "POST"]

    def test_answering_no_sends_no_shutdown_at_all(self):
        listener = self.listening(active_run=True)

        done = self.run_shortcut(listener.port, "no")

        self.assertEqual([], self.posts(listener))
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_answering_yes_sends_exactly_one_stop_carrying_the_precondition(self):
        listener = self.listening(active_run=True)

        done = self.run_shortcut(listener.port, "yes")

        self.assertEqual(1, len(self.posts(listener)), done.stdout + done.stderr)
        self.assertEqual(
            {"expect_runtime": ["wsl"], "expect_instance": ["instance-under-test"],
             "allow_active_run": ["yes"]},
            parse_qs(self.posts(listener)[0][2]),
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_an_idle_runtime_is_stopped_without_asking_anything(self):
        listener = self.listening()

        done = self.run_shortcut(listener.port, "no")

        self.assertEqual(1, len(self.posts(listener)), done.stdout + done.stderr)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)

    def test_answering_yes_is_what_puts_consent_in_the_body(self):
        """The Yes travels all the way down: dialog → ``--yes`` → the POST body."""
        listener = self.listening(active_run=True)

        self.run_shortcut(listener.port, "yes")

        self.assertEqual(
            ["yes"], parse_qs(self.posts(listener)[0][2])["allow_active_run"]
        )

    def test_a_shortcut_stop_of_an_idle_runtime_carries_no_consent(self):
        listener = self.listening()

        self.run_shortcut(listener.port, "no")

        self.assertNotIn("allow_active_run", parse_qs(self.posts(listener)[0][2]))

    def test_a_foreign_listener_is_never_asked_about_and_never_posted_to(self):
        listener = self.listening(runtime_owner="windows")

        done = self.run_shortcut(listener.port, "yes")

        self.assertEqual([], self.posts(listener))
        self.assertNotEqual(0, done.returncode)


@unittest.skipIf(powershell() is None, "這台機器沒有 Windows PowerShell")
class SetupRunTest(unittest.TestCase):
    """``bash setup-wsl.sh``, twice, against a desktop this test made up.

    Nothing here is a stand-in for the installer: the real PowerShell runs, and
    the real ``.lnk`` files are written. The two things replaced are where the
    desktop is and what ``PATH`` finds, and the second of those is the point —
    ``curl``, ``pip`` and ``npm`` are recorders, so "setup 不安裝任何東西" is a
    file that stayed empty rather than a claim.
    """

    INSTALLERS = ("curl", "wget", "pip", "pip3", "npm", "apt", "apt-get", "brew")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.desktop = self.tmp / "Desktop"
        self.desktop.mkdir()
        # Where setup is told to clean old shortcuts up. It is overridden for the
        # same reason the desktop is, only more so: that step *deletes files*, and
        # its default is this repository's own parent directory.
        self.legacy = self.tmp / "OldWorkspace"
        self.legacy.mkdir()
        self.shims = self.tmp / "bin"
        self.shims.mkdir()
        self.installer_log = self.tmp / "installers.txt"
        for name in self.INSTALLERS:
            shim = self.shims / name
            shim.write_text(
                "#!/usr/bin/env bash\nprintf '%s %s\\n' \"{}\" \"$*\" >> \"{}\"\n"
                "exit 0\n".format(name, self.installer_log),
                encoding="utf-8",
            )
            shim.chmod(0o755)

    def windows(self, path):
        return subprocess.run(
            ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
        ).stdout.strip()

    def run_setup(self):
        environment = dict(os.environ)
        environment["HOYA_DESKTOP"] = self.windows(self.desktop)
        environment["HOYA_LEGACY_DIR"] = self.windows(self.legacy)
        environment["PATH"] = "{}:{}".format(self.shims, environment.get("PATH", ""))
        return subprocess.run(
            ["bash", str(CODE_ROOT / "setup-wsl.sh")],
            capture_output=True, text=True, errors="replace",
            env=environment, timeout=300,
        )

    def links(self):
        return sorted(path.name for path in self.desktop.iterdir()
                      if path.suffix == ".lnk")

    def test_running_it_twice_succeeds_and_leaves_exactly_two_shortcuts(self):
        first = self.run_setup()
        after_first = self.links()
        second = self.run_setup()

        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(sorted(["開啟辯論室.lnk", "關閉辯論室.lnk"]), after_first)
        self.assertEqual(after_first, self.links())

    def test_it_installs_nothing_and_logs_nobody_in(self):
        self.run_setup()

        self.assertFalse(
            self.installer_log.is_file(),
            self.installer_log.read_text(encoding="utf-8")
            if self.installer_log.is_file() else "",
        )

    def test_it_creates_no_virtualenv_in_the_code_root(self):
        self.run_setup()

        self.assertFalse((CODE_ROOT / ".venv").exists())

    def test_it_only_ever_cleans_the_folder_it_was_pointed_at(self):
        """The deleting step is overridable, and a test must always override it."""
        stranger = self.legacy / "別人的捷徑.lnk"
        stranger.write_bytes(b"not a real shortcut")

        self.run_setup()

        self.assertTrue(stranger.is_file())
        self.assertIn("HOYA_LEGACY_DIR",
                      (CODE_ROOT / "setup-wsl.sh").read_text(encoding="utf-8"))

    def test_it_says_where_the_code_and_the_data_are(self):
        done = self.run_setup()

        self.assertIn(str(CODE_ROOT), done.stdout)
        self.assertIn(
            str(CODE_ROOT.parent / "AI-agnets-debating-chamber_data"), done.stdout
        )


@unittest.skipIf(powershell() is None, "這台機器沒有 Windows PowerShell")
class PowerShellParseTest(unittest.TestCase):
    """Both scripts parse. A shortcut that fails to parse fails silently."""

    def test_every_powershell_script_parses(self):
        for name in POWERSHELL_SCRIPTS:
            windows_path = subprocess.run(
                ["wslpath", "-w", str(CODE_ROOT / name)],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            script = (
                "$errors = $null; "
                "[void][System.Management.Automation.Language.Parser]::ParseFile("
                "'{}', [ref]$null, [ref]$errors); "
                "if ($errors.Count -gt 0) {{ $errors | ForEach-Object "
                "{{ Write-Output $_.Message }}; exit 1 }}; exit 0"
            ).format(windows_path)
            done = subprocess.run(
                [powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, errors="replace", timeout=120,
            )
            self.assertEqual(0, done.returncode, "{}: {}".format(name, done.stdout))


if __name__ == "__main__":
    unittest.main()
