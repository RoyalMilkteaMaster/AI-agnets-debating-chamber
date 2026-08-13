"""Ticket 08: 端到端驗收 —— 只測「跨票才看得出來」的組合。

每一票自己的行為已經有自己的測試檔守住：roster 三套與 fail-closed 在
``test_seat_profiles``、合併頁與 ``/stats`` 轉跳在 ``test_webapp_history_merge``、
PDF 匯出的成功與失敗路徑在 ``test_webapp_pdf_export``、停機在
``test_webapp_shutdown``、標的選單在 ``test_webapp_asset_picker``、設計 token 與
對比度在 ``test_design_tokens`` 與 ``test_webapp.ContrastTest``。這裡一項都不重
測，只測把七票接起來以後才成立的三件事：

**一次真的發問。** 主頁標的選單送出之後，webapp 交給 launcher 的那一份參數
（不是重打一次的副本）就是這一趟 run 的參數，而同一份參數在同一個暫存 Data Root
裡真的跑出一份完成的 run：報告、逐字稿、events 都在，``verify-run`` 收得下。整趟
不啟動瀏覽器、不呼叫 codex——席位池、Core 撰稿與命題撰寫三個外部邊界全部是注入的
替身。

**同一個 run，兩邊叫同一個名字。** 席位名稱在 webapp 是從 ``question.json`` 的資
產類別選套，在離線報告是從 ``report.json`` 的資產類別選套：兩份檔案、兩條路徑、
兩個 renderer。逐席比對兩邊**渲染後**的字串，是唯一能看出這兩條路徑分岔的檢查；
單票測試各自只看得到自己那一邊。

**全站繁體中文。** 各頁渲染後的可見文字裡，權威詞彙表涵蓋的枚舉值一個都不得以英
文原樣出現。枚舉值集合是從權威**推導**的（資產類別、燈號、命中狀態、共識狀態、
每種題型的立場詞彙），不是打字列出的，所以權威多一個值就自動納入掃描。

Fixtures 一律沿用既有的：``test_webapp`` 的 ``PageFixture``、
``test_webapp_asset_picker`` 的 ``AskFixture``（含 ``child_launch`` 這道「webapp
真的交出去的參數」接縫）、``test_debate_driver`` 的腳本化席位池與 Core 替身、
``test_launcher`` 的命題替身。第二份「一次發問長什麼樣」會和第一份漂移。

**本檔不假稱有截圖。** 執行環境沒有可自動截圖的瀏覽器；證據是渲染後的 HTML 與對
其中關鍵元素的斷言。
"""

import json
import re
import sys
import unittest
from datetime import timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path

# 這個模組要能用兩種名字被載入：``discover -s tests`` 自己會把這個目錄放上
# sys.path，從 Code Root 執行 ``python3 -m unittest
# tests.test_frontend_redesign_acceptance`` 不會。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hoya_market_agents.debate_state_machine import (  # noqa: E402
    STANCES_BY_QUESTION_TYPE,
)
from hoya_market_agents.launcher import run_launch  # noqa: E402
from hoya_market_agents.question import (  # noqa: E402
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASSES,
)
from hoya_market_agents.question_package import build_question_package  # noqa: E402
from hoya_market_agents.report_contract import CONFIDENCE_LEVELS  # noqa: E402
from hoya_market_agents.report_renderer import CONSENSUS_LABELS  # noqa: E402
from hoya_market_agents.run_index import OUTCOME_STATES, rebuild_index  # noqa: E402
from hoya_market_agents.run_verifier import verify_run  # noqa: E402
from hoya_market_agents.seats import (  # noqa: E402
    CODE_ROOT,
    SEAT_IDS,
    seat_display_names,
)
from hoya_market_agents.webapp import pages, pdf_export  # noqa: E402
from hoya_market_agents.webapp.server import SHUTDOWN_PATH  # noqa: E402
from tests.fakes import FIXED_START_UTC, FixedClock, ScriptedTokenSource  # noqa: E402

import test_debate_driver as debate_fixtures  # noqa: E402
from test_launcher import DEFAULT_PROPOSITION, FakePropositionAdapter  # noqa: E402
from test_webapp_asset_picker import AskFixture  # noqa: E402

# 兩組 fixture 題目：一組台股、一組幣圈。題目的**文字**不決定任何事情（那是
# Ticket 05 的測試在守的），這裡它們只是使用者在選單旁邊打的字。
TW_STOCK_QUESTION = "分析 2330 過去 14 日市場狀態"
TW_STOCK_TARGET = "2330"
CRYPTO_QUESTION = "分析 BTC 過去 14 日市場狀態"
CRYPTO_TARGET = "BTC"

# 工作區根目錄：Code Root 的上一層。Spec A1 最後一條講的是這裡的兩個檔案。
WORKSPACE_ROOT = CODE_ROOT.parent
RETIRED_ENTRY_FILES = ("辯論室預覽.html", "開啟辯論室.bat")


def enumerated_data_values():
    """權威詞彙表涵蓋的枚舉值全集，從權威自己推導。

    Spec A5 的規則是「權威已涵蓋的狀態／枚舉值不得以英文原樣出現」，所以這份集合
    必須跟著權威長大：資產類別看 ``question.ASSET_CLASSES``、燈號看
    ``report_contract.CONFIDENCE_LEVELS``、命中狀態看 ``run_index.OUTCOME_STATES``、
    共識狀態看 ``report_renderer.CONSENSUS_LABELS``、立場看每種題型自己的立場詞彙。

    命中狀態讀的是 ``OUTCOME_STATES`` 而不是 ``OUTCOME_VERDICTS``：後者只有三個
    **寫進紀錄**的判定，`pending` 與 `unreadable` 是**推導**出來的另外兩個狀態，而
    畫面照樣會顯示它們（Spec A5 更是明文點名 ``pending``）。只掃 verdicts 會漏掉那
    兩個值——這是 Reviewer A 的 Finding A8-1，鑑別力由
    ``test_the_scan_would_notice_a_derived_outcome_state_too`` 釘住。

    標的代號、``run_id``、``seat_id``、evidence ID 依 Spec A5 明文**不在此限**，所
    以它們不在這裡。
    """
    values = set(ASSET_CLASSES)
    values |= set(CONFIDENCE_LEVELS)
    values |= set(OUTCOME_STATES)
    values |= set(CONSENSUS_LABELS)
    for stances in STANCES_BY_QUESTION_TYPE.values():
        values |= set(stances)
    return frozenset(values)


# 沒有內容、也不需要收尾的標籤：深度計算要跳過它們，否則 ``<input>`` 之後的層數
# 就全歪了。
_VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
# 內容不是給人讀的標籤。
_SILENT_TAGS = frozenset(("style", "script"))


class _VisibleText(HTMLParser):
    """一個人在瀏覽器裡讀到的字，用 HTML 剖析器讀。

    **刻意不看標籤名稱、不看 class、不看誰包在誰裡面。** Spec〈測試決策／不應耦合
    的實作細節〉把「HTML 的 DOM 巢狀結構細節」與「CSS 類名」排除在斷言之外，所以
    這個讀法只用兩件事：文字本身，以及呼叫端指定的 ``id``。

    ``inside`` 給定時只讀那個 ``id`` 的元素子樹。``id`` 在這裡是**功能性錨點**而非
    樣式：即時頁的腳本就是靠 ``live-tally``／``live-feed`` 這些 id 找到要更新的區
    塊，它們是頁面與腳本之間的契約。包一層 wrapper、換語意標籤、改 class 都不會動
    到它，所以這樣定位不會製造假紅；真的把 id 改掉時，呼叫端會拿到 ``None`` 並以看
    得懂的訊息失敗。

    ``skip`` 給定時，``id`` 讓它回答「是」的那個元素**子樹裡的文字整段不讀**。略過
    是在剖析當下發生的，所以它的範圍就是那個節點的範圍：頁面別處寫了一模一樣的字，
    那份照樣讀得到。

    屬性一律不讀：``value="tw_stock"`` 與 ``class="stance-affirm"`` 是瀏覽器與查詢
    之間的機器值，Spec A5 管的是**畫面上**看到什麼。
    """

    def __init__(self, inside=None, skip=None):
        super().__init__(convert_charrefs=True)
        self.inside = inside
        self.skip = skip
        self.found = inside is None
        self.runs = []
        self._silent = 0
        self._depth = None
        self._skipped = None

    def handle_starttag(self, tag, attrs):
        if tag in _SILENT_TAGS:
            self._silent += 1
            return
        if tag in _VOID_TAGS:
            return
        self._skipped = _one_deeper(self._skipped, self.skip, dict(attrs).get("id"))
        if self._depth is not None:
            self._depth += 1
            return
        if self.inside is not None and dict(attrs).get("id") == self.inside:
            self._depth = 0
            self.found = True

    def handle_startendtag(self, tag, attrs):
        """``<br/>`` 這種自閉標籤既沒有文字也不佔層數。"""
        return None

    def handle_endtag(self, tag):
        if tag in _SILENT_TAGS:
            self._silent = max(0, self._silent - 1)
            return
        if tag in _VOID_TAGS:
            return
        self._skipped = _one_shallower(self._skipped)
        if self._depth is None:
            return
        if self._depth == 0:
            self._depth = None
        else:
            self._depth -= 1

    def handle_data(self, data):
        if self._silent or self._skipped is not None:
            return
        if self.inside is not None and self._depth is None:
            return
        text = data.strip()
        if text:
            self.runs.append(text)


def _one_deeper(depth, skip, element_id):
    """進入一個元素之後，「還在被略過的子樹裡幾層」是多少。

    ``None`` 代表不在任何被略過的子樹裡。已經在裡面就只是變深一層——判斷式只在最外
    緣問一次，裡面包幾層、包什麼標籤都不再問。
    """
    if depth is not None:
        return depth + 1
    if skip is not None and skip(element_id):
        return 0
    return None


def _one_shallower(depth):
    """離開一個元素之後的同一個數字；離開最外緣那一層就回到 ``None``。"""
    if depth is None:
        return None
    return None if depth == 0 else depth - 1


def visible_text(body, skip=None):
    """整頁的可見文字，照讀者讀到的順序。

    ``skip`` 會被交給 :class:`_VisibleText`，用來整段跳過某些節點的子樹。
    """
    return _read(body, skip).strip()


def region_text(body, element_id):
    """``id`` 為 ``element_id`` 的元素子樹的可見文字；找不到那個錨點時回 ``None``。"""
    parser = _VisibleText(inside=element_id)
    parser.feed(body)
    return " ".join(parser.runs).strip() if parser.found else None


def _read(body, skip=None):
    parser = _VisibleText(skip=skip)
    parser.feed(body)
    return " ".join(parser.runs)


def english_values_in(text, values):
    """``text`` 裡以完整英文單詞形式出現的枚舉值，排序後回傳。"""
    return sorted(
        value
        for value in values
        if re.search(r"(?<![A-Za-z_]){}(?![A-Za-z_])".format(value), text)
    )


# A5 的唯一一條豁免，範圍窄到只有一句話。
#
# 出處：Ticket 04（設定頁白話中文）R-001 與 A5 的衝突裁決。Spec R-001 逐字指定
# ``confidence.light_scale[].level`` 的白話說明是「這一級對應的燈色（blue／green／
# yellow／orange／red）」，而 A5 要求畫面上不得出現英文枚舉值——兩條同時成立不可能。
# 裁決依 Spec 自身的衝突原則（§13 與先前章節衝突時以 §13 為準）採 R-001：設定頁是
# 一個 JSON 規則檔的編輯器，那一欄**存進檔案的值就是那個英文字串**，不把合法值列出
# 來，使用者根本無從輸入；而且同一個值早就以 ``value="blue"`` 在畫面上，A5 自己把
# 屬性值判定為機器值、不算畫面上的字。
#
# 豁免綁在**那幾段說明自己的節點**上，不是整頁白名單，也不是對文字做字串替換：
# 略過發生在剖析當下，範圍就是那個節點的子樹。設定頁其他欄位、其他頁面、其他英文值
# 一律照掃；連同一句話原封不動出現在別的元素裡也照樣抓得到（Reviewer A F-A04-1 與
# Reviewer B F-B04-1 就是這個情境，見
# ``test_the_same_sentence_outside_the_exempt_node_is_still_caught``）。
#
# id 是頁面的功能性錨點，改 class、包 wrapper、換語意標籤都不會動到它；真的改掉 id
# 或改寫那句話，豁免會停止生效而讓掃描轉紅——失敗方向是安全的那一邊。
_EXEMPT_NOTE_ID = re.compile(r"note-confidence\.light_scale\[\d+\]\.level")


def is_exempt_note(element_id):
    """這個 ``id`` 是不是裁決指名的那幾段說明之一。"""
    return element_id is not None and _EXEMPT_NOTE_ID.fullmatch(element_id) is not None


def exempt_note_ids(body):
    """這一頁上，依裁決豁免的說明段落的 ``id``。"""
    return [
        element_id
        for element_id in re.findall(r'\bid="([^"]*)"', body)
        if is_exempt_note(element_id)
    ]


def exempt_notes(body):
    """那幾段說明**在畫面上的字**——照讀者讀到的樣子讀，不是手抄的。"""
    return [region_text(body, element_id) for element_id in exempt_note_ids(body)]


def scannable_text(body):
    """一頁上要拿去掃 A5 的可見文字：豁免節點的子樹整段不讀。

    「不讀那個節點」和「把那句話從頁面文字裡刪掉」差在哪，正是這條豁免的全部範圍：
    前者只放過那一個節點，後者會連頁面別處一模一樣的字一起放過。
    """
    return visible_text(body, skip=is_exempt_note)


def seat_name_candidates():
    """``seat_id`` → 這一席在三套 profiles 裡所有可能的顯示名稱。

    「頁面上寫的是哪一套」因此是從文字**讀出來**的，不是預設的：候選是權威給的全部
    可能，讀到哪一個算哪一個。名稱逐席互不重複，所以逐席判定不需要靠 DOM 位置——這
    正是本檔能同時做到「逐席」與「不斷言巢狀結構」的原因。
    """
    candidates = {seat_id: set() for seat_id in SEAT_IDS}
    for asset_class in (ASSET_CLASS_TW_STOCK, ASSET_CLASS_CRYPTO, ASSET_CLASS_OPEN):
        for seat_id, name in seat_display_names(asset_class).items():
            candidates[seat_id].add(name)
    return candidates


def names_shown_per_seat(text):
    """``seat_id`` → 這段文字裡**自己出現過**的候選名稱。

    同一席的候選名稱可以互相包含：Spec R-005 之後，社群那一席開放套叫「社群觀察員」，
    幣圈套叫「輿情與幣市社群觀察員」，後者整個含著前者。純粹用 ``in`` 判斷，一頁只印
    了幣圈名的頁面會被讀成「同時印了兩套」。

    所以短名要被算成「這一頁真的印了它」，出現次數必須超過它被長名夾帶的次數；否則
    它只是別人的一部分。兩個方向都保住：只印長名的頁面讀到一個名字，真的把兩套都印
    出去的頁面照樣讀到兩個。
    """
    shown = {}
    for seat_id, candidates in seat_name_candidates().items():
        found = set()
        for name in candidates:
            carried = sum(
                text.count(longer)
                for longer in candidates
                if longer != name and name in longer
            )
            if text.count(name) > carried:
                found.add(name)
        shown[seat_id] = found
    return shown


def numbers_after_labels(text, labels):
    """``key`` → ``labels[key]`` 這個詞之後第一個數字，讀不到的 key 不在結果裡。

    「詞的後面第一個數字」是讀者真正讀到的關係，不是 DOM 關係：中間包幾層元素、
    class 叫什麼、用 ``<strong>`` 還是 ``<data>`` 都不影響，但「偏多 6 印成偏多 1」
    會被抓到。
    """
    found = {}
    for key, label in labels.items():
        match = re.search(r"{}\D*?(\d+)".format(re.escape(label)), text)
        if match is not None:
            found[key] = int(match.group(1))
    return found


class FinishedRun:
    """一趟完成的 run，連同 webapp 當初交給 launcher 的那一份參數。

    ``package`` 是同一份參數獨立讀一次的結果：頁面上的立場詞彙要跟它比，才不是拿
    頁面自己讀出來的東西跟自己比。
    """

    def __init__(self, handed, package, run_id, run_dir):
        self.handed = handed
        self.package = package
        self.run_id = run_id
        self.run_dir = run_dir

    def artifact(self, name):
        return (self.run_dir / name).read_text(encoding="utf-8")

    def record(self, name):
        return json.loads(self.artifact(name))


class ScriptedPool(debate_fixtures.ScriptedDebateRunner):
    """腳本化的席位池，連研究階段一起答，證據卡講的是這一趟 run 自己的標的。

    ``test_debate_driver`` 的 ``FullRunFakeRunner`` 只答 BTC；證據閘門會拒絕不屬
    於本題資產範圍的卡，所以標的由呼叫端指定。
    """

    def __init__(self, run, results_queue, clock, stances, asset, **options):
        super().__init__(results_queue, clock, stances, **options)
        self.run = run
        self.asset = asset

    def start(self, attempt, checkpoint):
        self.results_queue.put(
            (
                "research_lineage",
                attempt.attempt_id,
                {
                    "seat_id": attempt.seat_id,
                    "attempt_id": attempt.attempt_id,
                    "attempt_kind": attempt.kind,
                    "provider": attempt.provider,
                    "actual_model": attempt.model,
                },
            )
        )
        self.results_queue.put(
            ("result", attempt.attempt_id, self._envelope(attempt))
        )
        return True

    def _envelope(self, attempt):
        card = debate_fixtures.evidence_card(self.run.run_id, attempt.seat_id)
        card["attempt_id"] = attempt.attempt_id
        card["asset"] = self.asset
        return json.dumps(
            {"seat_id": attempt.seat_id, "evidence_cards": [card]}, ensure_ascii=False
        )

    def checkpoint(self, attempt_id):
        return None

    def correct(self, attempt, raw_output, exact_error):
        return None

    def shutdown(self, wait=True):
        return None


class MenuRunFixture(AskFixture):
    """從標的選單送出，到一份完成的 run，全程在這個行程裡。

    ``AskFixture`` 負責「像瀏覽器一樣送出表單」與「webapp 交出去的參數是什麼」；
    這裡負責把**那一份參數**餵給 :func:`~hoya_market_agents.launcher.run_launch`。
    參數沒有被重打一次，所以中途掉了一個旗標，這裡就跑不出對的 run。
    """

    def setUp(self):
        super().setUp()
        self.write_certificate()
        # Core 撰稿替身的稿件。預設一份會被收下的初稿；要走紅字稽核那條路的測試
        # 自己改（``core_writes_a_draft_no_validation_accepts``）。
        self.core_narratives = [debate_fixtures.narrative()]

    def tw_stock_run(self, token="aaa111"):
        return self.run_from_the_menu(
            question=TW_STOCK_QUESTION,
            asset_class=ASSET_CLASS_TW_STOCK,
            target=TW_STOCK_TARGET,
            token=token,
        )

    def crypto_run(self, token="bbb222", days_later=1):
        return self.run_from_the_menu(
            question=CRYPTO_QUESTION,
            asset_class=ASSET_CLASS_CRYPTO,
            target=CRYPTO_TARGET,
            token=token,
            days_later=days_later,
        )

    def run_from_the_menu(
        self, *, question, asset_class, target, token, days_later=0
    ):
        """送出一次發問，跑完那一趟 run，然後把它放進索引。

        ``days_later`` 只是把 run 的時刻往後挪，好讓同一個 Data Root 裡的兩趟 run
        有各自的 ``run_id`` 時刻；聊天室「哪一趟是最新的」是照時刻比的。
        """
        self.finish_any_earlier_launch()
        self.submit(question=question, asset_class=asset_class, target=target)
        handed = self.child_launch()
        self.assertEqual(str(self.data_root), str(handed["data_root"]))
        run = self.launch(handed, token, days_later)
        rebuild_index(self.data_root)
        return run

    def launch(self, handed, token, days_later=0):
        clock = FixedClock(start_utc=FIXED_START_UTC + timedelta(days=days_later))
        package = self.package_for(handed)
        out = debate_fixtures.Stream()
        err = debate_fixtures.Stream()
        code = run_launch(
            handed["question"],
            handed["data_root"],
            clock=clock,
            token_source=ScriptedTokenSource([token]),
            runner_factory=self.runner_factory(clock, handed, package),
            proposition_adapter=FakePropositionAdapter(DEFAULT_PROPOSITION),
            sleeper=debate_fixtures.StepSleeper(clock, step_ms=30_000),
            out=out,
            err=err,
            no_live=True,
            assets=handed["assets"],
            asset_class=handed["asset_class"],
        )

        self.assertEqual(0, code, err.text)
        finalized = json.loads(out.lines[-1])
        self.assertEqual("FINALIZED", finalized["status"], out.text)
        return FinishedRun(
            handed, package, finalized["run_id"], Path(finalized["run_dir"])
        )

    def finish_any_earlier_launch(self):
        """讓上一趟發問結束，這一趟才是新的一趟。

        發問區有鎖：run 還在跑的時候再送一次只會拿到「正在進行」的頁面，不會有第二
        個程序。這個 fixture 一開始沒有讓假程序收尾，於是第二次發問讀到的是第一次
        的參數——兩趟 run 看起來都跑了，其實是同一組參數跑了兩次。收尾與清帳都在這
        裡做，``child_launch`` 的「只有一次啟動」斷言才說得出真話。
        """
        for process in self.processes:
            process.finish(0)
        self.spawned.clear()
        self.processes.clear()

    def package_for(self, handed):
        """這一份參數自己讀出來的題目 package——選單的答案，不是題目的文字。"""
        return build_question_package(
            handed["question"],
            assets=handed["assets"],
            asset_class=handed["asset_class"],
        )

    def runner_factory(self, clock, handed, package):
        stances = self.scripted_stances(package)
        asset = handed["assets"][0]

        def factory(*, run, data_root, results_queue, **options):
            return ScriptedPool(
                run,
                results_queue,
                clock,
                stances,
                asset,
                wave_advance_ms={"opening": 30_000, "r1": 50_000},
                core_adapter=debate_fixtures.ScriptedCoreAdapter(
                    clock, self.core_narratives
                ),
            )

        return factory

    def core_writes_a_draft_no_validation_accepts(self):
        """讓 Core 交出一份客觀驗證不會收的初稿，好走到紅字稽核那條路。

        ``confidence_level`` 不在核准燈號，是 ``validate_core_narrative`` 直接拒絕
        的那一種——真實世界的失敗形態，不是硬造的。撰稿替身會把最後一份初稿重複用
        於 correction，所以一份就夠讓流程走完「一次修正後仍不通過」。
        """
        self.core_narratives = [debate_fixtures.narrative(confidence_level="teal")]

    def scripted_stances(self, package):
        """六席一邊、一席另一邊，用這一種題型自己的立場詞彙。

        立場詞彙不是打在這裡的：它來自這一題的 package，所以題型換了、詞彙換了，
        腳本跟著換而不是變成一堆被拒絕的訊息。
        """
        options = tuple(package.stance_options)
        self.assertGreaterEqual(len(options), 2, options)
        return dict(zip(SEAT_IDS, [options[0]] * 6 + [options[1]]))

    # -- 渲染後的讀法 --------------------------------------------------------

    def room(self, run):
        """這一趟 run 的辯論室頁面（``?run=`` 指名，不靠「誰最新」）。"""
        response = self.get("/?run={}".format(run.run_id))
        self.assertEqual(200, response.status)
        return response.body

    # 辯論室的兩個功能性錨點：即時頁腳本就是靠這兩個 id 找到要更新的區塊，所以它們
    # 是頁面與腳本之間的契約，不是樣式。用 id 定位不會被包 wrapper 或改 class 影響。
    TALLY_REGION_ID = "live-tally"
    FEED_REGION_ID = "live-feed"

    def region(self, run, element_id, what):
        """辯論室裡某個錨點的可見文字；錨點真的不見時給看得懂的失敗訊息。"""
        text = region_text(self.room(run), element_id)
        self.assertIsNotNone(text, "辯論室找不到{}（id={}）".format(what, element_id))
        return text

    def tally_shown(self, run):
        """``stance`` → 畫面上寫在那個立場詞後面的票數。"""
        text = self.region(run, self.TALLY_REGION_ID, "票數區")
        labels = run.package.stance_labels
        shown = numbers_after_labels(text, labels)

        missing = [labels[stance] for stance in labels if stance not in shown]
        self.assertEqual([], missing, "票數區讀不到這些立場的數字：{!r}".format(text))
        return shown

    def room_names(self, run):
        """``seat_id`` → 辯論室畫面上出現了這一席的哪些候選名稱。"""
        return names_shown_per_seat(visible_text(self.room(run)))

    def offline_names(self, run, page="report.html"):
        """``seat_id`` → 離線頁面渲染後出現了這一席的哪些候選名稱。"""
        return names_shown_per_seat(visible_text(run.artifact(page)))

    def assert_named_by_one_set(self, shown, asset_class, where):
        """每一席恰好出現一個名稱，而且是這個市場那一套的名稱。

        「恰好一個」同時擋掉兩種壞掉的方式：印成別套（集合是另一個名字）、以及把三
        套都印出來（集合有兩個）。一席都沒印到則是空集合，同樣紅。
        """
        expected = seat_display_names(asset_class)
        for seat_id in SEAT_IDS:
            self.assertEqual(
                {expected[seat_id]}, shown[seat_id], "{}: {}".format(where, seat_id)
            )

    def assert_room_and_offline_agree(self, run, asset_class):
        """同一趟 run，兩份渲染後的輸出逐席比對，再各自對權威比對。

        兩邊讀的是不同的檔案（辯論室讀 ``question.json``、離線報告讀
        ``report.json``），所以逐席相等是唯一能看出這兩條路徑分岔的檢查。
        """
        room = self.room_names(run)
        offline = self.offline_names(run)

        for seat_id in SEAT_IDS:
            self.assertEqual(room[seat_id], offline[seat_id], seat_id)
        self.assert_named_by_one_set(room, asset_class, "辯論室")
        self.assert_named_by_one_set(offline, asset_class, "離線報告")

    def rendered_pages(self, run):
        """使用者會逐頁看的每一頁，渲染後的 HTML。"""
        pages = {
            "room": self.room(run),
            "history": self.get("/history").body,
            "settings": self.get("/settings").body,
            "run_detail": self.get("/run/{}".format(run.run_id)).body,
            "not_found": self.get("/no-such-page").body,
            "closed": self.post(SHUTDOWN_PATH, {}).body,
        }
        for name, body in pages.items():
            self.assertTrue(body.strip(), name)
        return pages


class TheMenusAnswerReachesTheFinishedRunTest(MenuRunFixture, unittest.TestCase):
    """Spec A3 第 4 條與 R6，一路走到底。

    Ticket 05 證明了「選單的答案到得了 launcher」。這裡接下去問剩下那一半：那一份
    參數真的變成了這一趟 run 的題目記錄與報告契約，而報告契約是離線報告選套的依
    據。中間任何一段把資產類別丟掉，這裡就紅。
    """

    def test_a_taiwan_stock_submission_finishes_a_run_this_market_owns(self):
        run = self.tw_stock_run()

        question = run.record("question.json")
        self.assertEqual(ASSET_CLASS_TW_STOCK, question["asset_class"])
        self.assertEqual([TW_STOCK_TARGET], question["assets"])
        self.assertEqual(TW_STOCK_QUESTION, question["question"])

    def test_a_crypto_submission_finishes_a_run_that_market_owns(self):
        run = self.crypto_run()

        question = run.record("question.json")
        self.assertEqual(ASSET_CLASS_CRYPTO, question["asset_class"])
        self.assertEqual([CRYPTO_TARGET], question["assets"])

    def test_the_market_the_menu_stated_reaches_the_report_contract(self):
        """離線報告的選套只看 ``report.json``；題目記錄對、報告契約錯就會分岔。"""
        run = self.tw_stock_run()

        self.assertEqual(ASSET_CLASS_TW_STOCK, run.record("report.json")["asset_class"])

    def test_the_finished_bundle_is_one_verify_run_accepts(self):
        run = self.tw_stock_run()

        self.assertEqual("VERIFIED", verify_run(self.data_root, run.run_id)["status"])

    def test_both_offline_pages_are_written_for_the_run_the_menu_started(self):
        run = self.tw_stock_run()

        for name in ("report.html", "debate.html"):
            self.assertTrue((run.run_dir / name).is_file(), name)

    def test_the_run_the_menu_started_is_the_run_the_history_page_lists(self):
        run = self.tw_stock_run()

        self.assertIn(run.run_id, self.listed_run_ids(self.get("/history").body))


class TheSameSeatIsNamedTheSameEverywhereTest(MenuRunFixture, unittest.TestCase):
    """Spec A3 第 1、2、3 條與驗收 6：逐席比對 webapp 與離線報告。

    兩邊的名字來自同一個讀取口，但走的是不同的檔案：辯論室讀 ``question.json`` 的
    資產類別，離線報告讀 ``report.json`` 的。所以這裡比的是**兩份渲染後的輸出**，
    不是兩次呼叫同一個函式——後者永遠相等，也就永遠測不到分岔。
    """

    def test_a_taiwan_stock_run_is_named_from_the_stock_set_in_both_places(self):
        self.assert_room_and_offline_agree(self.tw_stock_run(), ASSET_CLASS_TW_STOCK)

    def test_a_crypto_run_is_named_from_the_crypto_set_in_both_places(self):
        self.assert_room_and_offline_agree(self.crypto_run(), ASSET_CLASS_CRYPTO)

    def crypto_only_names(self):
        """只屬於幣圈套的席位名稱；套組不再有差別時這裡先失敗。"""
        crypto = set(seat_display_names(ASSET_CLASS_CRYPTO).values())
        stock = set(seat_display_names(ASSET_CLASS_TW_STOCK).values())
        only = crypto - stock
        self.assertTrue(only, "roster 的兩套名稱已無差別，這條驗收失去鑑別力")
        return only

    def test_a_taiwan_stock_runs_offline_pages_hold_no_crypto_seat_name(self):
        """Spec A3 第 3 條：兩份離線頁面都算，讀的是渲染後的可見文字。"""
        run = self.tw_stock_run()

        for page in ("report.html", "debate.html"):
            shown = visible_text(run.artifact(page))
            for name in self.crypto_only_names():
                self.assertNotIn(name, shown, "{}: {}".format(page, name))

    def test_a_taiwan_stock_runs_offline_pages_do_hold_the_stock_seat_names(self):
        """FP 方向：一份沒有任何席位名稱的報告也會通過上一條。"""
        run = self.tw_stock_run()

        for page in ("report.html", "debate.html"):
            self.assert_named_by_one_set(
                self.offline_names(run, page), ASSET_CLASS_TW_STOCK, page
            )

    def test_two_runs_in_one_data_root_are_each_named_by_their_own_market(self):
        """同一個 Data Root、同一份 roster，兩趟 run 各拿自己的套組。"""
        stock_run = self.tw_stock_run()
        crypto_run = self.crypto_run()

        self.assert_named_by_one_set(
            self.offline_names(stock_run), ASSET_CLASS_TW_STOCK, "台股離線報告"
        )
        self.assert_named_by_one_set(
            self.offline_names(crypto_run), ASSET_CLASS_CRYPTO, "幣圈離線報告"
        )

    def test_the_reading_is_not_tied_to_the_markup_that_carries_the_name(self):
        """Reviewer B 的 F1 方向：讀法要對版面調整免疫、對印錯名字敏感。

        同一句話換掉標籤、class 與巢狀層數，讀出來的必須一樣；把名字換成另一套的，
        讀出來的必須不一樣。
        """
        plain = '<p class="seat-head">籌碼面分析師｜<code>derivatives</code></p>'
        rewrapped = (
            '<section class="renamed"><header><h4><b>籌碼面分析師</b></h4>'
            "<data>derivatives</data></header></section>"
        )
        other_set = '<p class="seat-head">合約槓桿分析師｜<code>derivatives</code></p>'

        self.assertEqual(
            {"籌碼面分析師"}, names_shown_per_seat(visible_text(plain))["derivatives"]
        )
        self.assertEqual(
            names_shown_per_seat(visible_text(plain))["derivatives"],
            names_shown_per_seat(visible_text(rewrapped))["derivatives"],
        )
        self.assertEqual(
            {"合約槓桿分析師"},
            names_shown_per_seat(visible_text(other_set))["derivatives"],
        )

    def test_a_name_that_contains_another_set_s_name_is_read_as_one_name(self):
        """Spec R-005 之後有一組候選互相包含，讀法兩個方向都要對。

        「輿情與幣市社群觀察員」整個含著「社群觀察員」：只印前者的頁面只算印了一套，
        真的兩套都印出去的頁面則要照樣被讀成兩個名字。
        """
        one_set = "<p>輿情與幣市社群觀察員｜social-macro</p>"
        both_sets = "<p>輿情與幣市社群觀察員</p><p>社群觀察員</p>"

        self.assertEqual(
            {"輿情與幣市社群觀察員"},
            names_shown_per_seat(visible_text(one_set))["social-macro"],
        )
        self.assertEqual(
            {"輿情與幣市社群觀察員", "社群觀察員"},
            names_shown_per_seat(visible_text(both_sets))["social-macro"],
        )


class TheDegradedReportStillKnowsItsMarketTest(MenuRunFixture, unittest.TestCase):
    """Spec A3 第 1、3 條在**報告驗證失敗**那條路徑上也要成立。

    這是本票驗收時撞到的缺陷 D-1 的回歸測試。報告不只有一個生產端：Core 的稿件通
    過驗證時由 ``debate_driver.assemble_market_report`` 產生，一次修正後仍不通過時
    改由 ``report_workflow`` 的紅字稽核骨架產生。後者原本沒有 ``asset_class``，於是
    同一趟台股 run 的 webapp（讀 ``question.json``）說「籌碼雷達」、離線報告（讀
    ``report.json``）說「槓桿雷達」——A3 第 3 條與第 1 條的「兩處一致」同時破。

    與 ``tests/test_report_renderer.py`` 那條同批修好的測試不重複：那邊從 renderer
    的入口證明骨架帶得出市場，這裡從**使用者走的整條路**證明——選單發問、跑完一趟
    真的 run、比對兩份渲染後的輸出。兩者壞掉的方式不同：契約欄位掉了那邊紅，任何一
    端接線掉了這邊紅。
    """

    def refused_report_run(self):
        """一趟台股 run，其 Core 報告兩次都沒通過客觀驗證。

        退化路徑的形狀在這裡就斷言，所以後面每一條都不可能在「其實走了正常路徑」的
        run 上空過。
        """
        self.core_writes_a_draft_no_validation_accepts()
        run = self.tw_stock_run()
        report = run.record("report.json")

        self.assertTrue(report["process_failure"], report.get("consensus_status"))
        self.assertEqual("validation_failed", report["consensus_status"])
        self.assertTrue(report["validation_errors"], "紅字稽核報告沒有記下任何驗證錯誤")
        return run

    def test_a_refused_report_still_records_the_market_the_menu_stated(self):
        """``get`` 而非 ``[]``：欄位整個不見是 D-1 的原貌，那該讀成「沒記市場」而不是
        一個 ``KeyError``——讀 log 的人要看得出少的是什麼。"""
        run = self.refused_report_run()

        self.assertEqual(
            ASSET_CLASS_TW_STOCK, run.record("report.json").get("asset_class")
        )
        self.assertEqual(
            ASSET_CLASS_TW_STOCK, run.record("question.json").get("asset_class")
        )

    def test_a_refused_reports_offline_pages_hold_no_crypto_seat_name(self):
        run = self.refused_report_run()
        crypto = set(seat_display_names(ASSET_CLASS_CRYPTO).values())
        stock = set(seat_display_names(ASSET_CLASS_TW_STOCK).values())
        self.assertTrue(crypto - stock, "roster 的兩套名稱已無差別，這條失去鑑別力")

        for page in ("report.html", "debate.html"):
            shown = visible_text(run.artifact(page))
            for name in crypto - stock:
                self.assertNotIn(name, shown, "{}: {}".format(page, name))
            self.assert_named_by_one_set(
                self.offline_names(run, page), ASSET_CLASS_TW_STOCK, page
            )

    def test_a_refused_report_is_named_the_same_as_the_room_seat_by_seat(self):
        """兩邊讀不同的檔案，所以只有比對渲染後的輸出看得出接線掉了哪一端。"""
        run = self.refused_report_run()

        self.assert_room_and_offline_agree(run, ASSET_CLASS_TW_STOCK)


class EveryPageIsTraditionalChineseTest(MenuRunFixture, unittest.TestCase):
    """Spec A5：各頁渲染後的可見文字，權威涵蓋的枚舉值零命中。

    掃描對象是**真的跑出來的 run** 所渲染的頁面，而不是手寫的 fixture：報告完成、
    七席有票、燈號有值、命中狀態有紀錄，這些欄位才真的會被印出來。

    唯一的例外是設定頁上列出合法燈色的那一句，見 :func:`scannable_text` 上面記的
    裁決。它有多窄由下面三條斷言釘住：豁免真的在做事、它只吃掉裁決指名的那一句、
    以及同一頁其他地方的英文枚舉值照樣會紅。
    """

    def test_no_page_shows_an_enumerated_value_in_english(self):
        run = self.tw_stock_run()
        values = enumerated_data_values()

        for name, body in self.rendered_pages(run).items():
            self.assertEqual(
                [], english_values_in(scannable_text(body), values), name
            )

    def test_a_crypto_runs_pages_are_the_same(self):
        run = self.crypto_run()
        values = enumerated_data_values()

        for name, body in self.rendered_pages(run).items():
            self.assertEqual(
                [], english_values_in(scannable_text(body), values), name
            )

    def settings_page(self):
        """這一趟真的跑完的 run 所渲染的設定頁。

        一個測試只叫一次：``tw_stock_run`` 每次都真的發動一趟新的 run，同一個測試
        裡叫第二次會撞上同一個 run id。
        """
        return self.rendered_pages(self.tw_stock_run())["settings"]

    def plus(self, body, markup):
        """``body``，在 ``</main>`` 之前多植一段 markup。

        植入點先斷言過才用，否則「什麼都沒植進去」的空測試也會通過。
        """
        self.assertIn("</main>", body)
        return body.replace("</main>", "{}</main>".format(markup))

    def test_the_exemption_covers_exactly_the_sentence_the_ruling_names(self):
        """豁免掉的是哪幾個字，攤開來看。

        這一句是 Spec R-001 逐字指定的文案；設定頁把那個 ``id`` 拿去放別的話，或
        者文案被改寫，這裡就會紅——豁免不會跟著飄。
        """
        self.assertEqual(
            {"這一級對應的燈色（blue／green／yellow／orange／red）"},
            set(exempt_notes(self.settings_page())),
        )

    def test_the_exemption_is_the_only_reason_that_page_passes(self):
        """豁免不是一段沒有作用的程式：拿掉它，設定頁就是紅的。

        兩個方向一起釘住，這一條才有意義——上面那五個燈色詞確實在設定頁的可見文字
        裡，而略過裁決指名的那個節點之後，一個都不剩。
        """
        settings_page = self.settings_page()
        values = enumerated_data_values()

        self.assertEqual(
            ["blue", "green", "orange", "red", "yellow"],
            english_values_in(visible_text(settings_page), values),
        )
        self.assertEqual([], english_values_in(scannable_text(settings_page), values))

    def test_the_scan_still_bites_everywhere_else_on_that_same_page(self):
        """FP 方向：豁免有沒有把設定頁整頁變成免掃描區。

        同一頁、同一個燈色詞，只是出現在別的地方——照樣要被抓到。
        """
        planted = self.plus(self.settings_page(), "<p>燈號：green</p>")

        self.assertEqual(
            ["green"], english_values_in(scannable_text(planted), enumerated_data_values())
        )

    def test_the_same_sentence_outside_the_exempt_node_is_still_caught(self):
        """Reviewer A F-A04-1／Reviewer B F-B04-1 的回歸測試。

        第一版豁免是對攤平後的整頁純文字做 ``str.replace()``，於是「那一句話」在頁
        面上**任何地方**都會被刪掉——連豁免節點以外一模一樣的複製品也一起放過，而
        上面那條「另放一個 green」的守衛看不到這種情況（它植的是不同的字）。

        所以這裡植的是**一字不差的同一句**：豁免綁在節點上，這一份就必須照樣紅。
        """
        settings_page = self.settings_page()
        sentence = exempt_notes(settings_page)[0]
        planted = self.plus(settings_page, "<p>{}</p>".format(sentence))

        self.assertEqual(
            ["blue", "green", "orange", "red", "yellow"],
            english_values_in(scannable_text(planted), enumerated_data_values()),
        )

    def test_the_run_detail_page_shows_this_runs_market_as_a_chinese_word(self):
        """FP 方向：一頁什麼都沒印的空白頁也會通過零命中。"""
        run = self.tw_stock_run()

        detail = visible_text(self.get("/run/{}".format(run.run_id)).body)
        self.assertIn(pages.asset_class_label(ASSET_CLASS_TW_STOCK), detail)

    def test_the_scan_would_notice_an_english_value_on_a_page(self):
        """鑑別力：這種讀法要有能力失敗。"""
        planted = "<p>資產類別：tw_stock｜燈號：green</p>"

        self.assertEqual(
            ["green", "tw_stock"],
            english_values_in(visible_text(planted), enumerated_data_values()),
        )

    def test_the_scan_would_notice_a_derived_outcome_state_too(self):
        """Reviewer A 的 Finding A8-1：命中狀態的權威是 ``OUTCOME_STATES``。

        ``pending`` 與 ``unreadable`` 不寫進紀錄、只從紀錄推導，所以它們不在
        ``OUTCOME_VERDICTS`` 裡——但畫面照樣顯示它們，Spec A5 還明文點名 ``pending``。
        掃描集合漏掉它們的話，這兩個值植入頁面也不會被抓到。
        """
        planted = "<p>命中狀態：pending，另一筆 unreadable</p>"

        self.assertEqual(
            ["pending", "unreadable"],
            english_values_in(visible_text(planted), enumerated_data_values()),
        )

    def test_the_scan_covers_every_state_the_authority_declares(self):
        """權威多一個狀態就自動納入，不必回來改這個檔。"""
        self.assertEqual(set(), set(OUTCOME_STATES) - enumerated_data_values())

    def test_a_machine_value_in_an_attribute_is_not_a_word_on_screen(self):
        """Spec A5 管的是畫面上的字：``value=""`` 是表單送回去用的機器值。"""
        markup = '<option value="tw_stock">台股</option>'

        self.assertEqual(
            [], english_values_in(visible_text(markup), enumerated_data_values())
        )

    def test_the_target_code_stays_as_the_reader_typed_it(self):
        """Spec A5 明文把標的代號排除在外，所以它必須留在畫面上。"""
        run = self.tw_stock_run()

        self.assertIn(TW_STOCK_TARGET, visible_text(self.room(run)))


class TheProtectedZoneStillBehavesTest(MenuRunFixture, unittest.TestCase):
    """Spec A4 第 1 條：聊天室、燈位、三種票數在真正跑完的 run 上仍如舊。

    保護區的 markup、class 詞彙與語意色由 ``test_webapp.ProtectedZoneOuterwearTest``
    與 ``test_design_tokens`` 用手寫 fixture 釘住——那是 R2 凍結的擁有者，該由它們斷
    言 DOM 與 class。這裡補的是它們看不到的一半：改版後的七票接起來、一趟**真的**
    run 跑完之後，公開紀錄裡的票數與畫面上讀到的三種票數是不是同一組數字。

    因此本類別只用兩種讀法：區塊以 ``id``（即時頁腳本綁定的功能性錨點）定位，區塊內
    只讀可見文字。標籤名稱、巢狀層數與 class 一律不斷言（Spec〈不應耦合的實作細節〉
    明文排除），所以包一層 wrapper 或改 class 不會讓驗收假紅。
    """

    def test_each_of_the_three_tallies_is_the_public_records_own_number(self):
        """逐立場精確比對，不是比總和。

        只比總和的話，「偏多 6／偏空 1」被印成「偏多 1／偏空 6」總和仍是 7，會通過
        ——那正是保護區走鐘最可能的樣子（Reviewer B 的 Finding B8-F2）。
        """
        run = self.tw_stock_run()
        votes = run.record("votes.json")
        expected = {
            stance: votes["tally"].get(stance, 0)
            for stance in run.package.stance_options
        }

        shown = self.tally_shown(run)

        self.assertEqual(3, len(expected), expected)
        self.assertNotEqual(
            1, len(set(expected.values())), "這一趟的三個票數相同，逐項比對會失去鑑別力"
        )
        self.assertEqual(expected, shown)

    def test_every_tally_is_labelled_in_this_question_types_own_vocabulary(self):
        """立場詞彙的獨立真值是這一題的 package，不是頁面自己讀出來的那一份。"""
        run = self.tw_stock_run()
        text = self.region(run, self.TALLY_REGION_ID, "票數區")

        for stance, label in run.package.stance_labels.items():
            self.assertIn(label, text, stance)
            self.assertEqual([], english_values_in(text, {stance}), stance)

    def test_the_tally_reading_pairs_each_word_with_its_own_number(self):
        """FP 方向：這個讀法必須能分辨「總和一樣但每一項都不對」的版面。"""
        labels = {"bullish": "偏多", "bearish": "偏空", "neutral": "方向不明"}

        self.assertEqual(
            {"bullish": 6, "bearish": 1, "neutral": 0},
            numbers_after_labels("偏多 6 偏空 1 方向不明 0", labels),
        )
        self.assertEqual(
            {"bullish": 1, "bearish": 6, "neutral": 0},
            numbers_after_labels("偏多 1 偏空 6 方向不明 0", labels),
        )

    def test_the_tally_reading_survives_a_rewrapped_region(self):
        """另一個 FP 方向：換標籤、改 class、多包兩層，讀出來要一樣。"""
        markup = (
            '<div id="live-tally"><section class="renamed"><p><b>偏多</b></p>'
            "<data>6</data></section></div>"
        )

        self.assertEqual(
            {"bullish": 6},
            numbers_after_labels(
                region_text(markup, "live-tally"), {"bullish": "偏多"}
            ),
        )

    def test_the_chat_room_still_carries_every_seat_that_spoke(self):
        run = self.tw_stock_run()
        spoke = {
            entry["seat_id"]
            for entry in self.events(run)
            if entry.get("event") == "seat_message"
        }
        feed = self.region(run, self.FEED_REGION_ID, "聊天室")

        self.assertEqual(set(SEAT_IDS), spoke)
        self.assert_named_by_one_set(
            names_shown_per_seat(feed), ASSET_CLASS_TW_STOCK, "聊天室"
        )

    def test_the_light_this_run_earned_is_shown_as_the_authoritys_own_word(self):
        """燈位的 markup 與語意色歸 R2 凍結的擁有者測試管；這裡只問跨票的那一件事：
        真的跑完的 run 在合併頁上，燈號是權威的中文詞，英文原值不出現。"""
        run = self.tw_stock_run()
        level = run.record("report.json")["confidence"]["level"]
        self.assertIn(level, pages.CONFIDENCE_WORDS)

        history = visible_text(self.get("/history").body)

        self.assertIn(pages.CONFIDENCE_WORDS[level], history)
        self.assertEqual([], english_values_in(history, {level}))

    def events(self, run):
        return [
            json.loads(line)
            for line in run.artifact("events.jsonl").splitlines()
            if line.strip()
        ]


class TheReportsAreReachableAndExportableTest(MenuRunFixture, unittest.TestCase):
    """Spec A2 第 2、3 條，在一份**完整 launch 產生**的 bundle 上。

    Ticket 06 用 ``write_run`` 與 fake tracer 的 bundle 驗過匯出模組的每一條路
    徑。這裡補的是它沒有的那一種 bundle：走完 launch 的 run 目錄還有 events、
    snapshots、agents 與 diagnostics，``run_verifier`` 對它的檢查也更多。轉換器仍
    然是注入的假的——本檔沒有任何測試啟動瀏覽器。
    """

    def test_both_report_links_on_the_run_page_point_at_files_that_exist(self):
        run = self.tw_stock_run()

        detail = self.get("/run/{}".format(run.run_id)).body
        for name in ("report.html", "debate.html"):
            self.assertIn("/run/{}/{}".format(run.run_id, name), detail, name)
            self.assertTrue((run.run_dir / name).is_file(), name)

    def test_an_export_adds_two_pdfs_and_changes_nothing_else(self):
        run = self.tw_stock_run()
        before = self.fingerprint(run)

        exported = pdf_export.export_run_pdfs(
            self.data_root, run.run_id, convert=self.fake_converter()
        )

        self.assertTrue(exported.ok, exported.message)
        after = self.fingerprint(run)
        self.assertEqual(before, {name: after[name] for name in before})
        self.assertEqual(
            sorted(pdf_export.EXPORT_TARGETS), sorted(set(after) - set(before))
        )

    def test_the_bundle_still_verifies_after_an_export(self):
        run = self.tw_stock_run()
        self.assertEqual("VERIFIED", verify_run(self.data_root, run.run_id)["status"])

        pdf_export.export_run_pdfs(
            self.data_root, run.run_id, convert=self.fake_converter()
        )

        self.assertEqual("VERIFIED", verify_run(self.data_root, run.run_id)["status"])

    def fake_converter(self):
        """一個假轉換器：寫出真的 PDF 開頭，內容明顯是合成的。"""

        def convert(source, target):
            Path(target).write_bytes(
                "%PDF-1.7\n{} 的假 PDF\n%%EOF\n".format(Path(source).name).encode("utf-8")
            )

        return convert

    def fingerprint(self, run):
        """run 目錄第一層每個檔案的內容雜湊與修改時刻。"""
        return {
            path.name: (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in sorted(run.run_dir.iterdir())
            if path.is_file()
        }


class TheRetiredEntryFilesAreGoneTest(unittest.TestCase):
    """Spec A1 最後一條：工作區根目錄不存在那兩個檔案。

    捷徑本身是實機操作，由 Ticket 07 在宿主上實測並記錄；可程式驗證的部分就是這兩
    個檔案不在。"缺席"在任何佈局下都是安全的斷言：Code Root 被單獨取出時上一層也
    沒有它們。
    """

    def test_neither_retired_entry_file_is_in_the_workspace_root(self):
        for name in RETIRED_ENTRY_FILES:
            self.assertFalse((WORKSPACE_ROOT / name).exists(), name)

    def test_the_check_looks_at_a_directory_that_is_really_there(self):
        """FP 方向：對著不存在的目錄問，兩條都會通過。"""
        self.assertTrue(WORKSPACE_ROOT.is_dir(), WORKSPACE_ROOT)
        self.assertTrue((WORKSPACE_ROOT / CODE_ROOT.name).is_dir())


if __name__ == "__main__":
    unittest.main()
