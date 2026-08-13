"""Ticket 01: runtime ownership —— ``/health`` 的契約與有前提的關閉。

三件事在這裡被釘住：

**誰在聽。** ``GET /health`` 是 server 這一側唯一的 ownership producer，回傳
``app``、``runtime_owner``、非空 ``instance`` 與 JSON boolean ``active_run``。缺欄位、
錯型別、其他 app、其他 owner、malformed JSON、404 與連不上一律 fail closed——這一層用
真的 loopback listener 餵真的位元組，所以「解析」是量出來的而不是宣稱的。

**誰在解析。** ``webapp/runtime_control.py`` 是唯一的 consumer。Bash 與 PowerShell 只
讀它印出來的 ``key=value``，不各自碰 JSON。

**關掉的是不是同一台。** ``POST /shutdown`` 帶 ``expect_runtime`` 與 ``expect_instance``
時，server 在處理 POST 的當下重新比對；listener 已經被換掉的那一刻，舊 precondition 拿到
``409``，而且換上來的那一台還活著。

fixtures 沿用 ``tests/test_webapp.py`` 的 ``PageFixture``，理由與既有 webapp 測試相同：
第二份「一個 Data Root 加一份 log 加一個 handler」會和第一份漂移。
"""

import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

# 這個模組要能用兩種名字被載入：``discover -s tests`` 自己會把這個目錄放上
# sys.path，從 Code Root 執行 ``python3 -m unittest tests.test_runtime_control`` 不會。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents.webapp import launch as launch_module  # noqa: E402
from hoya_market_agents.webapp import runtime_control  # noqa: E402
from hoya_market_agents.webapp.log import open_webapp_log  # noqa: E402
from hoya_market_agents.webapp.server import (  # noqa: E402
    HEALTH_PATH,
    SHUTDOWN_PATH,
    create_webapp_server,
    webapp_handler_class,
)
from test_webapp import FakeProcess, PageFixture  # noqa: E402


def free_port():
    """A loopback port that nothing is listening on right now."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


class CannedListener:
    """One loopback listener that answers every request with fixed bytes.

    It is here so the client's refusals are tested against a real socket and a
    real HTTP response rather than against an injected parser: "404 是 foreign"
    and "malformed JSON 是 foreign" are statements about what arrives on a wire.
    """

    def __init__(self, status=200, body="", content_type="application/json"):
        listener = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                listener.requests.append(("GET", self.path, ""))
                self._answer()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                listener.requests.append(("POST", self.path, raw))
                self._answer()

            def _answer(self):
                payload = listener.body.encode("utf-8")
                self.send_response(listener.status)
                self.send_header("Content-Type", listener.content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return None

        self.status = status
        self.body = body
        self.content_type = content_type
        self.requests = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        # A short poll interval, because ``close`` waits for one: the default
        # half second, multiplied by the listeners this module raises, is the
        # difference between a fast suite and a minute of nothing happening.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)


class ProbeFixture:
    """A test that can raise a canned listener and always takes it down again."""

    def listening(self, status=200, body="", content_type="application/json"):
        listener = CannedListener(status=status, body=body, content_type=content_type)
        self.addCleanup(listener.close)
        return listener

    def serving(self, payload):
        return self.listening(body=json.dumps(payload))

    def probe(self, listener):
        return runtime_control.probe(port=listener.port)


# -- what the server publishes ----------------------------------------------


class HealthContractTest(PageFixture, unittest.TestCase):
    """``/health`` is the ownership contract, spelled exactly once."""

    def health(self):
        response = self.get(HEALTH_PATH)
        return response, json.loads(response.body)

    def test_health_answers_json_rather_than_a_page(self):
        response, _ = self.health()

        self.assertEqual(200, response.status)
        self.assertEqual(
            "application/json; charset=utf-8", response.headers["Content-Type"]
        )

    def test_health_names_this_app_and_names_wsl_as_its_runtime_owner(self):
        _, payload = self.health()

        self.assertEqual("hoya-market-agents-webapp", payload["app"])
        self.assertEqual("wsl", payload["runtime_owner"])

    def test_the_instance_is_a_non_empty_string(self):
        _, payload = self.health()

        self.assertIsInstance(payload["instance"], str)
        self.assertNotEqual("", payload["instance"])

    def test_two_servers_do_not_share_an_instance(self):
        """The value exists to tell one listener from the next one on that port."""
        first = json.loads(self.get(HEALTH_PATH).body)["instance"]
        self.build_handler()
        second = json.loads(self.get(HEALTH_PATH).body)["instance"]

        self.assertNotEqual(first, second)

    def test_active_run_is_a_json_boolean_and_is_false_when_nothing_runs(self):
        _, payload = self.health()

        self.assertIs(False, payload["active_run"])

    def test_active_run_is_true_while_a_launch_this_server_started_is_running(self):
        process = FakeProcess()
        self.lock.claim(lambda: process)

        self.assertIs(True, json.loads(self.get(HEALTH_PATH).body)["active_run"])

    def test_active_run_goes_back_to_false_once_that_launch_has_exited(self):
        process = FakeProcess()
        self.lock.claim(lambda: process)
        process.finish(0)

        self.assertIs(False, json.loads(self.get(HEALTH_PATH).body)["active_run"])

    def test_health_carries_only_the_four_contract_fields(self):
        """A consumer that fails closed needs the contract to be the whole body."""
        _, payload = self.health()

        self.assertEqual(
            {"app", "runtime_owner", "instance", "active_run"}, set(payload)
        )

    def test_posting_to_health_is_not_a_route(self):
        self.assertEqual(404, self.post(HEALTH_PATH, {}).status)


# -- what the one consumer makes of it --------------------------------------


class ProbeTest(ProbeFixture, unittest.TestCase):
    """free／owned／foreign, decided in one module against real responses."""

    OWNED = {
        "app": "hoya-market-agents-webapp",
        "runtime_owner": "wsl",
        "instance": "abc123",
        "active_run": False,
    }

    def foreign_reason_is_one_line(self, result):
        self.assertEqual(runtime_control.FOREIGN, result.state)
        self.assertTrue(result.reason.strip())
        self.assertNotIn("\n", result.reason)

    def test_a_port_nobody_listens_on_is_free(self):
        result = runtime_control.probe(port=free_port())

        self.assertEqual(runtime_control.FREE, result.state)
        self.assertIsNone(result.instance)

    def test_this_apps_own_health_is_owned_and_carries_its_instance(self):
        result = self.probe(self.serving(self.OWNED))

        self.assertEqual(runtime_control.OWNED, result.state)
        self.assertEqual("abc123", result.instance)
        self.assertIs(False, result.active_run)

    def test_an_owned_listener_reporting_an_active_run_says_so(self):
        payload = dict(self.OWNED, active_run=True)

        result = self.probe(self.serving(payload))

        self.assertEqual(runtime_control.OWNED, result.state)
        self.assertIs(True, result.active_run)

    def test_another_owner_on_this_port_is_foreign(self):
        self.foreign_reason_is_one_line(
            self.probe(self.serving(dict(self.OWNED, runtime_owner="windows")))
        )

    def test_another_app_on_this_port_is_foreign(self):
        self.foreign_reason_is_one_line(
            self.probe(self.serving(dict(self.OWNED, app="something-else")))
        )

    def test_an_empty_instance_is_foreign(self):
        self.foreign_reason_is_one_line(
            self.probe(self.serving(dict(self.OWNED, instance="")))
        )

    def test_a_missing_instance_is_foreign(self):
        payload = dict(self.OWNED)
        payload.pop("instance")

        self.foreign_reason_is_one_line(self.probe(self.serving(payload)))

    def test_a_missing_active_run_is_foreign(self):
        payload = dict(self.OWNED)
        payload.pop("active_run")

        self.foreign_reason_is_one_line(self.probe(self.serving(payload)))

    def test_an_active_run_that_is_not_a_json_boolean_is_foreign(self):
        """``"false"`` and ``0`` are not ``false``; a stop decided on them is a guess."""
        for wrong in ("false", 0, 1, None, "true"):
            with self.subTest(active_run=wrong):
                self.foreign_reason_is_one_line(
                    self.probe(self.serving(dict(self.OWNED, active_run=wrong)))
                )

    def test_malformed_json_is_foreign(self):
        self.foreign_reason_is_one_line(self.probe(self.listening(body="{not json")))

    def test_a_body_that_is_not_an_object_is_foreign(self):
        self.foreign_reason_is_one_line(self.probe(self.listening(body="[1, 2]")))

    def test_a_listener_answering_404_is_foreign(self):
        self.foreign_reason_is_one_line(
            self.probe(self.listening(status=404, body="nope"))
        )

    def test_a_listener_answering_500_is_foreign(self):
        self.foreign_reason_is_one_line(
            self.probe(self.listening(status=500, body="boom"))
        )

    def test_an_html_page_on_this_port_is_foreign(self):
        self.foreign_reason_is_one_line(
            self.listening(body="<html></html>", content_type="text/html")
            and self.probe(
                self.listening(body="<html></html>", content_type="text/html")
            )
        )

    def test_a_probe_asks_the_health_url_and_nothing_else(self):
        listener = self.serving(self.OWNED)

        self.probe(listener)

        self.assertEqual([("GET", HEALTH_PATH, "")], listener.requests)


# -- the shutdown precondition, at the moment the POST is handled -----------


class ShutdownPreconditionTest(PageFixture, unittest.TestCase):
    """The claim is re-checked when the POST arrives, not when it was made."""

    def setUp(self):
        super().setUp()
        self.stops = []
        self.instance = "instance-one"
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=self.spawn,
            stop=lambda: self.stops.append("stopped"),
            instance=self.instance,
        )

    def stop_with(self, **fields):
        return self.post(SHUTDOWN_PATH, fields)

    def test_a_matching_claim_stops_the_server(self):
        response = self.stop_with(
            expect_runtime="wsl", expect_instance=self.instance
        )

        self.assertEqual(200, response.status)
        self.assertEqual(1, len(self.stops))

    def test_a_claim_naming_another_instance_is_a_conflict_and_stops_nothing(self):
        response = self.stop_with(expect_runtime="wsl", expect_instance="other")

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)

    def test_a_claim_naming_another_runtime_is_a_conflict_and_stops_nothing(self):
        response = self.stop_with(
            expect_runtime="windows", expect_instance=self.instance
        )

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)

    def test_a_claim_missing_its_instance_is_a_conflict_and_stops_nothing(self):
        response = self.stop_with(expect_runtime="wsl")

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)

    def test_a_claim_missing_its_runtime_is_a_conflict_and_stops_nothing(self):
        response = self.stop_with(expect_instance=self.instance)

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)

    def test_the_pages_own_button_carries_no_claim_and_still_stops(self):
        """The in-page form is same-origin and keeps the behaviour it had."""
        response = self.stop_with()

        self.assertEqual(200, response.status)
        self.assertEqual(1, len(self.stops))

    def test_a_refused_stop_is_recorded(self):
        self.stop_with(expect_runtime="wsl", expect_instance="other")

        self.assertIn(
            "shutdown_claim_rejected", [record["event"] for record in self.records()]
        )


class ActiveRunConsentTest(PageFixture, unittest.TestCase):
    """A run that started *after* the probe still cannot be interrupted silently.

    The window this closes is a real one and it is not narrow: a client reads
    ``/health``, sees ``active_run: false``, decides no question needs asking, and
    posts. Between those two moments the same instance can pick up a launch. A
    server that only re-checks the instance would then stop a running analysis
    that nobody was ever asked about.

    So ``active_run`` is re-read when the ``POST`` is handled, inside the same
    critical section that decides accept or reject, and a busy server without
    explicit consent is a ``409`` that stops nothing.
    """

    def setUp(self):
        super().setUp()
        self.stops = []
        self.instance = "instance-one"
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=self.spawn,
            stop=lambda: self.stops.append("stopped"),
            instance=self.instance,
        )

    def stop_with(self, **fields):
        claim = {"expect_runtime": "wsl", "expect_instance": self.instance}
        claim.update(fields)
        return self.post(SHUTDOWN_PATH, claim)

    def probe_says_idle(self):
        seen = json.loads(self.get(HEALTH_PATH).body)
        self.assertIs(False, seen["active_run"])
        return seen

    def start_a_run(self):
        """What happens between the probe and the POST in the race this closes."""
        self.lock.claim(lambda: FakeProcess())

    def test_a_run_that_starts_after_an_idle_probe_refuses_an_unasked_stop(self):
        self.probe_says_idle()

        self.start_a_run()
        response = self.stop_with()

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)

    def test_that_refusal_leaves_the_server_answering_health(self):
        self.probe_says_idle()
        self.start_a_run()

        self.stop_with()

        self.assertEqual(200, self.get(HEALTH_PATH).status)
        self.assertIs(True, json.loads(self.get(HEALTH_PATH).body)["active_run"])

    def test_a_busy_server_stops_when_the_stop_carries_consent(self):
        self.start_a_run()

        response = self.stop_with(allow_active_run="yes")

        self.assertEqual(200, response.status)
        self.assertEqual(1, len(self.stops))

    def test_an_idle_server_stops_with_or_without_consent(self):
        self.assertEqual(200, self.stop_with().status)
        self.assertEqual(1, len(self.stops))

    def test_only_the_one_spelling_counts_as_consent(self):
        """Fail closed on the value too: a stop is not decided by a near miss."""
        for wrong in ("", " ", "no", "true", "1", "YES", "Yes", "y", "on", "allow"):
            with self.subTest(allow_active_run=wrong):
                self.setUp()
                self.start_a_run()

                response = self.stop_with(allow_active_run=wrong)

                self.assertEqual(409, response.status)
                self.assertEqual([], self.stops)

    def test_consent_does_not_excuse_a_wrong_instance(self):
        """The two preconditions are both required, not either one."""
        self.start_a_run()

        response = self.stop_with(expect_instance="other", allow_active_run="yes")

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)

    def test_a_run_that_has_finished_is_not_an_active_run(self):
        process = FakeProcess()
        self.lock.claim(lambda: process)
        process.finish(0)

        response = self.stop_with()

        self.assertEqual(200, response.status)
        self.assertEqual(1, len(self.stops))

    def test_the_refusal_is_recorded_as_its_own_reason(self):
        self.start_a_run()

        self.stop_with()

        rejections = [
            record for record in self.records()
            if record["event"] == "shutdown_claim_rejected"
        ]
        self.assertEqual(1, len(rejections))
        self.assertIn("分析", rejections[0]["message"])

    def test_the_in_page_button_cannot_interrupt_a_run_either(self):
        """It carries no claim, so it also carries no consent."""
        self.start_a_run()

        response = self.post(SHUTDOWN_PATH, {})

        self.assertEqual(409, response.status)
        self.assertEqual([], self.stops)


class LaunchLockShutdownSeamTest(unittest.TestCase):
    """Authorising a stop *takes* the lock; it does not merely look at it.

    A read that only reports "nothing is running" is out of date the instant it
    returns, and the gap between that answer and the server actually stopping is
    long enough to start a run in. So the answer and the claim on the lock are
    one step: once a stop is reserved, no launch begins.
    """

    def setUp(self):
        self.lock = launch_module.LaunchLock()
        self.started = []

    def start(self):
        self.started.append("started")
        return FakeProcess()

    def test_an_idle_lock_reserves_a_stop_that_asked_for_no_consent(self):
        self.assertTrue(self.lock.reserve_stop(False))

    def test_a_busy_lock_refuses_a_stop_that_asked_for_no_consent(self):
        self.lock.claim(lambda: FakeProcess())

        self.assertFalse(self.lock.reserve_stop(False))

    def test_a_busy_lock_reserves_a_stop_that_carries_consent(self):
        self.lock.claim(lambda: FakeProcess())

        self.assertTrue(self.lock.reserve_stop(True))

    def test_a_finished_launch_leaves_the_lock_reservable(self):
        process = FakeProcess()
        self.lock.claim(lambda: process)
        process.finish(0)

        self.assertTrue(self.lock.reserve_stop(False))

    def test_a_reserved_lock_starts_no_further_launch(self):
        """The whole point: the callable is never reached, not merely refused."""
        self.lock.reserve_stop(False)

        self.assertIsNone(self.lock.claim(self.start))
        self.assertEqual([], self.started)

    def test_a_refused_reservation_leaves_launching_alone(self):
        self.lock.claim(lambda: FakeProcess())
        self.lock.reserve_stop(False)

        # Still refused, but because a run is going — not because of the failed
        # reservation. A refusal that latched would close this server for good.
        self.assertIsNone(self.lock.claim(self.start))
        self.assertTrue(self.lock.release_stop() is None)

    def test_releasing_a_reservation_lets_launching_resume(self):
        self.lock.reserve_stop(False)
        self.lock.release_stop()

        self.assertIsNotNone(self.lock.claim(self.start))
        self.assertEqual(["started"], self.started)

    def test_reserving_twice_does_not_deadlock_on_its_own_guard(self):
        """It reads the child directly rather than calling back into ``busy``."""
        self.lock.claim(lambda: FakeProcess())

        self.assertFalse(self.lock.reserve_stop(False))
        self.assertFalse(self.lock.reserve_stop(False))
        self.assertTrue(self.lock.busy())

    def test_a_reservation_does_not_make_the_lock_report_a_run(self):
        """``active_run`` is about analyses, not about this server's own exit."""
        self.lock.reserve_stop(False)

        self.assertFalse(self.lock.busy())


class ShutdownReservationTest(PageFixture, unittest.TestCase):
    """Nothing starts between the moment a stop is authorised and the stop.

    This is the window a reservation exists for. ``POST /shutdown`` decides, and
    then — still in this request, but no longer holding any guard — asks the
    serving loop to end. A launch arriving in between would be a run started
    after this server had already agreed to die, and it would be killed without
    anybody having been asked about it.
    """

    def setUp(self):
        super().setUp()
        self.stops = []
        self.started = []
        self.instance = "instance-one"

    def start(self):
        self.started.append("started")
        return FakeProcess()

    def record_stop(self):
        """A stop seam that takes no argument, because the real one takes none."""
        self.stops.append("stopped")

    def build(self, stop):
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn, stop=stop, instance=self.instance,
        )

    def stop_request(self, **fields):
        claim = {"expect_runtime": "wsl", "expect_instance": self.instance}
        claim.update(fields)
        return self.post(SHUTDOWN_PATH, claim)

    def test_a_launch_reaching_the_lock_during_the_stop_starts_nothing(self):
        """The stop seam runs in the window; it tries to launch from inside it."""
        attempts = []

        def stop_that_races():
            self.stops.append("stopped")
            attempts.append(self.lock.claim(self.start))

        self.build(stop_that_races)

        response = self.stop_request()

        self.assertEqual(200, response.status)
        self.assertEqual(1, len(self.stops))
        self.assertEqual([None], attempts)
        self.assertEqual([], self.started)

    def test_the_same_thing_across_two_real_threads_at_a_barrier(self):
        """No sleep decides this: a barrier does, and both sides wait on it.

        The stop seam parks inside the window and lets a second thread run the
        launch. The launch attempt is over before the stop is allowed to finish,
        so the interleaving is the one being asserted rather than one that
        happened to occur.
        """
        window = threading.Barrier(2, timeout=10)
        attempted = threading.Event()
        attempts = []

        def stop_that_waits():
            self.stops.append("stopped")
            window.wait()
            self.assertTrue(attempted.wait(timeout=10))

        def launch_in_the_window():
            window.wait()
            attempts.append(self.lock.claim(self.start))
            attempted.set()

        self.build(stop_that_waits)
        launcher = threading.Thread(target=launch_in_the_window, daemon=True)
        launcher.start()
        self.addCleanup(launcher.join, 10)

        response = self.stop_request()
        launcher.join(timeout=10)

        self.assertEqual(200, response.status)
        self.assertEqual([None], attempts)
        self.assertEqual([], self.started)

    def test_a_refused_stop_reserves_nothing_and_launching_still_works(self):
        self.build(self.record_stop)
        self.lock.claim(lambda: FakeProcess())

        self.stop_request()

        self.assertEqual([], self.stops)
        self.assertIsNone(self.lock.claim(self.start))
        self.assertEqual([], self.started)

    def test_a_handler_with_no_loop_to_stop_gives_the_lock_back(self):
        """It said 200 but stopped nothing, so this server must still launch."""
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn, instance=self.instance,
        )

        response = self.stop_request()

        self.assertEqual(200, response.status)
        self.assertIsNotNone(self.lock.claim(self.start))
        self.assertEqual(["started"], self.started)

    def test_a_stop_that_breaks_gives_the_lock_back(self):
        def stop_that_breaks():
            raise RuntimeError("停不下來")

        self.build(stop_that_breaks)

        response = self.stop_request()

        self.assertEqual(200, response.status)
        self.assertIsNotNone(self.lock.claim(self.start))
        self.assertEqual(["started"], self.started)

    def test_a_stop_that_worked_keeps_the_lock(self):
        """This server is going away; a launch it started could outlive nothing."""
        self.build(self.record_stop)

        self.stop_request()

        self.assertIsNone(self.lock.claim(self.start))
        self.assertEqual([], self.started)


# -- the same thing over a socket, through the one client -------------------


class ServedRuntime:
    """One real webapp on one real port, in a thread of its own."""

    def __init__(self, data_root, port=0):
        self.log = open_webapp_log(data_root)
        self.server = create_webapp_server(data_root, self.log, port=port)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01},
            name="runtime-under-test", daemon=True,
        )
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self.server.server_close()
        self.log.close()


class ListenerReplacementTest(PageFixture, unittest.TestCase):
    """A stop aimed at the listener that answered ``/health`` and at no other."""

    def serve(self, port=0):
        served = ServedRuntime(self.data_root, port=port)
        self.addCleanup(served.close)
        return served

    def test_a_stop_carrying_the_instance_that_answered_health_succeeds(self):
        served = self.serve()
        seen = runtime_control.probe(port=served.port)

        result = runtime_control.shutdown(seen.instance, port=served.port)
        served.thread.join(timeout=10)

        self.assertEqual(runtime_control.OWNED, seen.state)
        self.assertTrue(result.stopped)
        self.assertFalse(served.thread.is_alive())

    def test_a_replaced_listener_refuses_the_old_claim_and_keeps_serving(self):
        first = self.serve()
        seen = runtime_control.probe(port=first.port)
        port = first.port
        first.close()
        second = self.serve(port=port)

        result = runtime_control.shutdown(seen.instance, port=port)

        self.assertFalse(result.stopped)
        self.assertEqual(409, result.status)
        self.assertTrue(second.thread.is_alive())

    def test_the_replacement_still_answers_health_after_refusing(self):
        first = self.serve()
        seen = runtime_control.probe(port=first.port)
        port = first.port
        first.close()
        second = self.serve(port=port)

        runtime_control.shutdown(seen.instance, port=port)
        after = runtime_control.probe(port=port)

        self.assertEqual(runtime_control.OWNED, after.state)
        self.assertNotEqual(seen.instance, after.instance)
        self.assertTrue(second.thread.is_alive())

    def test_a_stop_against_a_free_port_reports_it_rather_than_claiming_success(self):
        result = runtime_control.shutdown("anything", port=free_port())

        self.assertFalse(result.stopped)


# -- what Bash reads --------------------------------------------------------


class ProbeCommandTest(ProbeFixture, unittest.TestCase):
    """The command line is the whole seam Bash uses: ``key=value``, one per line."""

    def run_probe(self, port):
        out = []
        code = runtime_control.main(["probe", "--port", str(port)], out=out.append)
        return code, dict(
            line.split("=", 1) for line in "".join(out).splitlines() if "=" in line
        )

    def test_a_free_port_is_reported_as_free(self):
        code, fields = self.run_probe(free_port())

        self.assertEqual(0, code)
        self.assertEqual("free", fields["state"])

    def test_the_address_is_printed_so_the_shell_never_spells_the_port(self):
        port = free_port()

        _, fields = self.run_probe(port)

        self.assertEqual("http://127.0.0.1:{}/".format(port), fields["url"])

    def test_an_owned_listener_reports_its_instance_and_active_run(self):
        listener = self.serving(
            {
                "app": "hoya-market-agents-webapp",
                "runtime_owner": "wsl",
                "instance": "abc123",
                "active_run": True,
            }
        )

        code, fields = self.run_probe(listener.port)

        self.assertEqual(0, code)
        self.assertEqual("owned", fields["state"])
        self.assertEqual("abc123", fields["instance"])
        self.assertEqual("yes", fields["active_run"])

    def test_a_foreign_listener_reports_one_line_of_reason(self):
        listener = self.listening(status=404, body="nope")

        code, fields = self.run_probe(listener.port)

        self.assertEqual(0, code)
        self.assertEqual("foreign", fields["state"])
        self.assertTrue(fields["reason"].strip())

    def test_the_reason_never_spans_two_lines_of_the_seam(self):
        listener = self.listening(body="{not json\nsecond line")

        _, fields = self.run_probe(listener.port)

        self.assertEqual("foreign", fields["state"])
        self.assertNotIn("=", fields["reason"].replace("＝", ""))


class StopCommandTest(ProbeFixture, unittest.TestCase):
    """``stop`` sends the two precondition fields and reports what happened."""

    def run_stop(self, port, instance):
        out = []
        code = runtime_control.main(
            ["stop", "--port", str(port), "--instance", instance], out=out.append
        )
        return code, "".join(out)

    def test_the_claim_is_sent_as_a_form_body_naming_wsl_and_the_instance(self):
        listener = self.listening(status=200, body="ok", content_type="text/html")

        self.run_stop(listener.port, "abc123")

        method, path, body = listener.requests[-1]
        self.assertEqual("POST", method)
        self.assertEqual(SHUTDOWN_PATH, path)
        self.assertEqual(
            {"expect_runtime": ["wsl"], "expect_instance": ["abc123"]},
            parse_qs(body),
        )

    def test_a_conflict_exits_non_zero_and_says_so(self):
        listener = self.listening(status=409, body="conflict", content_type="text/html")

        code, printed = self.run_stop(listener.port, "abc123")

        self.assertNotEqual(0, code)
        self.assertTrue(printed.strip())

    def test_a_stop_that_was_taken_exits_zero(self):
        listener = self.listening(status=200, body="ok", content_type="text/html")

        code, _ = self.run_stop(listener.port, "abc123")

        self.assertEqual(0, code)

    def test_consent_is_absent_from_the_body_unless_it_was_given(self):
        """A field that is always sent is a field that consents to everything."""
        listener = self.listening(status=200, body="ok", content_type="text/html")

        self.run_stop(listener.port, "abc123")

        self.assertNotIn("allow_active_run", parse_qs(listener.requests[-1][2]))

    def test_consent_travels_in_the_body_when_it_was_given(self):
        listener = self.listening(status=200, body="ok", content_type="text/html")
        out = []

        runtime_control.main(
            ["stop", "--port", str(listener.port), "--instance", "abc123",
             "--allow-active-run"],
            out=out.append,
        )

        self.assertEqual(
            {"expect_runtime": ["wsl"], "expect_instance": ["abc123"],
             "allow_active_run": ["yes"]},
            parse_qs(listener.requests[-1][2]),
        )

    def test_the_client_sends_consent_only_when_it_is_asked_to(self):
        listener = self.listening(status=200, body="ok", content_type="text/html")

        runtime_control.shutdown("abc123", port=listener.port)
        without = parse_qs(listener.requests[-1][2])
        runtime_control.shutdown("abc123", port=listener.port, allow_active_run=True)
        with_consent = parse_qs(listener.requests[-1][2])

        self.assertNotIn("allow_active_run", without)
        self.assertEqual(["yes"], with_consent["allow_active_run"])


if __name__ == "__main__":
    unittest.main()
