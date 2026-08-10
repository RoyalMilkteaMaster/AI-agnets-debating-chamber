"""Ticket 07: 關閉伺服器 —— 先回應、後停機，照常寫 ``server_stop``。

三件事在這裡被釘住，而且刻意分開兩層：

**順序。** ``POST /shutdown`` 必須「先把「已關閉」頁面交給瀏覽器，再去停監聽」。
這一層用注入的停機接縫驗：接縫被呼叫的那一刻，客戶端已經收到了什麼位元組，是可以
直接讀出來的，所以「先回應」是量出來的而不是宣稱的。``GET`` 同一個網址什麼都不停。

**真的停得掉。** 另一層開一個真的 loopback socket、真的跑 ``serve_forever``，然後
用真的 HTTP 請求關掉它：迴圈要結束、埠要停止接受連線、``server_stop`` 要照舊出現在
log 裡、而且不留下任何一條執行緒。這一層不注入任何接縫——注入了就等於沒驗到。

**按鈕。** 每一頁的頁首右上角都有一顆走表單 POST 的「關閉伺服器」，而「已關閉」那
一頁自己沒有：那一頁上的每個連結都已經連不到東西，再給一顆按鈕是騙人。

fixtures 沿用 ``tests/test_webapp.py`` 的 ``PageFixture``，理由與 Ticket 04～06
相同：第二份「一個 Data Root 加一份 log 加一個 handler」會和第一份漂移。
"""

import http.client
import io
import json
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# 這個模組要能用兩種名字被載入：``discover -s tests`` 自己會把這個目錄放上
# sys.path，從 Code Root 執行 ``python3 -m unittest tests.test_webapp_shutdown``
# 不會。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents.webapp import pages  # noqa: E402
from hoya_market_agents.webapp.log import ACTIVE_LOG_NAME  # noqa: E402
from hoya_market_agents.webapp.server import (  # noqa: E402
    SHUTDOWN_PATH,
    serve_webapp,
    webapp_handler_class,
)
from tests.fakes import FixedClock  # noqa: E402
from test_webapp import PageFixture, Response, _Socket, write_live_run  # noqa: E402


class ShutdownFixture(PageFixture):
    """A handler whose stop seam records what the client had already been sent.

    ``self.stops`` gains one entry per call, and each entry is every byte the
    connection had received at the moment the seam was reached. That is what
    turns "先回應、後停機" into a measurement.
    """

    def setUp(self):
        self.stops = []
        self.connection = None
        super().setUp()

    def build_handler(self, stream=None, spawn=None):
        self.stream = stream or self.single_pass_stream()
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=spawn or self.spawn,
            stop=self.record_stop,
        )
        return self.handler

    def record_stop(self):
        self.stops.append(self.connection.outgoing.getvalue())

    def request(self, raw, break_after=None):
        self.connection = _Socket(raw.encode("utf-8"), break_after=break_after)
        self.handler(self.connection, ("127.0.0.1", 54321), None)
        return Response(self.connection.outgoing.getvalue())

    def events(self):
        return [record["event"] for record in self.records()]


class ShutdownEndpointTest(ShutdownFixture, unittest.TestCase):
    """The endpoint's contract: answer, then stop, and only on a submission."""

    def test_a_submitted_shutdown_is_answered_with_a_readable_closed_page(self):
        response = self.post(SHUTDOWN_PATH, {})

        self.assertEqual(200, response.status)
        self.assertIn("已關閉", response.body)
        self.assertEqual("text/html; charset=utf-8", response.headers["Content-Type"])

    def test_the_closed_page_reaches_the_client_before_the_loop_is_asked_to_stop(self):
        self.post(SHUTDOWN_PATH, {})

        self.assertEqual(1, len(self.stops))
        already_sent = self.stops[0].decode("utf-8")
        self.assertIn("200", already_sent.splitlines()[0])
        self.assertIn("已關閉", already_sent)

    def test_the_loop_is_asked_to_stop_exactly_once(self):
        self.post(SHUTDOWN_PATH, {})

        self.assertEqual(1, len(self.stops))

    def test_opening_the_shutdown_url_stops_nothing_and_says_why(self):
        response = self.get(SHUTDOWN_PATH)

        self.assertEqual(404, response.status)
        self.assertEqual([], self.stops)
        self.assertIn("表單", response.body)

    def test_the_closed_page_carries_no_script(self):
        self.assertNotIn("<script", self.post(SHUTDOWN_PATH, {}).body)

    def test_the_request_is_recorded_as_the_reason_the_server_is_stopping(self):
        self.post(SHUTDOWN_PATH, {})

        self.assertIn("shutdown_requested", self.events())

    def build_handler_with_stop(self, stop):
        """A handler whose stop seam is this one, whatever it does."""
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn, stop=stop,
        )
        return self.handler

    def responses_on_the_wire(self):
        return self.connection.outgoing.getvalue().count(b"HTTP/1.")

    def test_a_stop_that_breaks_leaves_one_response_on_the_wire_and_no_other(self):
        """A second response is not an error page; it is two replies in one stream.

        The reply is already sent by the time the seam is reached, so a failure
        there cannot travel back through the module's generic boundary — that
        boundary answers with a page, and a page appended to a page that has
        already gone is neither.
        """
        def stop_that_breaks():
            raise RuntimeError("停不下來")

        self.build_handler_with_stop(stop_that_breaks)

        response = self.post(SHUTDOWN_PATH, {})

        self.assertEqual(1, self.responses_on_the_wire())
        self.assertEqual(200, response.status)
        self.assertIn("已關閉", response.body)

    def test_a_stop_that_breaks_is_recorded_as_this_servers_own_failure(self):
        def stop_that_breaks():
            raise RuntimeError("停不下來")

        self.build_handler_with_stop(stop_that_breaks)
        self.post(SHUTDOWN_PATH, {})

        failures = [
            record for record in self.records()
            if record["event"] == "server_stop_failed"
        ]
        self.assertEqual(1, len(failures))
        self.assertEqual("ERROR", failures[0]["level"])
        self.assertIn("RuntimeError", failures[0]["message"])
        self.assertIn("停不下來", failures[0]["message"])

    def test_a_stop_that_breaks_is_not_recorded_as_a_failed_request(self):
        """It is not one: the request was answered, in full, before this happened."""
        def stop_that_breaks():
            raise RuntimeError("停不下來")

        self.build_handler_with_stop(stop_that_breaks)
        self.post(SHUTDOWN_PATH, {})

        self.assertNotIn("request_failed", self.events())

    def test_a_handler_with_no_server_says_the_button_stopped_nothing(self):
        """The seam's absence is a fact about the handler, not about the request.

        Every handler ``create_webapp_server`` builds has a loop to stop. One a
        test builds by hand has none, and a page claiming otherwise with nothing
        in the log would be the one failure nobody could find afterwards.
        """
        self.handler = webapp_handler_class(
            self.data_root, self.log, stream=self.stream, lock=self.lock,
            spawn=self.spawn,
        )

        response = self.post(SHUTDOWN_PATH, {})

        self.assertEqual(200, response.status)
        self.assertEqual([], self.stops)
        unavailable = [
            record for record in self.records()
            if record["event"] == "server_stop_unavailable"
        ]
        self.assertEqual(1, len(unavailable))
        self.assertEqual("WARNING", unavailable[0]["level"])


class ShutdownButtonTest(ShutdownFixture, unittest.TestCase):
    """One button, in every page's header, submitting to the route that stops."""

    PAGES = ("/", "/history", "/settings",
             "/run/20260801T020000Z-btc-aaaa11", "/does-not-exist")

    def setUp(self):
        super().setUp()
        write_live_run(self.data_root)
        self.index_two_runs()

    def header(self, body, path):
        found = re.search(r"<header.*?</header>", body, re.DOTALL)
        self.assertIsNotNone(found, "no header on {}".format(path))
        return found.group(0)

    def test_every_page_offers_the_stop_button_in_its_header(self):
        for path in self.PAGES:
            header = self.header(self.get(path).body, path)
            self.assertIn('action="{}"'.format(SHUTDOWN_PATH), header, path)
            self.assertIn('method="post"', header, path)
            self.assertIn(pages.SHUTDOWN_LABEL, header, path)

    def test_the_button_is_a_submit_and_carries_no_script_anywhere_on_the_page(self):
        for path in self.PAGES:
            body = self.get(path).body
            self.assertIn('type="submit"', self.header(body, path), path)
            self.assertNotIn("<script", body.replace(
                '<script src="{}" defer></script>'.format(pages.LIVE_SCRIPT_PATH), ""
            ), path)
            self.assertNotIn("onclick", body, path)

    def test_what_the_button_submits_to_is_what_the_server_routes(self):
        header = self.header(self.get("/history").body, "/history")
        action = re.search(r'<form[^>]*action="([^"]+)"[^>]*>\s*<button', header)

        self.assertIsNotNone(action)
        self.assertEqual(SHUTDOWN_PATH, action.group(1))

    def test_the_closed_page_offers_neither_the_button_nor_a_dead_navigation(self):
        body = self.post(SHUTDOWN_PATH, {}).body

        self.assertNotIn('action="{}"'.format(SHUTDOWN_PATH), body)
        self.assertNotIn(pages.SHUTDOWN_LABEL, body)
        self.assertNotIn('class="page-tabs"', body)

    def test_the_closed_page_says_how_to_come_back(self):
        self.assertIn("開啟辯論室", self.post(SHUTDOWN_PATH, {}).body)


class ServedWebappFixture:
    """A real server, on a real loopback port, serving in a thread of its own.

    Nothing here is injected but the clock: the point of this layer is the parts
    the handler tests cannot reach — a listening socket, ``serve_forever``, and
    what ``serve_webapp`` does in its ``finally``.
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.printed = io.StringIO()
        self.returned = {}
        self.threads_before = set(threading.enumerate())
        self.server_thread = threading.Thread(
            target=self._serve, name="webapp-under-test"
        )
        self.addCleanup(self._stop_if_still_serving)
        self.server_thread.start()
        self.port = self._wait_for_port()

    def _serve(self):
        self.returned["url"] = serve_webapp(
            self.data_root, port=0, clock=FixedClock(), out=self.printed
        )

    def _wait_for_port(self):
        """The port the server bound, read from what it printed for the user."""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            found = re.search(r"http://127\.0\.0\.1:(\d+)/", self.printed.getvalue())
            if found:
                return int(found.group(1))
            time.sleep(0.01)
        raise AssertionError("伺服器沒有在 10 秒內印出網址")

    def _stop_if_still_serving(self):
        if not self.server_thread.is_alive():
            return
        try:
            self.post(SHUTDOWN_PATH)
        except OSError:
            pass
        self.server_thread.join(timeout=10)

    def connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def request(self, method, path):
        connection = self.connection()
        try:
            connection.request(method, path)
            answer = connection.getresponse()
            return answer.status, answer.read().decode("utf-8")
        finally:
            connection.close()

    def get(self, path):
        return self.request("GET", path)

    def post(self, path):
        return self.request("POST", path)

    def records(self):
        path = self.data_root / "logs" / ACTIVE_LOG_NAME
        return [
            line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    def events(self):
        return [json.loads(line)["event"] for line in self.records()]

    def refuses_connections(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(5)
        try:
            probe.connect(("127.0.0.1", self.port))
        except (ConnectionRefusedError, socket.timeout):
            return True
        finally:
            probe.close()
        return False


class ShutdownOverASocketTest(ServedWebappFixture, unittest.TestCase):
    """What a browser really does, against a server that is really listening."""

    def stop_and_join(self):
        status, body = self.post(SHUTDOWN_PATH)
        self.server_thread.join(timeout=10)
        return status, body

    def test_a_submitted_shutdown_is_answered_and_then_ends_the_serving_loop(self):
        status, body = self.stop_and_join()

        self.assertEqual(200, status)
        self.assertIn("已關閉", body)
        self.assertFalse(self.server_thread.is_alive())

    def test_the_port_stops_accepting_connections(self):
        self.stop_and_join()

        self.assertTrue(self.refuses_connections())

    def test_server_stop_is_written_exactly_as_it_was_before(self):
        self.stop_and_join()

        stops = [
            record for record in self.records() if '"event": "server_stop"' in record
        ]
        self.assertEqual(1, len(stops))
        self.assertIn('"level": "INFO"', stops[0])
        self.assertIn('"source": "webapp.server"', stops[0])
        self.assertIn("已停止 http://127.0.0.1:{}/".format(self.port), stops[0])

    def test_server_stop_is_the_last_word_in_the_log(self):
        self.stop_and_join()

        self.assertEqual("server_stop", self.events()[-1])

    def extra_threads(self):
        return set(threading.enumerate()) - self.threads_before

    def test_nothing_is_left_holding_the_process_open(self):
        """No non-daemon thread outlives the loop, so the process really exits.

        This is the half of "無殘留執行緒" that is a property of the code rather
        than of the test's patience. The request thread that carried the stop is
        still tidying its socket up when ``serve_forever`` returns —
        ``ThreadingHTTPServer`` runs requests on daemon threads and
        ``server_close`` does not join those — and a daemon thread cannot keep an
        interpreter alive. One that was not a daemon could, which is what would
        make ``python3 -m hoya_market_agents webapp`` hang after the button was
        pressed instead of returning to the prompt.
        """
        self.stop_and_join()

        self.assertEqual([], [thread for thread in self.extra_threads()
                              if not thread.daemon])

    def test_the_last_request_thread_ends_by_itself(self):
        """And the other half: it ends, rather than living on as a daemon."""
        self.stop_and_join()

        deadline = time.monotonic() + 10
        while self.extra_threads() and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(set(), self.extra_threads())

    def test_opening_the_shutdown_url_leaves_the_server_serving(self):
        status, _ = self.get(SHUTDOWN_PATH)

        self.assertEqual(404, status)
        self.assertTrue(self.server_thread.is_alive())
        self.assertEqual(200, self.get("/")[0])

    def test_the_server_returns_the_url_it_served_when_the_loop_ends(self):
        self.stop_and_join()

        self.assertEqual(
            "http://127.0.0.1:{}/".format(self.port), self.returned["url"]
        )


if __name__ == "__main__":
    unittest.main()
