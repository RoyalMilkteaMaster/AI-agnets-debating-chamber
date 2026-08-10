"""Ticket 06: 匯出 PDF —— run 目錄的第三條寫入路徑，只新增 ``.pdf``。

三件事在這裡被釘住，而且刻意各自獨立：

**匯出模組本身。** ``export_run_pdfs`` 拿到 run 識別與一個轉換器，把該 run 現成
的兩個 HTML 轉成兩個 PDF。轉換器是注入的，所以**這個檔案裡沒有任何測試會啟動
瀏覽器**；每一條失敗路徑都由假轉換器製造，包含「宣稱成功卻什麼都沒寫」這一種。

**預設轉換器的接縫。** 預設實作是 Edge 無頭模式，而這裡驗的是它的**介面**：路徑
有沒有經過轉換、轉換後的值有沒有交給執行器、失敗有沒有變成看得懂的原因。命令列
的旗標拼字**不驗**——Spec〈測試決策／不應耦合的實作細節〉明文把它排除，理由是那
種斷言會在 Edge 改名一個旗標的那天失敗（而匯出仍然好的），也會在路徑轉換壞掉的
那天通過（只要拼字沒變）。

**端點與頁面。** ``POST /run/<id>/export-pdf`` 的成功與三條失敗路徑、``GET`` 不
寫檔、按鈕的可用與停用兩態、以及會寫檔的頁尾。

真實 Edge 在本執行環境不可用，這個檔案也沒有一個測試假裝它可用：所有 PDF 內容
都是假轉換器寫的位元組。

fixtures 沿用 ``tests/test_webapp.py`` 的 ``PageFixture`` 與 ``write_run``，理由
和 Ticket 05 一樣：第二份「完成的 run 長什麼樣」會和第一份漂移。
"""

import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

# 這個模組要能用兩種名字被載入：``discover -s tests`` 自己會把這個目錄放上
# sys.path，從 Code Root 執行 ``python3 -m unittest tests.test_webapp_pdf_export``
# 不會。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents.fake_provider import FakeProvider  # noqa: E402
from hoya_market_agents.run_controller import RunController  # noqa: E402
from hoya_market_agents.run_store import RunStore, resolve_run_dir  # noqa: E402
from hoya_market_agents.run_verifier import verify_run  # noqa: E402
from hoya_market_agents.webapp import pages, pdf_export  # noqa: E402
from hoya_market_agents.webapp import server  # noqa: E402
from hoya_market_agents.webapp.server import webapp_handler_class  # noqa: E402
from tests.fakes import FixedClock, ScriptedTokenSource  # noqa: E402
from test_webapp import PageFixture, write_run  # noqa: E402

RUN_ID = "20260801T020000Z-btc-aaaa11"
QUESTION = "BTC 未來七天會不會漲"


def fake_pdf_bytes(source):
    """What the假轉換器 writes: real PDF magic, obviously synthetic content."""
    return "%PDF-1.7\n{} 的假 PDF\n%%EOF\n".format(Path(source).name).encode("utf-8")


class FakeConverter:
    """A converter the test drives, so no test in this file starts a browser.

    ``fail_on`` names the source file whose conversion raises; ``write_nothing``
    makes it return without writing anything, which is the fixture for "轉換器
    宣稱成功但沒有內容" — the case that would otherwise leave a 0 位元 PDF.
    """

    REASON = "假轉換器就是要失敗"

    def __init__(self, fail_on=None, write_nothing=False, reason=REASON):
        self.calls = []
        self.read = {}
        self.fail_on = fail_on
        self.write_nothing = write_nothing
        self.reason = reason

    def __call__(self, source, target):
        source, target = Path(source), Path(target)
        self.calls.append((source, target))
        # 讀得到才算真的收到來源：這是「轉換器拿到的是這個 run 自己的 HTML」的
        # 證據，而不是一個看起來像路徑的字串。
        self.read[source.name] = source.read_text(encoding="utf-8")
        if self.fail_on is not None and source.name == self.fail_on:
            raise pdf_export.PdfConversionError(self.reason)
        if self.write_nothing:
            return
        target.write_bytes(fake_pdf_bytes(source))

    @property
    def sources(self):
        return [source.name for source, _ in self.calls]

    @property
    def targets(self):
        return [target for _, target in self.calls]


def failing_second_promotion(reason="第二次上名失敗"):
    """Patch the promotion so the first file lands and the second cannot.

    The boundary is :func:`os.link`, because that is what gives a name to a
    finished file — and refuses when the name is taken. One helper rather than four
    copies of the same stub: the injection point is a property of the module, and
    four copies of it would all have to be found the day it changes.
    """
    real_link = pdf_export.os.link
    calls = []

    def link(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError(reason)
        return real_link(source, target)

    return mock.patch.object(pdf_export.os, "link", link)


class RunDirectoryFixture(unittest.TestCase):
    """One finished run in a temporary Data Root, plus a fingerprint of it."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        self.run_dir = write_run(self.data_root, RUN_ID, QUESTION)

    def listing(self):
        return sorted(path.name for path in self.run_dir.iterdir())

    def fingerprint(self):
        """Every file in the run directory, by name, content hash and mtime."""
        return {
            path.name: (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in sorted(self.run_dir.iterdir())
            if path.is_file()
        }

    def pdfs(self):
        return sorted(path.name for path in self.run_dir.glob("*.pdf"))

    def export(self, converter=None, run_id=RUN_ID):
        return pdf_export.export_run_pdfs(
            self.data_root, run_id, convert=converter or FakeConverter()
        )


class ExportModuleTest(RunDirectoryFixture):
    """匯出模組的公開介面：兩個 PDF 落地，來源是這個 run 自己的 HTML。"""

    def test_both_pdfs_land_in_the_run_directory(self):
        result = self.export()

        self.assertTrue(result.ok, result.message)
        self.assertEqual(["debate.pdf", "report.pdf"], self.pdfs())

    def test_what_it_reports_written_is_what_is_on_disk(self):
        result = self.export()

        self.assertEqual(("report.pdf", "debate.pdf"), result.written)
        for name in result.written:
            self.assertTrue((self.run_dir / name).is_file(), name)

    def test_the_converter_is_handed_this_runs_own_html(self):
        converter = FakeConverter()

        self.export(converter)

        self.assertEqual(["report.html", "debate.html"], converter.sources)
        for name, text in converter.read.items():
            self.assertEqual(
                (self.run_dir / name).read_text(encoding="utf-8"), text, name
            )

    def test_each_pdf_holds_what_the_converter_produced_for_it(self):
        self.export()

        for source, target in pdf_export.EXPORTS:
            self.assertEqual(
                fake_pdf_bytes(source), (self.run_dir / target).read_bytes(), target
            )

    def test_the_converter_never_writes_the_final_name_itself(self):
        """轉換器只看得到暫存路徑；``.pdf`` 這個名字是模組移上去的。"""
        converter = FakeConverter()

        self.export(converter)

        for target in converter.targets:
            self.assertNotIn(target.name, ("report.pdf", "debate.pdf"))
            self.assertEqual(self.run_dir, target.parent)

    def test_nothing_but_the_two_pdfs_is_added_and_nothing_else_changes(self):
        before = self.fingerprint()

        self.export()
        after = self.fingerprint()

        self.assertEqual({"report.pdf", "debate.pdf"}, set(after) - set(before))
        self.assertEqual(before, {name: after[name] for name in before})

    def test_no_temporary_file_is_left_behind(self):
        self.export()

        self.assertEqual(
            sorted(["debate.pdf", "report.pdf"] + list(_BASE_FILES)), self.listing()
        )

    def test_a_second_export_is_refused_instead_of_overwriting_its_own_output(self):
        """票面範圍 4 沒有自產例外：「不得改寫既有檔案」包含上一次匯出的 PDF。

        這條測試取代了原本把覆寫釘成預期行為的那一條（Reviewer A｜A-F01）。當時
        的實作無條件 ``os.replace``，兩次匯出後兩份 PDF 的 SHA-256 全變，而頁面
        還寫著「既有的檔案一個都不會改」。
        """
        self.export()
        before = self.fingerprint()

        result = self.export()

        self.assertFalse(result.ok)
        self.assertEqual(pdf_export.ALREADY_EXPORTED, result.state)
        self.assertEqual((), result.written)
        self.assertEqual(before, self.fingerprint())

    def test_a_refusal_names_the_files_that_are_already_there(self):
        self.export()

        result = self.export()

        for name in pdf_export.EXPORT_TARGETS:
            self.assertIn(name, result.message, name)

    def test_one_pdf_already_there_is_enough_to_refuse(self):
        """半套也不覆寫：唯一一個 ``.pdf`` 也是既有檔案。"""
        (self.run_dir / "report.pdf").write_bytes("%PDF-1.7 舊的\n".encode("utf-8"))
        before = self.fingerprint()

        result = self.export(FakeConverter())

        self.assertEqual(pdf_export.ALREADY_EXPORTED, result.state)
        self.assertIn("report.pdf", result.message)
        self.assertEqual(before, self.fingerprint())

    def test_a_refusal_never_reaches_the_converter_or_makes_a_temporary_file(self):
        self.export()
        listing = self.listing()
        converter = FakeConverter()

        self.export(converter)

        self.assertEqual([], converter.calls)
        self.assertEqual(listing, self.listing())

    def test_a_run_whose_pages_are_gone_but_pdfs_remain_is_told_about_the_pdfs(self):
        """兩種拒絕同時成立時，先說已經匯出過——那才是「為什麼不能按」的答案。"""
        self.export()
        (self.run_dir / "debate.html").unlink()

        result = self.export()

        self.assertEqual(pdf_export.ALREADY_EXPORTED, result.state)

    def test_the_default_converter_is_the_edge_one(self):
        """沒有注入時用的是 Edge 無頭模式——用 patch 問，不真的叫 Edge。"""
        converter = FakeConverter()
        with mock.patch.object(
            pdf_export, "EdgeConverter", return_value=converter
        ) as default:
            result = pdf_export.export_run_pdfs(self.data_root, RUN_ID)

        self.assertTrue(default.called)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(["report.html", "debate.html"], converter.sources)


class ExportFailureTest(RunDirectoryFixture):
    """每一種失敗都說出實際原因，而且不留下任何 ``.pdf``。"""

    def test_a_run_id_that_names_nothing_is_reported_not_guessed(self):
        result = self.export(run_id="20261231T235959Z-btc-zzzz99")

        self.assertFalse(result.ok)
        self.assertEqual(pdf_export.RUN_MISSING, result.state)
        self.assertIn("20261231T235959Z-btc-zzzz99", result.message)
        self.assertEqual([], self.pdfs())

    def test_a_run_id_that_is_not_a_run_id_is_the_same_answer(self):
        result = self.export(run_id="../etc")

        self.assertEqual(pdf_export.RUN_MISSING, result.state)
        self.assertEqual([], self.pdfs())

    def test_a_missing_source_html_names_the_file_and_creates_nothing(self):
        (self.run_dir / "debate.html").unlink()
        before = self.fingerprint()
        converter = FakeConverter()

        result = self.export(converter)

        self.assertEqual(pdf_export.SOURCE_MISSING, result.state)
        self.assertIn("debate.html", result.message)
        self.assertEqual([], self.pdfs())
        self.assertEqual([], converter.calls)
        self.assertEqual(before, self.fingerprint())

    def test_a_converter_that_fails_leaves_no_pdf_at_all(self):
        before = self.fingerprint()

        result = self.export(FakeConverter(fail_on="report.html"))

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual((), result.written)
        self.assertEqual([], self.pdfs())
        self.assertEqual(before, self.fingerprint())

    def test_the_failure_message_carries_the_reason_the_converter_gave(self):
        result = self.export(FakeConverter(fail_on="report.html"))

        self.assertIn(FakeConverter.REASON, result.message)
        self.assertIn("report.html", result.message)

    def test_a_failure_on_the_second_file_rolls_the_first_one_back(self):
        """一半的匯出就是半成品：報告有 PDF、辯論沒有，不算成功。"""
        before = self.fingerprint()

        result = self.export(FakeConverter(fail_on="debate.html"))

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual([], self.pdfs())
        self.assertEqual(before, self.fingerprint())

    def test_a_converter_that_writes_nothing_is_a_failure_not_a_zero_byte_pdf(self):
        result = self.export(FakeConverter(write_nothing=True))

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual([], self.pdfs())
        self.assertEqual(sorted(_BASE_FILES), self.listing())

    def test_an_exception_the_module_never_declared_is_still_reported(self):
        """注入的轉換器可以壞在任何地方；頁面要拿到原因，不是 traceback。"""

        def explode(_source, _target):
            raise RuntimeError("轉換器內部壞了")

        result = self.export(explode)

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertIn("RuntimeError", result.message)
        self.assertIn("轉換器內部壞了", result.message)
        self.assertEqual([], self.pdfs())

    def test_a_promotion_that_fails_halfway_puts_the_directory_back(self):
        """回歸｜A-F02：第二份上名失敗時，第一份不准留在磁碟上。

        原本的實作只回報「這次寫入的是 report.pdf」就結束，於是 run 目錄留著一份
        單邊的新 PDF——正是驗收 5 說不可以的半成品。定向重現方式就是這裡做的：
        讓第二次 ``os.replace`` 拋 OSError。
        """
        before = self.fingerprint()

        with failing_second_promotion():
            result = self.export()

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual((), result.written)
        self.assertEqual([], self.pdfs())
        self.assertEqual(before, self.fingerprint())
        self.assertEqual(sorted(_BASE_FILES), self.listing())

    def test_that_failure_says_the_directory_is_back_as_it_was(self):
        with failing_second_promotion():
            result = self.export()

        self.assertIn("第二次上名失敗", result.message)
        self.assertIn("沒有留下任何 PDF", result.message)

    def test_a_rollback_leaves_a_file_that_is_not_this_calls_alone(self):
        """回歸｜A-F02 的並行證據：``_undo`` 不能只按檔名刪除。

        Reviewer A 讓「成功請求」與「promotion 失敗的請求」交錯，失敗方的 rollback
        按檔名刪掉了成功方剛上名的 ``report.pdf``，錯誤頁卻說「目錄已回復」。這裡把
        那個交錯壓成一個確定的情境：這次呼叫上名成功後，那個名字換成了別人的檔案
        （不同 inode），然後這次呼叫的第二份失敗。rollback 只能動自己建的東西。
        """
        foreign = "%PDF-1.7 別人後來放的\n".encode("utf-8")
        real_link = pdf_export.os.link
        calls = []

        def swap_then_fail(source, target):
            calls.append(target)
            if len(calls) == 1:
                real_link(source, target)
                replaced = Path(target)
                replaced.unlink()
                replaced.write_bytes(foreign)
                return None
            raise OSError("第二次上名失敗")

        with mock.patch.object(pdf_export.os, "link", swap_then_fail):
            result = self.export()

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual(foreign, (self.run_dir / "report.pdf").read_bytes())
        self.assertEqual((), result.written)
        self.assertIn("report.pdf", result.message)
        self.assertNotIn("回到匯出前的狀態", result.message)

    def test_a_rollback_that_cannot_remove_a_file_says_which_one_is_left(self):
        """不能空口說已經復原：清不掉就要指名，並算進 written。"""
        with failing_second_promotion():
            with mock.patch.object(
                pdf_export.os, "remove", side_effect=OSError("清不掉")
            ):
                result = self.export()

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual(("report.pdf",), result.written)
        self.assertIn("report.pdf", result.message)
        self.assertNotIn("沒有留下任何 PDF", result.message)

    def test_a_run_directory_that_cannot_be_written_says_so(self):
        mode = self.run_dir.stat().st_mode
        self.run_dir.chmod(0o500)
        self.addCleanup(self.run_dir.chmod, mode)

        result = self.export()

        self.assertEqual(pdf_export.CONVERSION_FAILED, result.state)
        self.assertEqual([], self.pdfs())


class GateConverter:
    """A converter whose first call waits until the test lets it finish.

    This is how a second request is driven **while the first is still inside the
    export**, which is the window both Reviewers' probes went through: the check
    for existing PDFs, the conversion and the promotion used to be three separate
    moments with nothing holding the run between them.
    """

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def __call__(self, source, target):
        source, target = Path(source), Path(target)
        self.calls.append(source.name)
        if len(self.calls) == 1:
            self.entered.set()
            if not self.release.wait(10):
                raise AssertionError("測試沒有放行第一個請求")
        target.write_bytes(fake_pdf_bytes(source))


class ExportConcurrencyTest(RunDirectoryFixture):
    """回歸｜並行：一個 run 同時只能有一次匯出走完「檢查→轉換→上名」。

    兩位 Reviewer 的並行探針證明修正不完整：存在檢查與上名之間沒有任何東西держ住
    這個 run，於是兩個同步請求可以都通過檢查、都回報成功，後者覆寫前者剛建立的
    PDF；失敗方的 rollback 還會按檔名刪掉成功方的檔案。這一組測試就是那兩個場景。
    """

    def export_in_a_thread(self, converter, into):
        def go():
            into.append(
                pdf_export.export_run_pdfs(self.data_root, RUN_ID, convert=converter)
            )

        thread = threading.Thread(target=go)
        thread.start()
        return thread

    def test_two_exports_at_once_end_with_one_export_and_one_refusal(self):
        gate = GateConverter()
        first = []
        thread = self.export_in_a_thread(gate, first)
        self.assertTrue(gate.entered.wait(10), "第一個請求沒有進到轉換")
        second_converter = FakeConverter()

        second = pdf_export.export_run_pdfs(
            self.data_root, RUN_ID, convert=second_converter
        )

        gate.release.set()
        thread.join(10)
        self.assertTrue(first, "第一個請求沒有回來")
        self.assertTrue(first[0].ok, first[0].message)
        self.assertFalse(second.ok, second.message)
        self.assertEqual(pdf_export.IN_PROGRESS, second.state)
        self.assertEqual((), second.written)
        self.assertEqual([], second_converter.calls)

    def test_the_winners_two_pdfs_are_the_ones_on_disk_and_nothing_else_is(self):
        gate = GateConverter()
        first = []
        thread = self.export_in_a_thread(gate, first)
        self.assertTrue(gate.entered.wait(10))

        pdf_export.export_run_pdfs(self.data_root, RUN_ID, convert=FakeConverter())

        gate.release.set()
        thread.join(10)
        self.assertEqual(["debate.pdf", "report.pdf"], self.pdfs())
        for source, target in pdf_export.EXPORTS:
            self.assertEqual(
                fake_pdf_bytes(source), (self.run_dir / target).read_bytes(), target
            )
        self.assertEqual(
            sorted(list(_BASE_FILES) + ["debate.pdf", "report.pdf"]), self.listing()
        )

    def test_a_burst_of_requests_produces_exactly_one_export(self):
        """沒有 barrier 的版本：誰贏不確定，但「恰好一個」必須成立。"""
        results = []
        guard = threading.Lock()

        def go():
            result = pdf_export.export_run_pdfs(
                self.data_root, RUN_ID, convert=FakeConverter()
            )
            with guard:
                results.append(result)

        threads = [threading.Thread(target=go) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        self.assertEqual(6, len(results))
        self.assertEqual(1, len([result for result in results if result.ok]), results)
        for result in results:
            if not result.ok:
                self.assertIn(
                    result.state,
                    (pdf_export.IN_PROGRESS, pdf_export.ALREADY_EXPORTED),
                    result.state,
                )
        self.assertEqual(["debate.pdf", "report.pdf"], self.pdfs())
        self.assertEqual(
            sorted(list(_BASE_FILES) + ["debate.pdf", "report.pdf"]), self.listing()
        )

    def test_the_run_that_is_busy_is_this_run_and_not_every_run(self):
        """鑑別力：鎖是 per-run 的，別的 run 不該被它擋住。"""
        other = write_run(self.data_root, "20260802T020000Z-btc-eeee55", "另一場")
        gate = GateConverter()
        first = []
        thread = self.export_in_a_thread(gate, first)
        self.assertTrue(gate.entered.wait(10))

        elsewhere = pdf_export.export_run_pdfs(
            self.data_root, "20260802T020000Z-btc-eeee55", convert=FakeConverter()
        )

        gate.release.set()
        thread.join(10)
        self.assertTrue(elsewhere.ok, elsewhere.message)
        self.assertEqual(
            ["debate.pdf", "report.pdf"], sorted(p.name for p in other.glob("*.pdf"))
        )

    def test_a_name_that_appeared_during_the_export_is_not_overwritten(self):
        """縱深防禦：就算檢查通過後才有人建了那個名字，上名也不准蓋過去。

        用 ``os.link`` 的語意做到：目標存在就 ``FileExistsError``，不是靜靜覆寫。
        """
        real_link = pdf_export.os.link
        calls = []

        def appear_then_link(source, target):
            calls.append(target)
            if len(calls) == 1:
                Path(target).write_bytes("%PDF-1.7 別人的\n".encode("utf-8"))
            return real_link(source, target)

        with mock.patch.object(pdf_export.os, "link", appear_then_link):
            result = self.export()

        self.assertFalse(result.ok, result.message)
        self.assertEqual(
            "%PDF-1.7 別人的\n".encode("utf-8"),
            (self.run_dir / "report.pdf").read_bytes(),
        )


class FakeRunner:
    """A stand-in for :func:`subprocess.run` that records what it was asked to do.

    It records rather than asserts: what the recorded command is compared against
    is each test's decision, and no test in this file compares it with a literal
    command line.
    """

    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.commands = []
        self.options = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises

    def __call__(self, command, **options):
        self.commands.append([str(part) for part in command])
        self.options.append(options)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            command, self.returncode, self.stdout, self.stderr
        )


class EdgeConverterSeamTest(unittest.TestCase):
    """預設轉換器：驗它的介面，不驗 Edge 的命令列組成。

    Spec〈測試決策／不應耦合的實作細節〉把「Edge 無頭模式的實際命令列組成」列為不
    得斷言的項目，所以這裡問的是三件**接縫**上的事：兩個路徑有沒有經過轉換、轉換後
    的值有沒有真的交給執行器、失敗有沒有變成看得懂的原因。旗標叫什麼、順序如何、
    等號還是空白，一個都不碰。
    """

    SOURCE = Path("/mnt/d/runs/2026-08-01/0200-btc-aaaa11/report.html")
    TARGET = Path("/mnt/d/runs/2026-08-01/0200-btc-aaaa11/.report.pdf-x.part")

    def setUp(self):
        super().setUp()
        self.translated = []
        self.runner = FakeRunner()

    def translate(self, path):
        self.translated.append(Path(path))
        return "HOST::{}".format(Path(path).name)

    def converter(self, **overrides):
        options = {
            "executable": "/opt/edge/msedge",
            "run": self.runner,
            "translate": self.translate,
        }
        options.update(overrides)
        return pdf_export.EdgeConverter(**options)

    def convert(self, **overrides):
        return self.converter(**overrides)(self.SOURCE, self.TARGET)

    def test_both_paths_go_through_the_translator(self):
        """The page being read and the file being written, both of them.

        Containment rather than equality: the conversion also translates a
        workspace of its own, which is the next few tests' subject.
        """
        self.convert()

        self.assertIn(self.SOURCE, self.translated)
        self.assertIn(self.TARGET, self.translated)

    def test_both_translated_paths_are_what_the_browser_is_handed(self):
        """參數傳遞驗到底；旗標拼字不在這個斷言裡。"""
        self.convert()

        arguments = "\n".join(self.runner.commands[0])
        self.assertIn("HOST::{}".format(self.SOURCE.name), arguments)
        self.assertIn("HOST::{}".format(self.TARGET.name), arguments)

    def test_no_untranslated_path_reaches_the_browser(self):
        """FP 方向：上面兩個斷言在「兩份都傳」的實作上也會通過。"""
        self.convert()

        arguments = "\n".join(self.runner.commands[0])
        self.assertNotIn(str(self.SOURCE), arguments)
        self.assertNotIn(str(self.TARGET), arguments)

    def test_the_program_started_is_the_executable_it_was_given(self):
        self.convert()

        self.assertEqual("/opt/edge/msedge", self.runner.commands[0][0])

    def test_the_browser_it_found_is_the_one_it_starts(self):
        self.converter(executable=None, locate=lambda: "/found/msedge.exe")(
            self.SOURCE, self.TARGET
        )

        self.assertEqual("/found/msedge.exe", self.runner.commands[0][0])

    def test_the_conversion_is_bounded_by_the_timeout_it_was_given(self):
        """一個常駐伺服器的請求佔一條執行緒；不會結束的瀏覽器不能無限等。"""
        self.convert(timeout=7)

        self.assertEqual(7, self.runner.options[0]["timeout"])

    def test_no_edge_anywhere_is_said_before_anything_is_started(self):
        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            self.convert(executable=None, locate=lambda: None)

        self.assertIn("Microsoft Edge", str(raised.exception))
        self.assertIn(pdf_export.EDGE_COMMAND_NAMES[0], str(raised.exception))
        self.assertEqual([], self.runner.commands)

    def test_a_non_zero_exit_is_a_failure_that_quotes_the_browsers_own_words(self):
        self.runner = FakeRunner(returncode=3, stderr="Edge 說：這個設定檔正在使用中")

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            self.convert()

        self.assertIn("3", str(raised.exception))
        self.assertIn("這個設定檔正在使用中", str(raised.exception))

    def test_a_long_complaint_is_quoted_from_the_end_where_the_reason_is(self):
        """Edge 的 stderr 開頭是每次都有的啟動雜訊，出錯的那一行在最後。"""
        self.runner = FakeRunner(
            returncode=1,
            stderr="啟動雜訊\n" * 400 + "真正的原因在最後一行",
        )

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            self.convert()

        self.assertIn("真正的原因在最後一行", str(raised.exception))
        self.assertLess(len(str(raised.exception)), pdf_export.MESSAGE_LIMIT + 200)

    def test_a_silent_non_zero_exit_still_says_there_was_no_output(self):
        self.runner = FakeRunner(returncode=1, stderr="   ")

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            self.convert()

        self.assertIn("沒有輸出", str(raised.exception))

    def test_a_browser_that_will_not_start_is_a_failure_not_a_traceback(self):
        self.runner = FakeRunner(raises=FileNotFoundError("msedge 不在這裡"))

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            self.convert()

        self.assertIn("FileNotFoundError", str(raised.exception))
        self.assertIn("msedge 不在這裡", str(raised.exception))

    def test_a_browser_that_never_finishes_is_a_failure_with_its_own_name(self):
        self.runner = FakeRunner(
            raises=subprocess.TimeoutExpired(["msedge"], pdf_export.EDGE_TIMEOUT_SECONDS)
        )

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            self.convert()

        self.assertIn("TimeoutExpired", str(raised.exception))

    def test_a_conversion_that_worked_raises_nothing(self):
        self.assertIsNone(self.convert())

    # -- 回歸：不能借用使用者正在跑的瀏覽器 --------------------------------

    def test_the_conversion_is_given_a_workspace_of_its_own(self):
        """回歸：本機實測的根因。

        沒有自己的工作目錄時，一個在使用者的 Edge 已經在跑的機器上啟動的無頭
        Edge **不會結束**——PDF 印出來了（888,870 位元組），程序卻一直不回來，
        90 秒逾時；而且那份 PDF 是交給使用者正在用的瀏覽器 session 印的。給它一
        個自己的工作目錄之後，同一份報告 6.4 秒結束、退出碼 0。

        釘的是機制而不是旗標：這次轉換多拿到一個**自己的、真的存在的目錄**，而且
        那個目錄也經過同一套路徑轉換交給瀏覽器。旗標叫什麼不在斷言裡。
        """
        seen = []
        self.translate = lambda path: _record(seen, path)

        self.converter(translate=self.translate)(self.SOURCE, self.TARGET)

        workspaces = [
            path for path, existed in seen if path not in (self.SOURCE, self.TARGET)
        ]
        self.assertEqual(1, len(workspaces), seen)
        self.assertTrue(
            dict((path, existed) for path, existed in seen)[workspaces[0]],
            "轉換進行中，那個工作目錄必須真的存在",
        )

    def test_the_workspace_is_handed_to_the_browser_too(self):
        seen = []
        self.translate = lambda path: _record(seen, path)

        self.converter(translate=self.translate)(self.SOURCE, self.TARGET)

        workspace = [
            path for path, _ in seen if path not in (self.SOURCE, self.TARGET)
        ][0]
        self.assertIn(
            "HOST::{}".format(workspace.name), "\n".join(self.runner.commands[0])
        )

    def test_the_workspace_is_gone_when_the_conversion_is_over(self):
        """它是瀏覽器的暫存設定檔，不是產出；留著就是每次匯出多一份垃圾。"""
        seen = []
        self.translate = lambda path: _record(seen, path)

        self.converter(translate=self.translate)(self.SOURCE, self.TARGET)

        workspace = [
            path for path, _ in seen if path not in (self.SOURCE, self.TARGET)
        ][0]
        self.assertFalse(workspace.exists(), workspace)

    def test_the_workspace_is_removed_even_when_the_conversion_fails(self):
        seen = []
        self.runner = FakeRunner(returncode=1, stderr="不行")
        self.translate = lambda path: _record(seen, path)

        with self.assertRaises(pdf_export.PdfConversionError):
            self.converter(translate=self.translate)(self.SOURCE, self.TARGET)

        workspace = [
            path for path, _ in seen if path not in (self.SOURCE, self.TARGET)
        ][0]
        self.assertFalse(workspace.exists(), workspace)

    def test_the_workspace_is_never_inside_the_run_directory(self):
        """run 目錄只能多兩個 ``.pdf``；瀏覽器的設定檔不能長在那裡。"""
        seen = []
        self.translate = lambda path: _record(seen, path)

        self.converter(translate=self.translate)(self.SOURCE, self.TARGET)

        workspace = [
            path for path, _ in seen if path not in (self.SOURCE, self.TARGET)
        ][0]
        self.assertNotEqual(self.TARGET.parent, workspace.parent)
        self.assertFalse(
            str(workspace).startswith(str(self.TARGET.parent)), workspace
        )


def _record(seen, path):
    """Record a translated path **and whether it existed at that moment**."""
    seen.append((Path(path), Path(path).exists()))
    return "HOST::{}".format(Path(path).name)


class EdgeDiscoveryTest(unittest.TestCase):
    """「Edge 在哪裡」只有兩種答案：找到的位置，或者沒找到。"""

    def test_a_browser_on_the_path_is_the_answer(self):
        wanted = pdf_export.EDGE_COMMAND_NAMES[0]

        found = pdf_export.find_edge(
            which=lambda name: "/usr/bin/" + name if name == wanted else None
        )

        self.assertEqual("/usr/bin/" + wanted, found)

    def test_a_conventional_install_location_is_looked_at_next(self):
        directory = Path(tempfile.mkdtemp(prefix="t06-edge-"))
        self.addCleanup(shutil.rmtree, directory, True)
        installed = directory / "msedge.exe"
        installed.write_bytes(b"")

        found = pdf_export.find_edge(
            which=lambda _name: None, install_paths=(str(installed),)
        )

        self.assertEqual(str(installed), found)

    def test_nothing_found_is_nothing_rather_than_a_guess(self):
        self.assertIsNone(
            pdf_export.find_edge(which=lambda _name: None, install_paths=())
        )

    def test_a_location_that_is_not_there_is_not_offered(self):
        self.assertIsNone(
            pdf_export.find_edge(
                which=lambda _name: None, install_paths=("/nowhere/msedge.exe",)
            )
        )


class HostPathTest(unittest.TestCase):
    """WSL 下的路徑轉換，以及它失敗時說什麼。"""

    PATH = Path("/mnt/d/workstationD/hoya bit/runs/report.html")

    def test_without_wslpath_the_path_is_its_own_translation(self):
        runner = FakeRunner()

        translated = pdf_export.host_path(
            self.PATH, run=runner, which=lambda _name: None
        )

        self.assertEqual(str(self.PATH), translated)
        self.assertEqual([], runner.commands)

    def test_with_wslpath_its_answer_is_the_translation(self):
        runner = FakeRunner(stdout="D:\\workstationD\\hoya bit\\runs\\report.html\n")

        translated = pdf_export.host_path(
            self.PATH, run=runner, which=lambda _name: "/usr/bin/wslpath"
        )

        self.assertEqual("D:\\workstationD\\hoya bit\\runs\\report.html", translated)
        self.assertIn(str(self.PATH), runner.commands[0])

    def test_a_refused_translation_is_a_failure_naming_the_path_and_the_reason(self):
        runner = FakeRunner(returncode=1, stderr="不是有效的路徑")

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            pdf_export.host_path(
                self.PATH, run=runner, which=lambda _name: "/usr/bin/wslpath"
            )

        self.assertIn(str(self.PATH), str(raised.exception))
        self.assertIn("不是有效的路徑", str(raised.exception))

    def test_an_empty_answer_is_a_failure_and_not_an_empty_path(self):
        runner = FakeRunner(stdout="  \n")

        with self.assertRaises(pdf_export.PdfConversionError):
            pdf_export.host_path(
                self.PATH, run=runner, which=lambda _name: "/usr/bin/wslpath"
            )

    def test_a_wslpath_that_will_not_start_is_a_failure_with_its_own_name(self):
        runner = FakeRunner(raises=OSError("wslpath 起不來"))

        with self.assertRaises(pdf_export.PdfConversionError) as raised:
            pdf_export.host_path(
                self.PATH, run=runner, which=lambda _name: "/usr/bin/wslpath"
            )

        self.assertIn("OSError", str(raised.exception))


class ExportEndpointFixture(PageFixture):
    """一個完成的 run、一個假轉換器，和真正的路由——沒有 socket，也沒有瀏覽器。"""

    def setUp(self):
        # ``PageFixture.setUp`` 最後就會建 handler，所以轉換器要先就位。
        self.converter = FakeConverter()
        super().setUp()
        self.run_dir = write_run(self.data_root, RUN_ID, QUESTION)

    def build_handler(self, stream=None, spawn=None):
        """和 ``PageFixture`` 一樣，只是多注入 PDF 轉換器這一個接縫。"""
        self.stream = stream or self.single_pass_stream()
        self.handler = webapp_handler_class(
            self.data_root,
            self.log,
            stream=self.stream,
            lock=self.lock,
            spawn=spawn or self.spawn,
            convert_pdf=self.converter,
        )
        return self.handler

    def export_url(self, run_id=RUN_ID):
        return "/run/{}/export-pdf".format(run_id)

    def export(self, run_id=RUN_ID):
        return self.post(self.export_url(run_id), {})

    def detail(self, run_id=RUN_ID):
        return self.get("/run/{}".format(run_id)).body

    def pdfs(self, run_dir=None):
        return sorted(path.name for path in (run_dir or self.run_dir).glob("*.pdf"))

    def listing(self, run_dir=None):
        return sorted(path.name for path in (run_dir or self.run_dir).iterdir())

    def fingerprint(self, run_dir=None):
        return {
            path.name: (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in sorted((run_dir or self.run_dir).iterdir())
            if path.is_file()
        }

    def events(self):
        return [record["event"] for record in self.records()]


class ExportEndpointTest(ExportEndpointFixture, unittest.TestCase):
    """``POST /run/<id>/export-pdf``：成功、失敗，以及 ``GET`` 什麼都不做。"""

    def test_a_submission_writes_both_pdfs_into_the_run_directory(self):
        response = self.export()

        self.assertEqual(200, response.status)
        self.assertEqual(["debate.pdf", "report.pdf"], self.pdfs())

    def test_the_page_that_comes_back_names_what_landed(self):
        body = self.export().body

        for name in pdf_export.EXPORT_TARGETS:
            self.assertIn(name, body, name)

    def test_only_the_two_pdfs_are_added_and_nothing_else_is_touched(self):
        before = self.fingerprint()

        self.export()
        after = self.fingerprint()

        self.assertEqual({"report.pdf", "debate.pdf"}, set(after) - set(before))
        self.assertEqual(before, {name: after[name] for name in before})

    def test_a_get_on_the_same_url_writes_nothing_and_says_why(self):
        response = self.get(self.export_url())

        self.assertEqual([], self.pdfs())
        self.assertEqual([], self.converter.calls)
        self.assertEqual(404, response.status)
        self.assertIn("表單", response.body)

    def test_a_get_does_not_leave_a_temporary_file_either(self):
        before = self.listing()

        self.get(self.export_url())

        self.assertEqual(before, self.listing())

    def test_a_run_id_that_names_nothing_is_a_404_with_nothing_written(self):
        response = self.export(run_id="20261231T235959Z-btc-zzzz99")

        self.assertEqual(404, response.status)
        self.assertEqual([], self.pdfs())
        self.assertEqual([], self.converter.calls)

    def test_a_failing_conversion_is_an_honest_error_page_with_no_pdf_left(self):
        self.converter.fail_on = "report.html"
        before = self.listing()

        response = self.export()

        self.assertEqual(500, response.status)
        self.assertIn(FakeConverter.REASON, response.body)
        self.assertIn("report.html", response.body)
        self.assertEqual([], self.pdfs())
        self.assertEqual(before, self.listing())

    def test_a_conversion_that_produced_nothing_says_that_and_not_success(self):
        self.converter.write_nothing = True

        response = self.export()

        self.assertEqual(500, response.status)
        self.assertIn("0 位元", response.body)
        self.assertEqual([], self.pdfs())

    def test_a_run_without_the_pages_is_told_so_rather_than_a_server_failure(self):
        (self.run_dir / "debate.html").unlink()

        response = self.export()

        self.assertEqual(200, response.status)
        self.assertIn("debate.html", response.body)
        self.assertEqual([], self.pdfs())

    def test_no_failure_puts_a_traceback_on_the_page(self):
        self.converter.fail_on = "report.html"

        self.assertNotIn("Traceback", self.export().body)

    def test_an_export_is_recorded_in_the_web_apps_own_log(self):
        self.export()

        recorded = [r for r in self.records() if r["event"] == "pdf_exported"]
        self.assertEqual(1, len(recorded))
        self.assertEqual("INFO", recorded[0]["level"])
        self.assertIn(RUN_ID, recorded[0]["message"])

    def test_a_failed_export_is_recorded_as_an_error_with_its_reason(self):
        self.converter.fail_on = "report.html"

        self.export()

        recorded = [r for r in self.records() if r["event"] == "pdf_export_failed"]
        self.assertEqual(1, len(recorded))
        self.assertEqual("ERROR", recorded[0]["level"])
        self.assertIn(FakeConverter.REASON, recorded[0]["message"])

    def test_a_run_that_has_nothing_to_convert_is_a_warning_not_an_error(self):
        (self.run_dir / "debate.html").unlink()

        self.export()

        recorded = [r for r in self.records() if r["event"] == "pdf_export_refused"]
        self.assertEqual(1, len(recorded))
        self.assertEqual("WARNING", recorded[0]["level"])

    def test_a_second_submission_is_refused_and_both_pdfs_are_untouched(self):
        """A-F01 的端點面：重複送出不覆寫，而且說得出為什麼。"""
        self.export()
        before = self.fingerprint()

        response = self.export()

        self.assertEqual(200, response.status)
        self.assertEqual(before, self.fingerprint())
        for name in pdf_export.EXPORT_TARGETS:
            self.assertIn(name, response.body, name)

    def test_a_refused_second_submission_is_a_warning_not_an_error(self):
        self.export()

        self.export()

        refused = [r for r in self.records() if r["event"] == "pdf_export_refused"]
        self.assertEqual(1, len(refused))
        self.assertEqual("WARNING", refused[0]["level"])

    def test_a_promotion_failure_through_the_endpoint_leaves_no_pdf(self):
        """回歸｜A-F02 的端點面。"""
        before = self.listing()

        with failing_second_promotion():
            response = self.export()

        self.assertEqual(500, response.status)
        self.assertEqual([], self.pdfs())
        self.assertEqual(before, self.listing())
        self.assertIn("沒有留下任何 PDF", response.body)


class ExportStatusTableTest(unittest.TestCase):
    """路由的狀態表、頁面的用語表，都與匯出模組的狀態集合對得起來。"""

    def answered_states(self):
        return set(pdf_export.STATES) - {pdf_export.RUN_MISSING}

    def test_every_state_that_reaches_the_route_has_a_status(self):
        self.assertEqual(self.answered_states(), set(server.EXPORT_STATUS))

    def test_every_state_that_reaches_a_page_has_words_of_its_own(self):
        """第五個狀態不准帶著別的狀態的標題出現在讀者面前。"""
        self.assertEqual(self.answered_states(), set(pages.EXPORT_NOTICES))

    def test_only_the_state_that_wrote_something_is_shown_as_a_success(self):
        """成功的視覺訊號是 ``saved`` 這個 class；role 講的是宣告強度，不是成敗。"""
        successes = {
            state for state, (_, _, style) in pages.EXPORT_NOTICES.items()
            if "saved" in style.split()
        }

        self.assertEqual({pdf_export.EXPORTED}, successes)

    def test_a_run_already_being_exported_is_the_requests_own_conflict(self):
        """和 launch 忙碌一樣是 409：送出的內容沒問題，是和伺服器正在做的事衝突。"""
        self.assertEqual(409, server.EXPORT_STATUS[pdf_export.IN_PROGRESS])

    def test_the_state_the_route_answers_with_a_404_is_not_in_the_table(self):
        """``RUN_MISSING`` 走的是 detail 頁同一個 404，不是狀態表。"""
        self.assertNotIn(pdf_export.RUN_MISSING, server.EXPORT_STATUS)

    def test_a_refusal_is_guidance_rather_than_a_server_failure(self):
        self.assertEqual(200, server.EXPORT_STATUS[pdf_export.ALREADY_EXPORTED])


class ExportEndpointConcurrencyTest(ExportEndpointFixture, unittest.TestCase):
    """端點面的並行：兩個同時送出，一個匯出、一個誠實地說正在匯出中。"""

    def setUp(self):
        self.gate = GateConverter()
        super().setUp()

    def build_handler(self, stream=None, spawn=None):
        self.converter = self.gate
        return super().build_handler(stream=stream, spawn=spawn)

    def test_two_submissions_at_once_are_one_export_and_one_conflict(self):
        first = []
        thread = threading.Thread(target=lambda: first.append(self.export()))
        thread.start()
        self.assertTrue(self.gate.entered.wait(10), "第一個請求沒有進到轉換")

        second = self.export()

        self.gate.release.set()
        thread.join(10)
        self.assertTrue(first, "第一個請求沒有回來")
        self.assertEqual(200, first[0].status)
        self.assertEqual(409, second.status)
        self.assertIn("正在匯出", second.body)
        self.assertEqual(["debate.pdf", "report.pdf"], self.pdfs())
        self.assertEqual(
            sorted(list(_BASE_FILES) + ["debate.pdf", "report.pdf"]), self.listing()
        )

    def test_the_conflict_is_recorded_without_calling_it_a_server_failure(self):
        first = []
        thread = threading.Thread(target=lambda: first.append(self.export()))
        thread.start()
        self.assertTrue(self.gate.entered.wait(10))

        self.export()

        self.gate.release.set()
        thread.join(10)
        levels = {
            record["level"]
            for record in self.records()
            if record["event"].startswith("pdf_export")
        }
        self.assertNotIn("ERROR", levels, levels)


class ExportButtonTest(ExportEndpointFixture, unittest.TestCase):
    """報告入口處的「匯出 PDF」按鈕，以及它的兩種狀態。"""

    def form(self, body):
        found = re.search(r'<form[^>]*action="[^"]*export-pdf"[^>]*>.*?</form>',
                          body, re.DOTALL)
        self.assertIsNotNone(found, "匯出表單不在頁面上")
        return found.group(0)

    def button(self, body):
        return re.search(r"<button[^>]*>[^<]*</button>", self.form(body)).group(0)

    def test_a_run_with_both_pages_offers_the_button(self):
        body = self.detail()

        self.assertIn("匯出 PDF", body)
        self.assertIn('method="post"', self.form(body))
        self.assertIn('action="{}"'.format(self.export_url()), self.form(body))

    def test_the_button_is_usable_when_the_report_is_there(self):
        button = self.button(self.detail())

        self.assertNotIn("disabled", button)
        self.assertIn('type="submit"', button)

    def test_the_button_is_disabled_before_the_run_produces_its_report(self):
        write_run(
            self.data_root, "20260803T020000Z-btc-ffff66", "BTC 下週會不會漲", artifacts=()
        )

        body = self.detail("20260803T020000Z-btc-ffff66")
        button = self.button(body)

        self.assertIn("disabled", button)
        self.assertIn('aria-disabled="true"', button)

    def test_a_disabled_button_says_which_page_is_missing(self):
        write_run(
            self.data_root,
            "20260804T020000Z-btc-999977",
            "BTC 下下週會不會漲",
            artifacts=("report.html",),
        )

        body = self.detail("20260804T020000Z-btc-999977")

        self.assertIn("debate.html", body)
        self.assertIn("disabled", self.button(body))

    def test_the_button_is_disabled_once_this_run_has_its_pdfs(self):
        """頁面不提供一按就會被拒絕的按鈕——和「已到期才收人工結果」同一個原則。"""
        self.export()

        body = self.detail()
        button = self.button(body)

        self.assertIn("disabled", button)
        self.assertIn('aria-disabled="true"', button)

    def test_a_page_whose_run_is_already_exported_says_so_and_not_that_it_will_export(
        self,
    ):
        """B6-01：文案要跟磁碟一致，不能還在承諾「既有的檔案一個都不會改」。"""
        self.export()

        body = self.detail()

        for name in pdf_export.EXPORT_TARGETS:
            self.assertIn(name, body, name)
        self.assertNotIn("既有的檔案一個都不會改", body)

    def test_a_page_with_nothing_exported_yet_still_offers_the_export(self):
        """鑑別力：上面那條在「永遠停用」的實作上也會通過。"""
        button = self.button(self.detail())

        self.assertNotIn("disabled", button)
        self.assertIn("既有的檔案一個都不會改", self.detail())

    def test_the_form_action_on_the_page_is_the_url_the_endpoint_answers(self):
        """頁面與路由不是兩份拼字：照著頁面上的 action 送出就會成功。"""
        action = re.search(r'action="([^"]*export-pdf)"', self.detail()).group(1)

        response = self.post(action, {})

        self.assertEqual(200, response.status)
        self.assertEqual(["debate.pdf", "report.pdf"], self.pdfs())

    def test_the_page_that_offers_the_button_says_it_writes(self):
        body = self.detail()

        self.assertIn(pages.RUN_DETAIL_FOOTER, body)
        self.assertNotIn(pages.READ_ONLY_FOOTER, body)

    def test_the_answer_to_a_submission_says_it_writes_as_well(self):
        body = self.export().body

        self.assertIn(pages.RUN_DETAIL_FOOTER, body)
        self.assertNotIn(pages.READ_ONLY_FOOTER, body)

    def test_the_footer_names_the_two_files_this_page_can_add(self):
        for name in pdf_export.EXPORT_TARGETS:
            self.assertIn(name, pages.RUN_DETAIL_FOOTER, name)

    def test_neither_state_of_the_page_carries_a_script_or_an_outside_resource(self):
        write_run(
            self.data_root, "20260805T020000Z-btc-eeee55", "BTC 之後會不會漲", artifacts=()
        )

        for body in (
            self.detail(),
            self.detail("20260805T020000Z-btc-eeee55"),
            self.export().body,
        ):
            for pattern in (r"<script", r"<link\b", r"\bsrc\s*=\s*[\"']https?://", r"@import"):
                self.assertIsNone(re.search(pattern, body, re.IGNORECASE), pattern)

    def test_the_export_notice_is_absent_until_something_was_exported(self):
        self.assertNotIn("已匯出", self.detail())


class ExportKeepsTheRunVerifiableTest(unittest.TestCase):
    """驗收 4：匯出之後 ``run_verifier`` 對這個 run 的檢查仍然通過。

    這裡用的是真的 ``RunController`` 產出的 run，不是頁面 fixture：只有它產出的
    bundle 才是 ``verify_run`` 會受理的東西，而「多了兩個 ``.pdf`` 之後還是
    VERIFIED」正是本票對 run artifact 唯讀邊界的證明。
    """

    QUESTION = "分析 BTC 過去 14 日市場狀態"

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_root = Path(self._tmp.name)
        controller = RunController(
            store=RunStore(self.data_root),
            provider=FakeProvider(),
            clock=FixedClock(auto_advance_ms=250),
            token_source=ScriptedTokenSource(["ticket"]),
        )
        self.result = controller.execute(self.QUESTION)

    def test_the_bundle_still_verifies_after_an_export(self):
        self.assertEqual(
            "VERIFIED", verify_run(self.data_root, self.result.run_id)["status"]
        )

        exported = pdf_export.export_run_pdfs(
            self.data_root, self.result.run_id, convert=FakeConverter()
        )

        self.assertTrue(exported.ok, exported.message)
        summary = verify_run(self.data_root, self.result.run_id)
        self.assertEqual("VERIFIED", summary["status"])

    def test_the_export_left_every_required_artifact_hash_alone(self):
        before = verify_run(self.data_root, self.result.run_id)["required_artifacts"]

        pdf_export.export_run_pdfs(
            self.data_root, self.result.run_id, convert=FakeConverter()
        )

        after = verify_run(self.data_root, self.result.run_id)["required_artifacts"]
        self.assertEqual(before, after)

    def test_both_pdfs_are_beside_the_artifacts_they_came_from(self):
        pdf_export.export_run_pdfs(
            self.data_root, self.result.run_id, convert=FakeConverter()
        )

        run_dir = resolve_run_dir(self.data_root, self.result.run_id)
        for name in pdf_export.EXPORT_TARGETS:
            self.assertTrue((run_dir / name).is_file(), name)


# 一個 ``write_run`` 產生的 run 目錄裡有哪些檔案。列在這裡是為了讓「只多了兩個
# ``.pdf``」這句話能用清單相等來斷言，而不是用「差集是空的」——後者對留下來的暫存
# 檔是看不見的。
_BASE_FILES = (
    "debate.html",
    "evidence.jsonl",
    "manifest.json",
    "question.json",
    "report.html",
    "report.json",
    "votes.json",
)
