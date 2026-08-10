"""Ticket 07：離線兩頁的五導覽注入在回應上，不在檔案裡。

一份 run 的 ``report.html`` 與 ``debate.html`` 寫完就唯讀，而且分享出去要能自己
看（ADR 0007）。所以「經伺服器瀏覽時也有五導覽」這件事只發生在 HTTP 回應層：檔案
一個位元組都不動，導覽是送出去的那份位元組多出來的一段。

這個模組釘住的是那條分界，而且都從公開的請求路徑量：

* 兩頁經伺服器取用時，多出來的那一段就是站內五導覽，而且「市場報告／完整辯論」指
  向**正在讀的這一個 run**，不是別的 run。
* 多出來的那一段自帶樣式：值取自 ``design_tokens``、每一條選擇器都以注入導覽自己
  的 class 開頭，所以舊 run 的紙白頁上也是新設計風，而且碰不到那一頁原有的任何元
  素。裡面沒有 ``<script>``、沒有 inline event handler。
* 回應裡新增的 landmark 名稱是唯一的：離線頁自己那條導覽叫「主要頁面」，注入的這
  條叫「站內導覽」。
* 插入點是開頭的 ``<body>`` 標籤之後，而且是**真的**那一個——註解裡的 ``<body>``
  不算，``<body data-note="1 > 0">`` 這種帶引號屬性的標籤要整個跨過去。定位不確定
  就原樣送出。
* 導覽本身組不出來（run 目錄消失、資料異常）時，讀者拿到的是原始位元組加一行
  log，不是 500。
* 送過之後磁碟上 ``runs/`` 底下每個檔案的雜湊都沒變；檔案本身也從來沒有站內連結
  ——直接開檔看到的還是離線 bundle 自己的兩分頁導覽。
* run 詳情頁把同一個網址嵌在 ``<iframe>`` 裡，那一次不注入。

fixture 是這個模組自己的：一個暫時 Data Root、一份 log、一個 handler，沒有一行從
別的測試模組借來。這一票和 ``pages.py``／``tests/test_webapp.py`` 是並行開發的，
借過去等於把兩張票綁在一起。
"""

import io
import json
import re
import sys
import tempfile
import unittest
from email.parser import BytesParser
from hashlib import sha256
from pathlib import Path
from unittest import mock

# 這個模組要能用兩種名字被載入：``discover -s tests`` 自己會把這個目錄放上
# sys.path，從 Code Root 執行 ``python3 -m unittest tests.test_webapp_nav_injection``
# 不會。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents import design_tokens  # noqa: E402
from hoya_market_agents.run_store import run_dir_parts  # noqa: E402
from hoya_market_agents.webapp import server as server_module  # noqa: E402
from hoya_market_agents.webapp.log import ACTIVE_LOG_NAME, open_webapp_log  # noqa: E402
from hoya_market_agents.webapp.server import (  # noqa: E402
    SITE_NAV_ADMIN_CLASS,
    SITE_NAV_CLASS,
    SITE_NAV_LABEL,
    artifact_with_site_nav,
    body_tag_end,
    run_artifacts,
    site_nav_fragment,
    webapp_handler_class,
)


RUN_ID = "20260801T020000Z-btc-aaaa11"
QUESTION = "BTC 未來七天會不會漲"

# 另一個也有報告的 run。它存在只為了一件事：讓「這一個 run」和「別的 run」是兩個
# 不同的答案，否則「指向自身」的測試會自動通過。
OTHER_RUN_ID = "20260805T020000Z-eth-bbbb22"
OTHER_QUESTION = "ETH 未來七天會不會漲"

BODY_OPEN = b"<body>"

REPORT_ARTIFACT = "report.html"
DEBATE_ARTIFACT = "debate.html"

OFFLINE_PAGE_TITLES = {
    REPORT_ARTIFACT: "市場判斷報告",
    DEBATE_ARTIFACT: "完整辯論與證據",
}

# 沒有插入點的一頁。``<style>`` 裡出現 ``body{`` 是故意的：那不是標籤。
PAGE_WITH_NO_BODY_TAG = (
    "<!doctype html><title>沒有 body 標籤的舊頁</title>"
    "<style>body{margin:0;}</style><p>這一頁還是要開得起來。</p>"
)

# 五個導覽的目標。標籤與路徑都是 R-002 點名的那五個，寫死在這裡而不是從實作讀回
# 來：從實作讀回來的期望值不會因為實作改錯而失敗。
SITE_TABS = {
    "即時辯論": "/",
    "歷史與命中率": "/history",
    "市場報告": "/run/{}/report.html".format(RUN_ID),
    "完整辯論": "/run/{}/debate.html".format(RUN_ID),
    "設定": "/settings",
}

TAB_LINK = re.compile(r'<a href="([^"]*)"[^>]*>([^<]*)</a>')
INLINE_HANDLER = re.compile(r"\son[a-zA-Z]+\s*=")
NAV_LANDMARK = re.compile(r'<nav\b[^>]*aria-label="([^"]*)"')
STYLE_BLOCK = re.compile(r"<style>(.*?)</style>", re.DOTALL)
CSS_RULE = re.compile(r"([^{}]+)\{[^{}]*\}")


def offline_page(title):
    """一份離線頁該有的最小形狀，包含一個可以插在後面的 ``<body>``。"""
    return (
        '<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
        "<title>{0}</title><style>body{{margin:0;}}</style></head>"
        "<body><main><h1>{0}</h1><p>離線內容。</p></main></body></html>"
    ).format(title)


def paper_white_page(title):
    """舊 build 的紙白風離線頁：沒有 design token、沒有 ``.page-tabs`` 規則。

    注入的樣式要在這種頁面上也成立，否則 Spec 已核准代價那句「舊 run 紙白頁上浮新
    設計風導覽列」是空的。
    """
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>{0}</title><style>body{{font-family:serif;background:#fff;}}</style>"
        "</head><body><h1>{0}</h1><p>舊版報告內容。</p></body></html>"
    ).format(title)


def tab_targets(fragment):
    """``{標籤: 連結目標}``：這一段導覽實際上會把讀者帶到哪裡。"""
    return {label: href for href, label in TAB_LINK.findall(fragment)}


class _Socket:
    """一個請求走完真實 handler 所需要的最小 socket。"""

    def __init__(self, request_bytes):
        self.incoming = io.BytesIO(request_bytes)
        self.outgoing = io.BytesIO()

    def makefile(self, mode, *_args, **_kwargs):
        return self.incoming if "r" in mode else self.outgoing

    def sendall(self, data):
        self.outgoing.write(data)

    def close(self):
        return None


class Response:
    """一份解析過的 HTTP 回應。"""

    def __init__(self, raw):
        head, _, body = raw.partition(b"\r\n\r\n")
        status_line, _, header_text = head.partition(b"\r\n")
        self.status = int(status_line.split()[1])
        self.headers = BytesParser().parsebytes(header_text)
        self.body_bytes = body
        self.body = body.decode("utf-8", errors="replace")


def write_run(data_root, run_id, question, artifacts=()):
    """一個 ``resolve_run_dir`` 認得的 run 目錄，只帶這一票用得到的紀錄。

    刻意只寫 ``question.json`` 與 ``manifest.json``：注入導覽問的是「這個 run 的兩
    個檔案在不在」，它不該需要 votes 或 evidence，而一個沒有那些紀錄的 run 目錄正
    好是這件事的證據。
    """
    date_dir, name = run_dir_parts(run_id, question)
    run_dir = Path(data_root) / "runs" / date_dir / name
    run_dir.mkdir(parents=True)
    for record, payload in (
        ("question.json", {"run_id": run_id, "question": question}),
        ("manifest.json", {"run_id": run_id, "question": question}),
    ):
        (run_dir / record).write_text(
            json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    for artifact, html in artifacts:
        (run_dir / artifact).write_text(html, encoding="utf-8")
    return run_dir


class NavInjectionFixture:
    """一個暫時 Data Root、一份 log、一個 handler，沒有監聽中的 socket。"""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.log = open_webapp_log(self.data_root)
        self.addCleanup(self.log.close)
        self.handler = webapp_handler_class(self.data_root, self.log)
        self.run_dir = write_run(self.data_root, RUN_ID, QUESTION)
        self.on_disk = {}
        for name, title in OFFLINE_PAGE_TITLES.items():
            self.write_artifact(name, offline_page(title))

    def write_artifact(self, name, html, run_dir=None):
        """把一份離線頁寫進 run 目錄，並記下它在磁碟上的位元組。"""
        path = (run_dir or self.run_dir) / name
        path.write_text(html, encoding="utf-8")
        if run_dir is None:
            self.on_disk[name] = path.read_bytes()
        return path

    def get(self, path, headers=()):
        lines = ["GET {} HTTP/1.1".format(path), "Host: 127.0.0.1"]
        lines += ["{}: {}".format(name, value) for name, value in headers]
        connection = _Socket(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
        self.handler(connection, ("127.0.0.1", 54321), None)
        return Response(connection.outgoing.getvalue())

    def open_artifact(self, name, headers=(), run_id=RUN_ID):
        return self.get("/run/{}/{}".format(run_id, name), headers=headers)

    def inserted(self, name, response):
        """回應比磁碟上那份多出來的那一段，順便釘住它插在哪裡。

        前綴一路到開頭的 ``<body>``、後綴是 ``<body>`` 之後的全部，兩邊都要與磁碟
        上的位元組完全相同——所以這個方法本身就是「插入點在 ``<body>`` 之後，其他
        位元組原封不動」這句話的量測。
        """
        head, mark, tail = self.on_disk[name].partition(BODY_OPEN)
        self.assertTrue(mark, "fixture 沒有 <body>，測不到插入點")
        opening = head + mark
        sent = response.body_bytes
        self.assertTrue(sent.startswith(opening), "插入點不在 <body> 之後")
        self.assertTrue(sent.endswith(tail), "<body> 之後的內容被改動了")
        return sent[len(opening) : len(sent) - len(tail)].decode("utf-8")

    def fingerprint(self):
        """``runs/`` 底下每個檔案的雜湊，用來證明磁碟真的沒被動過。"""
        runs_root = self.data_root / "runs"
        return {
            str(path.relative_to(runs_root)): sha256(path.read_bytes()).hexdigest()
            for path in sorted(runs_root.rglob("*"))
            if path.is_file()
        }

    def records(self):
        path = self.data_root / "logs" / ACTIVE_LOG_NAME
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class OfflinePageNavigationTest(NavInjectionFixture, unittest.TestCase):
    """經伺服器瀏覽時，兩頁都多出站內五導覽，而且只多出那一段。"""

    def test_the_report_page_arrives_with_the_sites_five_tabs(self):
        response = self.open_artifact(REPORT_ARTIFACT)

        self.assertEqual(200, response.status)
        self.assertEqual(
            SITE_TABS, tab_targets(self.inserted(REPORT_ARTIFACT, response))
        )

    def test_the_transcript_page_arrives_with_the_same_five_tabs(self):
        response = self.open_artifact(DEBATE_ARTIFACT)

        self.assertEqual(200, response.status)
        self.assertEqual(
            SITE_TABS, tab_targets(self.inserted(DEBATE_ARTIFACT, response))
        )

    def test_the_two_report_tabs_open_the_run_being_read_not_another_one(self):
        """讀者正在看這一個 run，導覽就指向這一個 run。

        另外那個也有兩份報告的 run 是判別條件：沒有它，「指向自身」與「指向隨便哪
        一個」會是同一個答案，這條測試就什麼都沒釘住。
        """
        write_run(
            self.data_root,
            OTHER_RUN_ID,
            OTHER_QUESTION,
            artifacts=(
                (REPORT_ARTIFACT, offline_page("另一場的報告")),
                (DEBATE_ARTIFACT, offline_page("另一場的辯論")),
            ),
        )

        fragment = self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))

        self.assertEqual(SITE_TABS["市場報告"], tab_targets(fragment)["市場報告"])
        self.assertEqual(SITE_TABS["完整辯論"], tab_targets(fragment)["完整辯論"])
        self.assertNotIn(OTHER_RUN_ID, fragment)

    def test_the_tab_for_the_file_being_read_is_marked_as_the_current_page(self):
        report = self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))
        debate = self.inserted(DEBATE_ARTIFACT, self.open_artifact(DEBATE_ARTIFACT))

        self.assertIn(
            '<a href="{}" aria-current="page">市場報告</a>'.format(SITE_TABS["市場報告"]),
            report,
        )
        self.assertIn(
            '<a href="{}" aria-current="page">完整辯論</a>'.format(SITE_TABS["完整辯論"]),
            debate,
        )
        self.assertNotIn('aria-current="page">完整辯論', report)

    def test_the_navigation_carries_no_script_and_no_inline_handler(self):
        """CSP 說 ``script-src 'none'``，注入的那一段不准是唯一的例外。"""
        for name in OFFLINE_PAGE_TITLES:
            fragment = self.inserted(name, self.open_artifact(name))

            self.assertNotIn("<script", fragment.lower(), name)
            self.assertIsNone(INLINE_HANDLER.search(fragment), name)
            self.assertNotIn("javascript:", fragment.lower(), name)

    def test_the_reply_still_carries_the_sites_own_policy(self):
        response = self.open_artifact(REPORT_ARTIFACT)

        self.assertEqual(
            server_module.CONTENT_SECURITY_POLICY,
            response.headers["Content-Security-Policy"],
        )

    def test_the_length_header_counts_the_bytes_that_were_actually_sent(self):
        """注入之後長度要跟著變，否則瀏覽器會讀到半頁。"""
        response = self.open_artifact(REPORT_ARTIFACT)

        self.assertEqual(
            len(response.body_bytes), int(response.headers["Content-Length"])
        )
        self.assertGreater(len(response.body_bytes), len(self.on_disk[REPORT_ARTIFACT]))


class InjectedLandmarkTest(NavInjectionFixture, unittest.TestCase):
    """回應裡每個 nav landmark 的名稱都是唯一的（F-B07-3）。"""

    NAMED_NAV = '<nav class="page-tabs" aria-label="主要頁面"><a href="report.html">市場報告</a></nav>'

    def offline_page_with_its_own_nav(self, title):
        return (
            '<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">'
            "<title>{}</title></head><body>{}<main>內容</main></body></html>"
        ).format(title, self.NAMED_NAV)

    def test_the_injected_bar_does_not_reuse_the_bundles_landmark_name(self):
        """離線 bundle 自己那條導覽也叫「主要頁面」，注入的不能同名。"""
        self.write_artifact(
            REPORT_ARTIFACT, self.offline_page_with_its_own_nav("市場判斷報告")
        )

        names = NAV_LANDMARK.findall(self.open_artifact(REPORT_ARTIFACT).body)

        self.assertEqual(sorted(names), sorted(set(names)), names)
        self.assertIn(SITE_NAV_LABEL, names)
        self.assertIn("主要頁面", names)

    def test_the_injected_bar_is_one_landmark_and_not_two(self):
        """設定和其他四個分開，靠的是位置而不是第二個 landmark。"""
        fragment = self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))

        self.assertEqual([SITE_NAV_LABEL], NAV_LANDMARK.findall(fragment))
        self.assertIn(
            '<a href="/settings" class="{}">設定</a>'.format(SITE_NAV_ADMIN_CLASS),
            fragment,
        )


class InjectedStyleTest(NavInjectionFixture, unittest.TestCase):
    """注入內容自帶 scoped 樣式：取自 design_tokens、只畫自己。"""

    def style_of(self, fragment):
        blocks = STYLE_BLOCK.findall(fragment)
        self.assertEqual(1, len(blocks), "注入段落應該只有一個 <style>")
        return blocks[0]

    def test_the_bar_brings_its_own_style_block(self):
        style = self.style_of(
            self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))
        )

        self.assertIn(".{}{{".format(SITE_NAV_CLASS), style)

    def test_every_selector_is_rooted_at_the_injected_bars_own_class(self):
        """一條都不准碰到離線頁原有的元素——包含它自己那條 ``.page-tabs``。"""
        style = self.style_of(
            self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))
        )

        selectors = [
            part.strip()
            for rule in CSS_RULE.findall(style)
            for part in rule.split(",")
        ]

        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(
                selector.startswith(".{}".format(SITE_NAV_CLASS)), selector
            )
        self.assertNotIn(":root", style)
        self.assertNotIn(".page-tabs", style)

    def test_the_values_are_the_design_tokens_and_not_a_second_palette(self):
        style = self.style_of(
            self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))
        )

        for name in ("accent", "link", "surface", "border", "muted"):
            self.assertIn(
                "--{}:{};".format(name, design_tokens.PALETTE[name]), style, name
            )
        for name in ("space_1", "radius_md", "size_sm", "font_sans", "dim"):
            self.assertIn(
                "--{}:{};".format(name.replace("_", "-"), design_tokens.SCALE[name]),
                style,
                name,
            )

    def test_an_old_paper_white_run_gets_the_same_styled_bar(self):
        """Spec 已核准代價：舊 run 紙白頁上浮的是**新設計風**導覽列。"""
        self.write_artifact(REPORT_ARTIFACT, paper_white_page("舊版市場報告"))

        fragment = self.inserted(REPORT_ARTIFACT, self.open_artifact(REPORT_ARTIFACT))
        style = self.style_of(fragment)

        self.assertIn("--accent:{};".format(design_tokens.PALETTE["accent"]), style)
        self.assertEqual(SITE_TABS, tab_targets(fragment))
        self.assertNotIn("<script", fragment.lower())


class PagesThatAreLeftAloneTest(NavInjectionFixture, unittest.TestCase):
    """一個位元組都不加的幾種情形。"""

    def test_a_page_with_no_body_tag_is_sent_exactly_as_it_is_on_disk(self):
        """fail-open：導覽是附加價值，插不進去也要把頁面送出去。"""
        self.write_artifact(REPORT_ARTIFACT, PAGE_WITH_NO_BODY_TAG)

        response = self.open_artifact(REPORT_ARTIFACT)

        self.assertEqual(200, response.status)
        self.assertEqual(self.on_disk[REPORT_ARTIFACT], response.body_bytes)

    def test_a_body_tag_that_only_exists_inside_a_comment_is_not_one(self):
        """F-B07-1：註解裡的 ``<body>`` 不是插入點。"""
        self.write_artifact(
            REPORT_ARTIFACT,
            "<!doctype html><title>註解</title><!-- <body> 這裡只是註解 -->"
            "<p>內容</p>",
        )

        response = self.open_artifact(REPORT_ARTIFACT)

        self.assertEqual(200, response.status)
        self.assertEqual(self.on_disk[REPORT_ARTIFACT], response.body_bytes)

    def test_the_preview_frame_on_the_detail_page_gets_the_file_untouched(self):
        """run 詳情頁把同一個網址嵌在 ``<iframe>`` 裡。

        在預覽框裡放站內導覽，等於給讀者一顆會把整個網站拉進小框裡的按鈕。瀏覽器
        自己會說這一次是不是嵌入，所以這件事不必猜。
        """
        for destination in ("iframe", "frame"):
            response = self.open_artifact(
                REPORT_ARTIFACT, headers=(("Sec-Fetch-Dest", destination),)
            )

            self.assertEqual(200, response.status, destination)
            self.assertEqual(
                self.on_disk[REPORT_ARTIFACT], response.body_bytes, destination
            )

    def test_a_top_level_read_is_still_the_one_that_gets_the_navigation(self):
        """判別條件：擋掉的是嵌入，不是所有帶 ``Sec-Fetch-Dest`` 的請求。"""
        response = self.open_artifact(
            REPORT_ARTIFACT, headers=(("Sec-Fetch-Dest", "document"),)
        )

        self.assertEqual(
            SITE_TABS, tab_targets(self.inserted(REPORT_ARTIFACT, response))
        )

    def test_a_file_this_server_does_not_link_is_not_served_at_all(self):
        """非 ``report.html``／``debate.html`` 的檔案沒有被注入，因為根本沒被送。"""
        exported = self.run_dir / "report.pdf"
        exported.write_bytes(b"%PDF-1.7\n%% \xe9\x9b\xa2\xe7\xb7\x9a\n")

        response = self.open_artifact("report.pdf")

        self.assertEqual(404, response.status)
        self.assertNotIn(exported.read_bytes(), response.body_bytes)


class NavigationThatCannotBeBuiltTest(NavInjectionFixture, unittest.TestCase):
    """F-B07-2：導覽組不出來時，讀者拿到的是報告，不是 500。"""

    def test_a_report_still_opens_when_its_navigation_cannot_be_assembled(self):
        with mock.patch.object(
            server_module, "run_artifacts", side_effect=OSError("目錄讀不到")
        ):
            response = self.open_artifact(REPORT_ARTIFACT)

        self.assertEqual(200, response.status)
        self.assertEqual(self.on_disk[REPORT_ARTIFACT], response.body_bytes)

    def test_that_failure_is_recorded_rather_than_swallowed(self):
        with mock.patch.object(
            server_module, "site_nav_fragment", side_effect=ValueError("壞掉了")
        ):
            self.open_artifact(REPORT_ARTIFACT)

        events = [record["event"] for record in self.records()]

        self.assertIn("artifact_nav_unavailable", events)
        self.assertNotIn("request_failed", events)

    def test_the_navigation_asks_only_which_of_the_two_files_are_there(self):
        """窄 helper：一個只有 question／manifest 的 run 也要能回答。

        這個 fixture 沒有 ``votes.json``、沒有 ``evidence.jsonl``——正是 F-B07-2
        裡會把整個報告拖下水的那些紀錄。
        """
        self.assertEqual(
            {REPORT_ARTIFACT: True, DEBATE_ARTIFACT: True},
            run_artifacts(self.data_root, RUN_ID),
        )

        (self.run_dir / DEBATE_ARTIFACT).unlink()

        self.assertEqual(
            {REPORT_ARTIFACT: True, DEBATE_ARTIFACT: False},
            run_artifacts(self.data_root, RUN_ID),
        )

    def test_a_run_that_resolves_to_nothing_is_not_an_error(self):
        self.assertIsNone(run_artifacts(self.data_root, "20260101T000000Z-nil-999999"))

    def test_a_run_with_nothing_to_open_still_gets_five_tabs(self):
        """導覽不會因為缺檔就變短——那會是兩套導覽。"""
        fragment = site_nav_fragment(RUN_ID, None)

        self.assertIn('<span role="link" aria-disabled="true">市場報告</span>', fragment)
        self.assertIn('<span role="link" aria-disabled="true">完整辯論</span>', fragment)
        self.assertEqual(
            {"即時辯論": "/", "歷史與命中率": "/history", "設定": "/settings"},
            tab_targets(fragment),
        )


class TheFilesThemselvesTest(NavInjectionFixture, unittest.TestCase):
    """磁碟上的那兩份檔案，服務前後一個位元組都不變。"""

    def test_serving_both_pages_changes_nothing_under_runs(self):
        before = self.fingerprint()

        for name in OFFLINE_PAGE_TITLES:
            self.assertEqual(200, self.open_artifact(name).status, name)

        self.assertEqual(before, self.fingerprint())

    def test_the_files_on_disk_carry_no_link_into_this_site(self):
        """直接開檔／分享出去的那一份，仍然是離線自足的：沒有站內連結。"""
        for name in OFFLINE_PAGE_TITLES:
            self.assertEqual(200, self.open_artifact(name).status, name)

        for name in OFFLINE_PAGE_TITLES:
            disk_text = (self.run_dir / name).read_text(encoding="utf-8")

            self.assertEqual(self.on_disk[name], (self.run_dir / name).read_bytes())
            for label, target in SITE_TABS.items():
                self.assertNotIn('href="{}"'.format(target), disk_text, label)


class BodyTagScanTest(unittest.TestCase):
    """插入點怎麼找：真的那個 ``<body>``，找不準就不找（F-B07-1）。"""

    def test_the_plain_tag_is_found(self):
        self.assertEqual(6, body_tag_end(b"<body><p>x</p>"))

    def test_a_quoted_attribute_value_may_hold_the_tags_own_terminator(self):
        page = b'<body data-note="1 > 0"><p>x</p>'

        self.assertEqual(page.index(b"<p>"), body_tag_end(page))

    def test_a_single_quoted_attribute_value_counts_too(self):
        page = b"<body data-note='a > b' id=top><p>x</p>"

        self.assertEqual(page.index(b"<p>"), body_tag_end(page))

    def test_a_tag_inside_a_comment_is_not_the_tag(self):
        page = b"<!-- <body> --><body id=real><p>x</p>"

        self.assertEqual(page.index(b"<p>"), body_tag_end(page))

    def test_a_comment_that_is_never_closed_leaves_no_insertion_point(self):
        self.assertIsNone(body_tag_end(b"<!-- <body> and then nothing"))

    def test_an_attribute_list_that_runs_off_the_end_leaves_no_insertion_point(self):
        self.assertIsNone(body_tag_end(b'<body data-note="never closed'))

    def test_the_tag_is_recognised_however_it_was_spelled(self):
        self.assertEqual(6, body_tag_end(b"<BODY>x"))

    def test_an_element_whose_name_merely_starts_with_body_is_not_it(self):
        self.assertIsNone(body_tag_end(b"<bodyguard>x</bodyguard>"))

    def test_a_page_with_no_tag_at_all_has_no_insertion_point(self):
        self.assertIsNone(body_tag_end("<p>沒有標籤</p>".encode("utf-8")))


class ArtifactNavInjectorTest(unittest.TestCase):
    """注入器自己：給一份位元組，插入或原樣退回，兩條路徑。"""

    NAV = '<nav class="hoya-site-nav"><a href="/">即時辯論</a></nav>'

    def test_the_navigation_lands_immediately_after_the_opening_tag(self):
        page = b"<html><head></head><body><h1>x</h1></body></html>"

        sent = artifact_with_site_nav(page, self.NAV)

        self.assertEqual(
            "<html><head></head><body>" + self.NAV + "<h1>x</h1></body></html>",
            sent.decode("utf-8"),
        )

    def test_a_body_tag_with_attributes_is_still_the_insertion_point(self):
        page = b'<body class="report" id="top">\xe5\x85\xa7\xe5\xae\xb9</body>'

        sent = artifact_with_site_nav(page, self.NAV).decode("utf-8")

        self.assertTrue(sent.startswith('<body class="report" id="top">' + self.NAV))

    def test_nothing_is_inserted_into_a_quoted_attribute_value(self):
        page = b'<body data-note="1 > 0">\xe5\x85\xa7\xe5\xae\xb9</body>'

        sent = artifact_with_site_nav(page, self.NAV).decode("utf-8")

        self.assertTrue(sent.startswith('<body data-note="1 > 0">' + self.NAV))

    def test_a_page_with_no_opening_tag_comes_back_exactly_as_it_arrived(self):
        page = "<!doctype html><style>body{margin:0;}</style><p>沒有標籤</p>".encode(
            "utf-8"
        )

        self.assertEqual(page, artifact_with_site_nav(page, self.NAV))

    def test_only_the_first_opening_tag_is_used(self):
        """一份頁面只有一個 ``<body>``；真的出現第二個時不再插一次。"""
        page = b"<body>1</body><body>2</body>"

        self.assertEqual(1, artifact_with_site_nav(page, self.NAV).count(b"<nav"))


if __name__ == "__main__":
    unittest.main()
