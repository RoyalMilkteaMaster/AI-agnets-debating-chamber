"""Read-only verification of one immutable run bundle."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .contract_validator import ContractViolationError, RUN_RULES_FIELD, load_run_rules
from .debate_rules import DebateRules, DebateRulesError
from .debate_state_machine import UNANIMOUS_BLIND_PASS
from .report_contract import (
    RULES_NOT_RECORDED,
    ReportContractError,
    validate_market_report,
)
from .report_audit_renderer import render_debate_html
from .report_renderer import render_market_html, render_market_markdown
from .report_workflow import HARD_DEADLINE_MS
from .research_scheduler import research_deadlines
from .run_store import resolve_run_dir
from .seats import SEAT_IDS
from .system_preflight import (
    NON_BLOCKING_CHECK_IDS,
    REQUIRED_CHECK_IDS,
    RUN_SCOPED_CHECK_IDS,
    load_frozen_roster,
)


REQUIRED_ARTIFACTS = (
    "manifest.json",
    "evidence.jsonl",
    "debate.jsonl",
    "votes.json",
    "report.md",
    "report.html",
)
PRESENTATION_VERSION = "2.0.0"
_FORBIDDEN_HTML_DEPENDENCIES = (
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"<link\b", re.IGNORECASE),
    re.compile(r"\bsrc\s*=\s*[\"']https?://", re.IGNORECASE),
    re.compile(r"@import\b", re.IGNORECASE),
)
# 共用導覽那一個 ``<nav>`` 的 class token。**這是給 parser 用的名字，不是一條
# 抽片段的 regex**：先前它是 ``<nav\b[^>]*class="page-tabs"[^>]*>(.*?)</nav>``，
# 由 :func:`re.findall` 把 nav 的內容挖出來再交給 parser。那條路上片段已經離開
# 原本的上下文，於是整段被 ``<!-- ... -->`` 註解掉的導覽仍然被數成一份、裡面的
# ``<a>`` 仍然被當成真的連結——瀏覽器一個都看不到的東西，驗證器全看到了。較弱的
# 工具先處理、較強的工具後接，較強的工具就只能看到較弱的工具准它看的東西。
# 現在整份文件只解析一次，由 :class:`_HTMLLinks` 在原始上下文裡追蹤這個元素。
#
# 比對方式是 **class token**（``class`` 是空白分隔的 token 清單），不是整個屬性
# 值字串相等：``class='page-tabs'``、``class="page-tabs wide"`` 都是 CSS 選擇器
# ``nav.page-tabs`` 選得到的同一個元素，而舊 regex 只認雙引號且要求整串相等。
_PAGE_TABS_CLASS = "page-tabs"
# 會指向 bundle 內檔案的屬性。**這是刻意畫小的範圍，不是「所有連結」**：
# ``href`` 與 ``src`` 各自只帶一個 URL，語法無歧義，而且是本專案兩個 renderer
# 唯一產出的那種。以下**不檢查**，如果哪天有人開始產出它們，這裡就是漏的：
# ``srcset``（逗號分隔的候選清單）、CSS 的 ``url()``（含 inline ``style`` 與
# ``<style>``）、``<object data>``、``<form action>``、``poster``。
# ``<base>`` 是例外：它不是連結，但它會改寫所有相對連結的解析基準，也就是讓
# 底下整套「相對連結必須是這個 run 目錄裡的檔案」的推論失效，所以它被直接拒絕
# 而不是被忽略。
_LINK_ATTRIBUTES = ("href", "src")
_REAL_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "system_preflight_id",
    "run_id",
    "competition_challenge",
    "seat_id",
    "attempt_id",
    "provider",
    "target_model",
    "actual_model",
    "dispatch",
    "completion",
    "search_receipt_path",
    "search_receipt_sha256",
    "raw_transcript_path",
    "raw_transcript_sha256",
    "output_path",
    "output_sha256",
}
_PHASE_RECEIPT_FIELDS = {"receipt_id", "at_utc", "elapsed_ms"}
_SEARCH_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "run_id",
    "seat_id",
    "attempt_id",
    "provider",
    "competition_challenge",
    "tool",
    "succeeded",
    "completed_at_utc",
    "elapsed_ms",
}
_SEARCH_TOOLS = {
    "codex": "web_search",
    "claude": "WebSearch",
    "antigravity": "search_web",
}
# 需要「該 run 當時的規則」才答得出來的檢查。manifest 沒有記錄規則時（B2 之前
# 產生的 run），**四項一致地跳過**並在 summary 裡逐項列出——拿現行設定去補就是
# 本票要修掉的那個誤判，換一個檢查項目也一樣。
#
# 前三項由 ``_verify_competition_timeline`` 自己跳過；``report_confidence_cap``
# 的權威住在 ``report_contract``，靠往下傳 ``RULES_NOT_RECORDED`` 讓它只放掉上
# 限那一項。跳過的是「這一項驗不了」，不是「這一項通過」。
RULE_DEPENDENT_CHECKS = (
    "debate_stop_reason",
    "debate_stop_at_ms_upper_bound",
    "stop_semantics",
    "report_confidence_cap",
)
_SHIPPED_FAKE_MARKERS = (
    "fake-competition-drill",
    "fake.invalid",
    "fake-source:",
    "fixture:",
    "fake drill",
    "deterministic fixture",
    "不得作為真實市場或訂閱 provider ready 證據",
)


class RunVerificationError(ValueError):
    pass


def verify_run(data_root, run_id):
    """Verify paths, hashes, seven-seat lineage and offline report constraints.

    規則來自**這個 run 自己的 manifest**，不是現行的 ``config/debate_rules.json``。
    設定頁改一次門檻，現讀的驗證就會拿新規則去判舊資料，於是一場從頭到尾都正確
    的 run 被以「T+10 有效票不足停止語意不一致」這種與它本身無關的理由拒絕。

    **有記錄規則的 run，整趟驗證一次也不查現行設定**，所以它的結果不隨設定改動
    而變——「run 目錄是不可變的完整證據」這件事延伸到了規則。同一份快照往下傳給
    時間線、停止語意與報告信心三處，所以整趟回答的是同一個問題。

    manifest 沒有記錄規則（B2 之前產生的 run）時，:func:`load_run_rules` 回傳
    ``None``＝未知。未知就不猜：:data:`RULE_DEPENDENT_CHECKS` 四項一致地跳過，
    並在 summary 的 ``rule_checks_without_run_rules`` 裡逐項列出。跳過的意思是
    「這幾項驗不了」，不是「這幾項通過」；其餘檢查一項不減。所以無論有沒有記錄
    規則，這裡都不會拿現行設定去判任何一份 run 的資料。
    """
    if not isinstance(run_id, str) or Path(run_id).name != run_id or run_id in (".", ".."):
        raise RunVerificationError("run_id 不得包含路徑。")
    root = Path(data_root).resolve()
    run_dir = resolve_run_dir(root, run_id)
    if run_dir is None or not run_dir.resolve().is_relative_to(root):
        raise RunVerificationError("找不到 Data Root 內的 run：{}".format(run_id))

    manifest = _read_json(run_dir / "manifest.json")
    if manifest.get("run_id") != run_id:
        raise RunVerificationError("manifest run_id 不一致。")
    try:
        rules = load_run_rules(manifest)
    except (ContractViolationError, DebateRulesError) as exc:
        raise RunVerificationError(
            "manifest 的規則快照無法載入：{}".format(exc)
        ) from exc
    rules_record = manifest.get(RUN_RULES_FIELD)
    artifact_index = manifest.get("artifacts")
    if not isinstance(artifact_index, dict):
        raise RunVerificationError("manifest 缺少 artifact index。")

    provider_mode = manifest.get("provider_mode")
    competition_ready = manifest.get("competition_ready") is True
    declares_real = provider_mode == "real-subscription" or competition_ready
    if declares_real and not (provider_mode == "real-subscription" and competition_ready):
        raise RunVerificationError("real provider mode 與 competition_ready 必須同時成立。")
    presentation_version = manifest.get("presentation_version")
    if presentation_version not in (None, PRESENTATION_VERSION):
        raise RunVerificationError("未知的報告 presentation_version。")
    if declares_real and presentation_version != PRESENTATION_VERSION:
        raise RunVerificationError("real competition run 必須提供可稽核雙頁報告。")
    required_artifacts = list(REQUIRED_ARTIFACTS)
    if presentation_version == PRESENTATION_VERSION:
        required_artifacts.append("debate.html")

    digests = {}
    for name in required_artifacts:
        path = run_dir / name
        _require_regular_file(path, run_dir)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests[name] = digest
        if name == "manifest.json":
            continue
        expected = artifact_index.get(name)
        expected_digest = expected.get("sha256") if isinstance(expected, dict) else None
        if expected_digest != digest:
            raise RunVerificationError("artifact hash 不符：{}".format(name))

    for name, record in artifact_index.items():
        if not isinstance(name, str) or not isinstance(record, dict) or record.get("path") != name:
            raise RunVerificationError("artifact index 路徑不一致。")
        path = run_dir / name
        _require_regular_file(path, run_dir)
        if hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
            raise RunVerificationError("artifact index hash 不符：{}".format(name))

    seats = manifest.get("seats")
    if not isinstance(seats, list) or [seat.get("seat_id") for seat in seats] != list(SEAT_IDS):
        raise RunVerificationError("manifest 必須包含固定七席且順序一致。")

    evidence = _read_jsonl(run_dir / "evidence.jsonl")
    debate = _read_jsonl(run_dir / "debate.jsonl")
    votes = _read_json(run_dir / "votes.json")
    evidence_seats = {record.get("seat_id") for record in evidence}
    debate_seats = {record.get("seat_id") for record in debate if record.get("seat_id")}
    vote_records = votes.get("votes")
    if not isinstance(vote_records, list):
        raise RunVerificationError("votes.json 缺少 votes 陣列。")
    vote_seats = {record.get("seat_id") for record in vote_records}
    expected_seats = set(SEAT_IDS)
    if (
        evidence_seats != expected_seats
        or debate_seats != expected_seats
        or vote_seats != expected_seats
        or len(vote_records) != len(SEAT_IDS)
    ):
        raise RunVerificationError("evidence/debate/votes 無法回查固定七席。")
    _verify_vote_table(votes, vote_records)
    # 時間戳的底線對所有可驗證 run 生效，不只對帶 competition_timeline 的那些；
    # 範圍檢查（封存到停止之間）才需要 timeline，見 _verify_competition_timeline。
    _verify_message_timestamps(debate)

    # 這份 bundle 該帶哪幾個 HTML 頁面，唯一權威是它的必備 artifact 清單——
    # 不是另抄一份頁面名單。抄第二份的話，加一頁只改到其中一份就會各自漂移。
    html_names = [name for name in required_artifacts if name.endswith(".html")]
    html_documents = {}
    for html_name in html_names:
        html = (run_dir / html_name).read_text(encoding="utf-8")
        html_documents[html_name] = html
        if any(pattern.search(html) for pattern in _FORBIDDEN_HTML_DEPENDENCIES):
            raise RunVerificationError(
                "{} 含 script 或外部 runtime dependency。".format(html_name)
            )
        if "<html" not in html.lower() or "@media print" not in html.lower():
            raise RunVerificationError(
                "{} 缺少離線 HTML 或列印樣式。".format(html_name)
            )
    if presentation_version == PRESENTATION_VERSION:
        for html_name, html in html_documents.items():
            _verify_shared_navigation(run_dir, html_name, html, html_names)

    timeline = manifest.get("competition_timeline")
    if declares_real and not isinstance(timeline, dict):
        raise RunVerificationError("real competition run 缺少完整 timeline。")
    if timeline is not None:
        _verify_report_lineage(
            run_dir,
            evidence,
            debate,
            votes,
            presentation_version=presentation_version,
            rules=rules,
        )
        _verify_competition_timeline(run_dir, manifest, debate, votes, timeline, rules)
    operational_advisories = []
    if declares_real:
        authorization = _verify_real_provider_lineage(root, manifest)
        operational_advisories = list(authorization.get("preflight_advisories", ()))
        receipt_payloads = _verify_real_run_receipts(
            run_dir,
            manifest,
            artifact_index,
            timeline,
            evidence,
            debate,
            votes,
            authorization,
        )
        _reject_shipped_fake_markers(
            manifest,
            evidence,
            debate,
            _read_json(run_dir / "report.json"),
            receipt_payloads,
        )

    return {
        "schema_version": "1.0.0",
        "status": "VERIFIED",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "seat_count": len(seats),
        "required_artifacts": digests,
        "provider_mode": provider_mode,
        "competition_ready": competition_ready,
        "advisories": operational_advisories,
        "timeline": timeline,
        # 這一趟是照哪一份規則驗的。``None``＝該 run 沒有記錄規則。
        "rules_sha256": rules_record["sha256"] if rules_record else None,
        # 沒有 timeline 就沒有跑到任何規則相關的檢查，那不算「驗不了」。
        "rule_checks_without_run_rules": (
            [] if rules is not None or timeline is None else list(RULE_DEPENDENT_CHECKS)
        ),
    }


class _HTMLLinks(HTMLParser):
    """把一份 HTML 的連結與共用導覽讀出來，用 HTML 的規則而不是字串的形狀。

    先前這件事是 ``re.compile(r'href="([^"]*)"')`` 做的，於是
    ``<a href='ghost.html'>`` 與 ``<a href=ghost.html>`` 這兩種**完全合法**的寫法
    直接不存在——檢查器看不到的連結當然通得過。合法 HTML 的屬性有三種引號寫法，
    要嘛全認，要嘛就等著下一個人用另外兩種。stdlib 的 :class:`~html.parser.
    HTMLParser` 三種都認，而且順手把 ``&amp;`` 這類 entity 還原成瀏覽器真正會去
    取的那個路徑，這也是自己寫 regex 一定會漏的一步。

    ``navigations`` 是**同一趟解析**認出來的每一個 ``nav.page-tabs``，每一項是那
    份導覽自己的 ``(href, aria-current)`` 清單。它在這裡而不是留給呼叫端先用
    regex 把 nav 挖出來再解析，理由和上一段是同一個：那條路上片段已經離開原本的
    上下文，於是整段被註解掉的導覽——瀏覽器一個字都不會讀的東西——照樣被數成一份
    合格的導覽，裡面的 ``<a>`` 照樣被當成真的連結。註解、``<script>``、
    ``<style>`` 的內容都不是標記，而「什麼算標記」只有 parser 答得出來；先抽片段
    就是把這個問題交給答不出來的那個工具。

    ``nav`` 會巢狀，所以追的是**開著的 nav 堆疊**而不是一個旗標：``<a>`` 記到堆疊
    上最內層那個 ``page-tabs`` 導覽裡，內層 nav 收掉之後外層還在。多餘的
    ``</nav>``（沒有對應的開標籤）忽略，因為那不會讓瀏覽器多出一份導覽。

    重複屬性取**第一個**，跟瀏覽器一致：``<a href="a.html" href="evil.html">`` 打
    開的是 ``a.html``，所以要驗的也是 ``a.html``。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.link_targets = []
        self.navigations = []
        self.declares_a_base = False
        # 目前開著的每一個 ``<nav>``：是 ``page-tabs`` 就記它在 ``navigations``
        # 裡的位置，否則記 ``None``。
        self._open_navs = []

    def handle_starttag(self, tag, attrs):
        values = {}
        for name, value in attrs:
            values.setdefault(name, value)
        if tag == "base":
            self.declares_a_base = True
        for name in _LINK_ATTRIBUTES:
            if isinstance(values.get(name), str):
                self.link_targets.append(values[name])
        if tag == "nav":
            self._open_navs.append(self._opened_navigation(values))
        if tag == "a":
            navigation = self._innermost_navigation()
            if navigation is not None:
                navigation.append((values.get("href"), values.get("aria-current")))

    def handle_endtag(self, tag):
        if tag == "nav" and self._open_navs:
            self._open_navs.pop()

    def _opened_navigation(self, values):
        """Start a navigation for a ``nav.page-tabs``, and return its place."""
        classes = values.get("class")
        if not isinstance(classes, str) or _PAGE_TABS_CLASS not in classes.split():
            return None
        self.navigations.append([])
        return len(self.navigations) - 1

    def _innermost_navigation(self):
        """Return the anchor list an ``<a>`` here belongs to, or ``None``."""
        for place in reversed(self._open_navs):
            if place is not None:
                return self.navigations[place]
        return None


def _read_links(html_name, html):
    """Return the parsed links of one page, or refuse if it will not parse.

    讀不動的文件不是「沒有問題的文件」。解析失敗時底下每一項檢查都失去依據，
    所以這裡直接擋掉，而不是拿一份空的連結清單去宣告通過。

    **整份文件一次**：連結與共用導覽都從這一趟的結果讀，沒有第二次解析、也沒有
    任何一段先被 regex 切出來。
    """
    links = _HTMLLinks()
    try:
        links.feed(html)
        links.close()
    except Exception as exc:  # noqa: BLE001 - 解析不動就沒有結論可下
        raise RunVerificationError(
            "{} 無法解析為 HTML（{}：{}），導覽與連結檢查失去依據。".format(
                html_name, type(exc).__name__, exc
            )
        ) from exc
    if links.declares_a_base:
        raise RunVerificationError(
            "{} 帶著 <base>，它會改寫所有相對連結的解析基準，"
            "離線 bundle 不得這樣做。".format(html_name)
        )
    return links


def _verify_shared_navigation(run_dir, html_name, html, page_names):
    """Check one page's shared navigation against the bundle it ships inside.

    導覽該有哪幾頁**不是一份手寫清單**。權威是這份 bundle 自己該帶的 HTML
    artifact（``page_names``，由必備 artifact 清單推導），所以「加一頁」只有一
    個地方要改，清單也無從和實際產出各自漂移。

    先前那份手寫清單的第一項是 ``live.html``，而它從來不是 run 目錄裡的檔案：
    舊的直播頁走伺服器 URL，Ticket 10 退役 live_dashboard 之後連那條脈絡都沒
    有了。於是驗證器不是漏看死連結，是反過來**強制**每一份離線報告帶著一個永
    遠 404 的連結出貨。

    兩個方向都驗，缺一個就只修了一半：

    * **少連**：任何一頁沒連到它的姊妹頁，共用導覽原本要保證的「每一頁都連得到
      其他頁」就不成立；每一頁還必須標示自己是目前頁面，否則輔助科技讀不出讀者
      站在哪。
    * **連到 bundle 沒有的東西**：導覽與內文出現在 :data:`_LINK_ATTRIBUTES`
      （``href`` 與 ``src``，就這兩個）上的每一個相對連結，都必須是這個 run 目錄
      裡真的存在的檔案。**這不是「每一個相對連結」**——那個範圍是刻意畫小的，沒
      被檢查的還有哪些寫在 :data:`_LINK_ATTRIBUTES` 上面。硬編清單擋不住下一個人
      加的下一個 dead tab，問檔案系統可以。

    兩個方向都是**解析出來的**，不是字串比對出來的，而且是**同一趟解析**。導覽
    那一段以前問的是 ``'href="debate.html"' in navigation``，於是
    ``<!-- href="debate.html" -->`` 這種註解也算它連到了；``aria-current`` 更是
    要求它剛好緊接在 ``href`` 後面，屬性換個順序就誤判。改成 parser 之後又留了
    半步：nav 是先用 regex 從整份文件挖出來、再把片段交給 parser 的，於是整段被
    註解掉的 ``<nav class="page-tabs">`` 仍然被數成一份合格的導覽——瀏覽器看不到
    的導覽，驗證器看到了，而且認它。現在整份文件只解析一次，導覽是 parser 在原始
    上下文裡認出來的元素；註解裡的東西沒有元素，所以不存在。
    """
    links = _read_links(html_name, html)
    if len(links.navigations) != 1:
        raise RunVerificationError(
            "{} 必須恰好帶一份共用導覽，實際有 {} 份。".format(
                html_name, len(links.navigations)
            )
        )
    anchors = links.navigations[0]
    for page_name in page_names:
        linked = [current for target, current in anchors if target == page_name]
        if not linked:
            raise RunVerificationError(
                "{} 的共用導覽缺少 {}。".format(html_name, page_name)
            )
        # 自己那一格要標成目前頁面；其餘只要連得到。
        if page_name == html_name and "page" not in linked:
            raise RunVerificationError(
                "{} 的共用導覽沒有把 {} 標成目前頁面（aria-current）。".format(
                    html_name, page_name
                )
            )
    # 範圍是整份文件而不只有導覽：頁內的相對連結（例如「查看七席如何形成判斷」）
    # 一樣是離線讀者點得到的連結，死在哪裡都一樣死。用的是上面那一趟的結果。
    for target in links.link_targets:
        unreachable = _unreachable_bundle_link(run_dir, target)
        if unreachable is not None:
            raise RunVerificationError(
                "{} 連到 bundle 未提供的 {}。".format(html_name, unreachable)
            )


def _unreachable_bundle_link(run_dir, target):
    """Return the link target this bundle cannot open on its own, or ``None``.

    頁內錨點不離開文件；``http``／``https`` 是證據來源，所以這裡放行。

    **放行不等於「已經有人把關」，這件事要講清楚。**``report_contract.
    is_safe_source_url`` 決定哪些來源 URL 會被 renderer 畫成 active link，而
    :data:`_FORBIDDEN_HTML_DEPENDENCIES` 擋的是 ``<script>``、``<link>``、
    ``@import``，以及**帶引號的** ``src="https://…"``。落在這三者之外的遠端相依
    （不帶引號的 ``src=https://…``、``srcset``、``poster``、CSS 的 ``url()``）
    目前**沒有任何一層在看**——這裡放行 http／https，那條 regex 又對不上。範圍
    寫在這裡是為了讓下一個人知道自己在依賴什麼，不是為了讓它聽起來已經蓋住了。

    其餘一律當成 bundle 內的相對路徑，而且必須真的是這個 run 目錄裡的一般檔案。

    scheme 用 :func:`~urllib.parse.urlsplit` 取再轉小寫，不用 ``startswith``。
    URL scheme 依規範**大小寫不敏感**，而 ``report_contract.is_safe_source_url``
    早就是這樣判的，所以 ``HTTPS://…`` 會被 renderer 輸出成一個 active link——
    然後被 ``startswith(("http://", "https://"))`` 當成「bundle 裡不存在的檔案」
    退掉。同一件事在兩個地方用兩套規則判，遲早有一邊是錯的；這裡改成跟另一邊
    同一套。

    絕對路徑（``/live``）因此也過不了，這是刻意的：離線 bundle 的契約是**它自己
    就能看**，指向一個要跑伺服器才存在的路徑只是把死連結換成「沒開伺服器就壞掉」
    的連結，不是修好。``//host/path`` 這種 protocol-relative 寫法同理。符號連結
    與指向目錄外的路徑同樣不算數，理由與 :func:`_require_regular_file` 相同——
    不可變 bundle 的內容必須就在它自己裡面。

    ``mailto:``、``data:``、``javascript:`` 等其他 scheme 沒有被列舉，也不需要：
    **有 scheme 而不是 ``http``／``https`` 的一律拒絕**，只有 scheme 為空的目標才
    走下面的相對路徑判定。

    那個「才」是補回來的一步，不是修辭。先前這裡只有正面放行 ``http``／
    ``https``，沒被放行的非空 scheme 就掉進了「當成 bundle 內的相對檔名」那條
    路——於是在 run 目錄裡建一個名叫 ``javascript:alert(1)`` 的檔案，
    ``<a href="javascript:alert(1)">`` 就通過了，整份 bundle 照樣 VERIFIED。
    ``mailto:``、``data:``、``file:`` 同理，而且檔名由誰決定不重要：這個檢查的
    問題是「離線讀者點下去會不會離開這份 bundle」，而任何非空 scheme 的答案都是
    會，不管檔案系統上剛好有沒有同名的檔案。名字碰巧撞上就放行，等於讓檔名決定
    一個和檔案無關的問題。
    """
    try:
        scheme = urlsplit(target).scheme.lower()
    except ValueError:
        # 連 scheme 都拆不出來的目標，沒有任何理由當成安全的。
        return target
    if scheme in ("http", "https"):
        return None
    if scheme:
        return target
    relative = target.split("#", 1)[0].split("?", 1)[0]
    if not relative:
        return None
    if relative.startswith("/"):
        return relative
    candidate = run_dir / relative
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or not candidate.resolve().is_relative_to(run_dir.resolve())
    ):
        return relative
    return None


def _verify_competition_timeline(run_dir, manifest, debate, votes, timeline, rules):
    """Check the competition timeline against the rules that run actually used.

    ``rules`` 沒有預設值，而且**不接受「省略＝現讀」**。這個函式回答的是「這份
    artifact 對某一套規則是否成立」，那套規則是輸入，不是環境。留一個現讀的預設
    值等於在一個呼叫點之外重新種下本票要修的 bug——忘了傳就靜靜換一套規則。現
    在忘了傳會是 ``TypeError``。

    ``rules=None`` 是明確的第三種值：**這個 run 沒有記錄它跑的規則**。此時
    :data:`RULE_DEPENDENT_CHECKS` 前三項跳過，其餘照驗。
    """
    if not isinstance(timeline, dict):
        raise RunVerificationError("competition_timeline 必須為 object。")
    # 這一場的收件牆與封存時刻由題型決定；權威是 research_deadlines，
    # 不是 manifest 自己宣告的數字。
    deadlines = research_deadlines(_question_type(run_dir, manifest))
    if timeline.get("all_seats_dispatched_at_ms") != 0:
        raise RunVerificationError("七席必須在 T+0 同時 dispatch。")
    completions = timeline.get("seat_completion_ms")
    if not isinstance(completions, dict) or set(completions) != set(SEAT_IDS):
        raise RunVerificationError("competition timeline 必須包含七席 completion。")
    if any(
        type(value) is not int or value < 0 or value > deadlines.accept_until_ms
        for value in completions.values()
    ):
        raise RunVerificationError("研究席未在收件牆前完成有效 contract。")
    if timeline.get("evidence_snapshot_sealed_at_ms") != deadlines.seal_ms:
        raise RunVerificationError("Evidence snapshot 未在該題型的封存時刻 seal。")
    snapshot = run_dir / "snapshots" / "evidence.jsonl"
    _require_regular_file(snapshot, run_dir)
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if timeline.get("evidence_snapshot_sha256") != snapshot_sha:
        raise RunVerificationError("封存證據 snapshot hash 不一致。")
    if snapshot.read_bytes() != (run_dir / "evidence.jsonl").read_bytes():
        raise RunVerificationError("正式 evidence.jsonl 與 sealed snapshot 不一致。")

    stop_reason = timeline.get("debate_stop_reason")
    # 停止原因的合法集合有一半是規則的函數（``consensus_<門檻>_votes``）。規則
    # 未知時只驗與規則無關的那一半：它得是個名字。
    if not isinstance(stop_reason, str) or not stop_reason.strip():
        raise RunVerificationError("辯論停止原因必須是非空字串。")
    if rules is not None and stop_reason not in _legal_stop_reasons(rules):
        raise RunVerificationError("未知辯論停止原因。")
    stop_ms = timeline.get("debate_stop_at_ms")
    # 下界（封存時刻）與規則無關；上界是該版規則的最終結算時刻。
    # 規則未知時就沒有上界，不拿現行設定去補一個。
    if type(stop_ms) is not int or stop_ms < deadlines.seal_ms:
        raise RunVerificationError("辯論停止時間早於證據封存。")
    if rules is not None and stop_ms > _final_settle_ms(deadlines, rules):
        raise RunVerificationError("辯論停止時間超出當次規則的最終結算時刻。")
    if votes.get("stop_reason") != stop_reason or votes.get("stop_elapsed_ms") != stop_ms:
        raise RunVerificationError("timeline 與 votes 停止紀錄不一致。")
    if manifest.get("tally") != votes.get("tally"):
        raise RunVerificationError("manifest 與 votes 票數不一致。")
    _verify_tally_enum(votes)
    _verify_message_timestamps(debate, deadlines.seal_ms, stop_ms)
    if isinstance(rules, DebateRules):
        _verify_v2_final_vote_timing(debate, deadlines, rules)
    _verify_attempt_lineage(debate, votes)
    if stop_reason == UNANIMOUS_BLIND_PASS:
        _verify_blind_pass_record(debate, votes)
    # v1 快照與無快照舊 run 保留當時的挑戰票生命週期；
    # v2 已退役該關卡。
    elif not isinstance(rules, DebateRules):
        _verify_first_round_lineage(debate, votes)
    if not isinstance(rules, DebateRules):
        _verify_challenge_completed(debate, votes)
    if rules is not None:
        _verify_stop_semantics(votes, stop_reason, stop_ms, deadlines, rules)

    report_ms = timeline.get("report_completed_at_ms")
    if type(report_ms) is not int or report_ms >= HARD_DEADLINE_MS:
        raise RunVerificationError("報告未在 T+15 前完成。")
    if report_ms - stop_ms > 180_000:
        raise RunVerificationError("Core 報告超過共識後三分鐘。")
    if timeline.get("report_hard_deadline_ms") != HARD_DEADLINE_MS:
        raise RunVerificationError("report hard deadline 不是 T+15。")
    if manifest.get("elapsed_ms") != report_ms:
        raise RunVerificationError("manifest elapsed_ms 與 report timeline 不一致。")


def _legal_stop_reasons(rules):
    """Return stop names expressible by one recorded rule snapshot."""
    if isinstance(rules, DebateRules):
        return {
            UNANIMOUS_BLIND_PASS,
            *("consensus_{}_votes".format(item.threshold) for item in rules.vote_rounds),
            "forced_stop_{}_votes".format(rules.vote_rounds[-1].threshold),
            "forced_stop_no_consensus",
            "forced_stop_insufficient_valid_votes",
        }
    return {
        UNANIMOUS_BLIND_PASS,
        "consensus_{}_votes".format(rules.initial_votes),
        "consensus_{}_votes".format(rules.reduced_votes),
        "forced_stop_{}_votes".format(rules.forced_stop_votes),
        "forced_stop_no_consensus",
        "forced_stop_insufficient_valid_votes",
    }


def _final_settle_ms(deadlines, rules):
    if isinstance(rules, DebateRules):
        return deadlines.seal_ms + rules.final_settle_offset_ms
    return rules.force_stop_ms


def _question_type(run_dir, manifest):
    """Return the run's own question type, refusing two disagreeing copies.

    改 manifest 的題型就能換到晚 30 秒的封存，所以只要 ``question.json``
    也記了題型，兩份必須一致；缺漏時退回預設（較嚴）的時刻表。
    """
    declared = manifest.get("question_type")
    recorded = _optional_json(run_dir / "question.json").get("question_type")
    if declared is not None and recorded is not None and declared != recorded:
        raise RunVerificationError("manifest 與 question.json 的題型不一致。")
    return declared if declared is not None else recorded


def _optional_json(path):
    if not path.is_file():
        return {}
    return _read_json(path)


def _verify_vote_table(votes, vote_records):
    tally = votes.get("tally")
    if not isinstance(tally, dict) or not tally:
        raise RunVerificationError("votes tally 必須為非空 object。")
    if any(type(count) is not int or count < 0 for count in tally.values()):
        raise RunVerificationError("votes tally 含無效票數。")
    recomputed = {stance: 0 for stance in tally}
    modern = "valid_vote_count" in votes
    valid_count = 0
    for record in vote_records:
        if not isinstance(record, dict):
            raise RunVerificationError("votes 含無效席位紀錄。")
        if modern and record.get("state") != "valid":
            continue
        stance = record.get("final_stance") if modern else record.get("stance")
        if stance not in recomputed:
            raise RunVerificationError("有效票立場不在 tally。")
        recomputed[stance] += 1
        valid_count += 1
    count_matches = votes.get("valid_vote_count") == valid_count if modern else True
    if not count_matches or tally != recomputed:
        raise RunVerificationError("votes tally 與逐席有效票不一致。")


def _verify_blind_pass_record(debate, votes):
    """A blind pass is seven openings, and those openings *are* the seven votes.

    兩件事都要驗，缺一不可：

    1. 「辯論可為空」在這個停止原因下不只是允許，而是必要條件——直過的前提就
       是收齊開場時公開紀錄裡還沒有任何 challenge／response／final_vote。
    2. 直過沒有第二套計票路徑：每一席的正式票就是它自己那則開場原文。這一條
       必須由 verifier 強制，不能只靠實作自律，否則一份與公開紀錄矛盾的票表
       （例如某席開場投反方、票表卻記正方）就能冒充七票一致。
    """
    openings = _blind_pass_openings(debate)
    rows = {
        row.get("seat_id"): row
        for row in votes.get("votes", ())
        if isinstance(row, dict)
    }
    tally = votes.get("tally")
    # 計票欄位取自本場的立場 enum，不取自受驗資料自己的 tally 鍵：沿用被驗者
    # 的鍵集合等於讓 bundle 自己定義計票欄位，多塞一個永遠是 0 的立場，任何
    # 「重算後相等」的檢查都還是會相等。enum 與 tally 的一致性由
    # ``_verify_tally_enum`` 先行把關。
    recomputed = {stance: 0 for stance in votes.get("stances", ())}
    for seat_id in SEAT_IDS:
        opening = openings[seat_id]
        # 先驗開場自己的形狀與它跟票列的對應，再計票：立場欄位要先被證明是非
        # 空字串，「不在 tally」這句話才有意義。
        problem = _blind_pass_seat_problem(opening, rows.get(seat_id) or {})
        if problem is not None:
            raise RunVerificationError(
                "{} 的公開開場與正式票表不一致：{}".format(seat_id, problem)
            )
        stance = opening["stance"]
        if stance not in recomputed:
            raise RunVerificationError(
                "盲投直過的開場立場 {!r} 不在 tally。".format(stance)
            )
        recomputed[stance] += 1
    if recomputed != tally:
        raise RunVerificationError(
            "盲投直過由七則開場重算的票數 {} 與 votes.json 的 tally {} 不一致。".format(
                recomputed, tally
            )
        )


def _blind_pass_openings(debate):
    """Return the seven opening messages, refusing any other public record."""
    seat_messages = [
        entry for entry in debate if entry.get("event") == "seat_message"
    ]
    openings = {entry.get("seat_id"): entry for entry in seat_messages}
    if (
        {entry.get("kind") for entry in seat_messages} != {"position"}
        or len(seat_messages) != len(SEAT_IDS)
        or set(openings) != set(SEAT_IDS)
    ):
        raise RunVerificationError(
            "盲投直過的公開紀錄必須恰好是七則開場，不得含任何辯論訊息。"
        )
    return openings


def _verify_message_timestamps(debate, seal_ms=None, stop_ms=None):
    """Every public seat message must carry a sane monotonic timestamp.

    分兩層，因為兩層的適用範圍不同：

    * **無條件**：``elapsed_ms`` 必須是非負的精確整數（``True`` 是 ``bool``，
      不算）。狀態機讀單調時鐘，負的經過時間在任何 run 裡都不可能出現，所以這
      是每一份可驗證 bundle 的共同底線——不能只在 manifest 有
      ``competition_timeline`` 時才生效。
    * **有 timeline 時追加**：必須落在該場的 ``[封存時刻, 停止時刻]`` 內。辯論
      室封存前開不了、停止後不再收件，所以窗外的時間戳同樣是狀態機產不出來的。
    """
    for entry in debate:
        if entry.get("event") != "seat_message":
            continue
        elapsed = entry.get("elapsed_ms")
        if type(elapsed) is not int or elapsed < 0:
            raise RunVerificationError(
                "{} 的公開訊息 elapsed_ms（{!r}）必須是非負整數毫秒。".format(
                    entry.get("seat_id"), elapsed
                )
            )
        if seal_ms is not None and not seal_ms <= elapsed <= stop_ms:
            raise RunVerificationError(
                "{} 的公開訊息 elapsed_ms（{!r}）不在本場的辯論時間窗 [{}, {}] 內。".format(
                    entry.get("seat_id"), elapsed, seal_ms, stop_ms
                )
            )


def _verify_v2_final_vote_timing(debate, deadlines, rules):
    """Match every v2 final vote to the run snapshot's inter-wall turn."""
    round_starts = [
        vote_round.open_offset_ms for vote_round in rules.vote_rounds[:-1]
    ]
    if not round_starts:
        round_starts = [rules.vote_rounds[0].open_offset_ms]
    round_ends = round_starts[1:] + [rules.final_settle_offset_ms]

    openings = {}
    for entry in debate:
        if entry.get("event") != "seat_message" or entry.get("kind") != "position":
            continue
        openings.setdefault(entry.get("seat_id"), entry.get("elapsed_ms"))

    for entry in debate:
        if entry.get("event") != "seat_message" or entry.get("kind") != "final_vote":
            continue
        seat_id = entry.get("seat_id")
        round_number = entry.get("round")
        if (
            type(round_number) is not int
            or round_number < 1
            or round_number > len(round_starts)
        ):
            raise RunVerificationError(
                "{} 的 final_vote round 必須是 1..{} 的整數。".format(
                    seat_id, len(round_starts)
                )
            )

        elapsed = entry["elapsed_ms"]
        opening_elapsed = openings.get(seat_id)
        if opening_elapsed is None or elapsed < opening_elapsed:
            raise RunVerificationError(
                "{} 的 final_vote 時間不得早於同席 opening。".format(seat_id)
            )

        index = round_number - 1
        lower = deadlines.seal_ms + round_starts[index]
        upper = deadlines.seal_ms + round_ends[index]
        if not lower <= elapsed < upper:
            raise RunVerificationError(
                "{} 的 final_vote 時間不在 round {} 的 [{}, {}) 區間。".format(
                    seat_id, round_number, lower, upper
                )
            )


def _verify_attempt_lineage(debate, votes):
    """Each seat's attempt list must be exactly what the public record shows.

    重算規則直接對齊狀態機的 ``_validate_lineage``，不另立一套：

    * attempt ID 必須是非空字串——狀態機對空的或非字串 attempt 一律丟
      ``UnknownAttemptError``。不先擋住它，``[None] == [None]`` 會讓相等比對
      自己騙自己。
    * 第 n 個 attempt 的名字**恰好**是 ``"{seat_id}-a{n}"``。只做「首次出現去
      重」等於只對齊了 ``_record``：那樣一份從 a2 開場、或 a1 之後直接跳到 a3
      的 bundle 仍然過得去，而狀態機兩者都不可能產生。
    * 第二個 attempt 起，每一個都必須有自己的 ``replacement_replayed_public
      _history`` 事件，且成對出現：事件排在該 attempt 的第一則訊息之前、
      ``replaced_attempt_id`` 指向前一個 attempt、``replayed_history_sha256``
      等於它前一則 entry 的公開歷史雜湊（狀態機就是拿這個值比對的）。單有訊息
      沒有事件、或單有事件沒有訊息，都是狀態機不可能產生的形狀。

    §5.2 的合法替補（a1 完成第一輪、a2 才投出正式票）完全不受影響。
    """
    attempts, first_seen = _public_attempts(debate)
    events = _replacement_events(debate)
    for seat_id in SEAT_IDS:
        problem = _attempt_lineage_problem(
            seat_id, attempts[seat_id], first_seen[seat_id], events[seat_id], debate
        )
        if problem is not None:
            raise RunVerificationError(
                "{} 的 attempt lineage 與公開紀錄不一致：{}".format(seat_id, problem)
            )
    for row in votes.get("votes", ()):
        if not isinstance(row, dict):
            continue
        seat_id = row.get("seat_id")
        if row.get("attempt_ids") != attempts.get(seat_id):
            raise RunVerificationError(
                "{} 的 attempt lineage 與公開紀錄不一致：票表 {!r}，公開紀錄 {!r}。".format(
                    seat_id, row.get("attempt_ids"), attempts.get(seat_id)
                )
            )


def _canonical_numbering_problem(seat_id, attempts):
    """Every formal attempt must be exactly ``"{seat_id}-a{n}"`` for its position.

    ``DebateStateMachine._validate_lineage`` 只接受這一種名字，n 就是該 attempt
    的序位——所以「開場就用 a2」「a1 之後跳到 a3」「``a01``」「別席的名字」全
    是狀態機不可能產生的 lineage。

    這裡刻意**不留任何命名體系的豁免**。先前為了不打掉
    ``tests/test_reviewer_complete_attack`` 的合成形狀而只比對「長得像 canonical
    的名字」，但那只擋住特定拼字：改一個字元（``b2``／``A2``／``a1x``）就重新
    接受狀態機不可能產生的稽核資料。該測試已改為對齊 canonical 命名，其斷言
    （VERIFIED／competition_ready／advisories）完全維持。
    """
    for number, attempt_id in enumerate(attempts, start=1):
        expected = "{}-a{}".format(seat_id, number)
        if attempt_id != expected:
            return "第 {} 個 attempt 應為 {!r}，公開紀錄是 {!r}".format(
                number, expected, attempt_id
            )
    return None


def _public_attempts(debate):
    """Return each seat's attempts in first-appearance order, plus where they start."""
    attempts = {seat_id: [] for seat_id in SEAT_IDS}
    first_seen = {seat_id: {} for seat_id in SEAT_IDS}
    for index, entry in enumerate(debate):
        if entry.get("event") != "seat_message":
            continue
        seat_id = entry.get("seat_id")
        if seat_id not in attempts:
            continue
        attempt_id = entry.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise RunVerificationError(
                "{} 的公開訊息缺少非空字串 attempt_id。".format(seat_id)
            )
        if attempt_id not in first_seen[seat_id]:
            attempts[seat_id].append(attempt_id)
            first_seen[seat_id][attempt_id] = index
    return attempts, first_seen


def _replacement_events(debate):
    """Return each seat's replacement replay events, in public record order."""
    events = {seat_id: [] for seat_id in SEAT_IDS}
    for index, entry in enumerate(debate):
        if entry.get("event") != "replacement_replayed_public_history":
            continue
        seat_id = entry.get("seat_id")
        if seat_id not in events:
            raise RunVerificationError(
                "replacement 事件指向未知席位：{!r}".format(seat_id)
            )
        events[seat_id].append((index, entry))
    return events


def _attempt_lineage_problem(seat_id, attempts, first_seen, events, debate):
    """Name why this seat's attempt lineage could not have come from the machine."""
    problem = _canonical_numbering_problem(seat_id, attempts)
    if problem is not None:
        return problem
    replacements = attempts[1:]
    if [entry.get("attempt_id") for _, entry in events] != replacements:
        return "replacement 事件 {!r} 與替補 attempt {!r} 未一一對應".format(
            [entry.get("attempt_id") for _, entry in events], replacements
        )
    for position, (index, entry) in enumerate(events):
        attempt_id = replacements[position]
        if entry.get("replaced_attempt_id") != attempts[position]:
            return "{} 的 replacement 事件宣稱替換 {!r}，實際前一個 attempt 是 {!r}".format(
                attempt_id, entry.get("replaced_attempt_id"), attempts[position]
            )
        # 相鄰，不只是「比較早」：``DebateStateMachine._record`` 先寫 replay
        # 事件、緊接著就寫該 attempt 的第一則訊息，中間沒有任何插入視窗。只驗
        # 先後的話，中間塞一則別席的 final_vote 也過得去。
        if index + 1 != first_seen[attempt_id]:
            return "{} 的 replacement 事件未緊鄰它自己的第一則訊息".format(attempt_id)
        if index == 0 or entry.get("replayed_history_sha256") != debate[index - 1].get(
            "public_history_sha256"
        ):
            return "{} 的 replacement 事件未重播當下的完整公開歷史".format(attempt_id)
    return None


def _blind_pass_seat_problem(opening, row):
    """Name the first field where a seat's opening and its official vote differ.

    先確認開場自己的識別欄位是非空字串，再做相等比對：兩邊同時是 ``None`` 時
    ``[None] == [None]`` 會成立，等於讓一份殘缺的公開紀錄替自己背書。attempt
    lineage 不在這裡比——它的唯一權威是 :func:`_verify_attempt_lineage`。
    """
    for name in ("message_id", "content_sha256", "stance", "public_reason"):
        value = opening.get(name)
        if not isinstance(value, str) or not value.strip():
            return "開場的 {} 不是非空字串".format(name)
    evidence_ids = opening.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(not isinstance(item, str) or not item.strip() for item in evidence_ids)
    ):
        return "開場的 evidence_ids 不是非空字串陣列"
    stance = opening["stance"]
    reason = opening["public_reason"]
    checks = (
        ("state", row.get("state") == "valid"),
        ("initial_stance", row.get("initial_stance") == stance),
        ("final_stance", row.get("final_stance") == stance),
        ("initial_public_reason", row.get("initial_public_reason") == reason),
        ("final_public_reason", row.get("final_public_reason") == reason),
        ("initial_evidence_ids", row.get("initial_evidence_ids") == evidence_ids),
        ("final_evidence_ids", row.get("final_evidence_ids") == evidence_ids),
        ("stance_changed", row.get("stance_changed") is False),
        ("stance_change_reason", row.get("stance_change_reason") is None),
        ("vote_changes", row.get("vote_changes") == []),
        ("message_ids", row.get("message_ids") == [opening.get("message_id")]),
        (
            "content_sha256",
            row.get("content_sha256") == [opening.get("content_sha256")],
        ),
    )
    return next((name for name, holds in checks if not holds), None)


def _verify_tally_enum(votes):
    """The tally's columns must be exactly this run's own stance enum.

    重算票數時若沿用受驗資料自己的 tally 鍵，等於讓 bundle 自己定義計票欄位：
    多塞一個永遠是 0 的立場，「重算後相等」的檢查照樣會相等。立場詞彙的權威是
    該場自己記下的 ``stances``（狀態機由題型推導後寫入 votes.json）。
    """
    stances = votes.get("stances")
    tally = votes.get("tally")
    if (
        not isinstance(stances, list)
        or not stances
        or len(set(stances)) != len(stances)
        or not isinstance(tally, dict)
        or set(tally) != set(stances)
    ):
        raise RunVerificationError(
            "votes tally 的立場欄位必須恰好是本場記錄的立場 enum。"
        )


def _verify_challenge_completed(debate, votes):
    """Recompute the room-level flag from the public record and demand equality.

    這個旗標是「所有公開過開場的席位是否都完成質詢」。只驗它是布林值只擋得住
    型別錯誤，擋不住說謊：真的辯論過的 bundle 可以謊稱 False，一席掉隊的
    bundle 也可以謊稱 True。報告與稽核都讀這個欄位，所以它必須從公開紀錄本身
    重算出來，而不是被 bundle 自己宣告。

    重算方式逐字對齊 ``DebateStateMachine.summary``：參與者＝公開過開場的席
    位；完成＝該席至少各有一則 challenge 與 response（不限回合，回合順序另由
    :func:`_verify_first_round_lineage` 逐席把關）。
    """
    kinds = {}
    for entry in debate:
        if entry.get("event") != "seat_message":
            continue
        kinds.setdefault(entry.get("seat_id"), set()).add(entry.get("kind"))
    participants = [marks for marks in kinds.values() if "position" in marks]
    recomputed = bool(participants) and all(
        {"challenge", "response"} <= marks for marks in participants
    )
    if votes.get("challenge_completed") is not recomputed:
        raise RunVerificationError(
            "votes.challenge_completed（{!r}）與公開紀錄重算的結果（{!r}）不一致。".format(
                votes.get("challenge_completed"), recomputed
            )
        )


def _verify_first_round_lineage(debate, votes):
    """Every valid vote must show its own first round, in the order §5.4 requires.

    §5.4：**完成**第一輪反方挑戰後，該席的票才成為有效票。「完成」蘊含順序，
    所以只確認「這三種訊息都出現過」是不夠的——把 final vote 搬到 challenge
    之前，訊息集合完全不變，但那張票在狀態機裡根本不可能產生。

    這是逐席規則，和房間層級的 ``challenge_completed`` 不是同一件事：一席
    provider 超時、其餘席位正常完成並成立共識是合法形狀，那時旗標是 False，
    但每一張有效票仍各自完成了自己的第一輪。

    替補（跨 attempt 接續）刻意不擋：position／challenge／response 在 a1、
    final vote 在 a2 是 §5.2 核准的形狀，這裡只看公開紀錄的先後與回合。
    """
    timeline = {}
    for index, entry in enumerate(debate):
        if entry.get("event") != "seat_message":
            continue
        timeline.setdefault(entry.get("seat_id"), []).append((index, entry))
    for row in votes.get("votes", ()):
        if not isinstance(row, dict) or row.get("state") != "valid":
            continue
        problem = _first_round_problem(timeline.get(row.get("seat_id"), ()))
        if problem is not None:
            raise RunVerificationError(
                "{} 的有效票不符合第一輪生命週期：{}".format(row.get("seat_id"), problem)
            )


def _first_round_problem(messages):
    """Name why this seat's public record could not have produced a valid vote."""
    position = _first_seat_message(messages, "position", (0,))
    challenge = _first_seat_message(messages, "challenge", (1,))
    response = _first_seat_message(messages, "response", (1,))
    vote = _first_seat_message(messages, "final_vote", (1, 2, 3))
    for label, found in (
        ("缺少 round 0 開場", position),
        ("缺少 round 1 挑戰", challenge),
        ("缺少 round 1 回應", response),
        ("缺少 round 1-3 的正式投票", vote),
    ):
        if found is None:
            return label
    # elapsed_ms 的型別與範圍由 ``_verify_message_timestamps`` 統一把關（它掃
    # 的是全部公開訊息，不只有效票那幾席），這裡只比先後。
    prerequisites = (position, challenge, response)
    if challenge[0] < position[0] or response[0] < position[0]:
        return "第一輪訊息排在該席開場之前"
    # 紀錄順序與時間戳要一起驗：只比 final vote 的話，把 challenge 的
    # elapsed_ms 改到開場之前、紀錄順序原封不動，就沒有任何檢查會出聲。
    # 範圍刻意限縮在同一席自己的四則訊息——跨席的全域單調性不在這裡主張。
    if any(entry["elapsed_ms"] < position[1]["elapsed_ms"] for _, entry in (challenge, response)):
        return "第一輪訊息的 elapsed_ms 早於該席開場"
    if any(vote[0] < index for index, _ in prerequisites):
        return "正式投票排在它所依據的第一輪之前"
    if any(
        vote[1]["elapsed_ms"] < entry["elapsed_ms"] for _, entry in prerequisites
    ):
        return "正式投票的 elapsed_ms 早於它所依據的第一輪"
    return None


def _first_seat_message(messages, kind, rounds):
    """Return the earliest ``(index, entry)`` of one kind in an allowed round.

    回合用精確型別比對：Python 的 ``True == 1``，所以 ``round: true`` 光靠
    ``in (1,)`` 會被當成第一輪放行。
    """
    return next(
        (
            item
            for item in messages
            if item[1].get("kind") == kind
            and type(item[1].get("round")) is int
            and item[1]["round"] in rounds
        ),
        None,
    )


def _verify_stop_semantics(votes, stop_reason, stop_ms, deadlines, rules):
    """Check one stop record against the rule set it claims to satisfy.

    ``rules`` 沒有預設值，理由與 :func:`_verify_competition_timeline` 相同：門
    檻、牆與時點全都是規則的函數，省略時現讀等於換一套規則來判。規則未知時**由
    呼叫端整個跳過這個檢查**，不是在這裡拿預設值頂替；真的傳 ``None`` 進來會當
    場炸開，不會靜靜換一套規則。
    """
    if isinstance(rules, DebateRules):
        return _verify_v2_stop_semantics(
            votes, stop_reason, stop_ms, deadlines, rules
        )

    initial, reduced, forced = (
        rules.initial_votes,
        rules.reduced_votes,
        rules.forced_stop_votes,
    )
    tally = votes["tally"]
    leader_count = max(tally.values())
    adopted = votes.get("adopted_stance")
    adopted_count = tally.get(adopted, 0)
    threshold = votes.get("threshold_required")
    status = votes.get("consensus_status")
    valid_count = votes.get("valid_vote_count")
    # ``challenge_completed`` 不在這裡驗：它的唯一權威是
    # ``_verify_challenge_completed``——從公開紀錄重算並要求精確相等。在這裡
    # 再寫一次「必須是 False／必須是布林」只會讓同一條規則有兩個來源，而且第
    # 二個來源比第一個弱。
    if stop_reason == UNANIMOUS_BLIND_PASS:
        # 直過的門檻與時點跟狀態機讀同一份設定：票數是
        # vote_thresholds.unanimous_blind_pass，時點是「該 run 自己的第一輪牆」
        # （封存時刻 ＋ round_one_window），比較題晚封存，牆跟著平移。
        blind_pass = rules.unanimous_blind_pass_votes
        wall = rules.challenge_deadline_ms(deadlines.seal_ms)
        valid = (
            deadlines.seal_ms <= stop_ms <= wall
            and threshold == blind_pass
            # 採納立場自己就要有那麼多票。不另外檢查 adopted 非空：tally 的鍵
            # 是立場字串，adopted 為 null 時 adopted_count 就是 0，而門檻恆
            # ≥1（debate_rules 的票數校驗守住下界），所以 null 一定過不了。
            and adopted_count >= blind_pass
            # 直過採納的是七席各自的開場，所以七席全部是有效票，一席不少。
            and valid_count == len(SEAT_IDS)
            and status == "consensus"
        )
        if not valid:
            raise RunVerificationError("盲投直過的停止語意與票數不一致。")
        return
    if stop_reason == "consensus_{}_votes".format(initial):
        valid = (
            stop_ms < rules.reduced_threshold_from_ms
            and threshold == initial
            and adopted_count >= initial
        )
    elif stop_reason == "consensus_{}_votes".format(reduced):
        valid = (
            rules.reduced_threshold_from_ms <= stop_ms < rules.force_stop_ms
            and threshold == reduced
            and adopted_count >= reduced
        )
    elif stop_reason == "forced_stop_{}_votes".format(forced):
        valid = (
            stop_ms == rules.force_stop_ms
            and threshold == forced
            and adopted_count >= forced
        )
    elif stop_reason == "forced_stop_no_consensus":
        valid = (
            stop_ms == rules.force_stop_ms
            and threshold == forced
            and valid_count >= forced
            and leader_count < forced
            and adopted is None
            and status == "no_consensus"
        )
        if not valid:
            raise RunVerificationError("T+10 無共識停止語意與票數不一致。")
        return
    else:
        valid = (
            stop_ms == rules.force_stop_ms
            and threshold == forced
            and valid_count < forced
            and adopted is None
            and status == "failed_insufficient_valid_votes"
        )
        if not valid:
            raise RunVerificationError("T+10 有效票不足停止語意不一致。")
        return
    if not (valid and status == "consensus"):
        raise RunVerificationError("辯論門檻、停止時間或採用立場與票數不一致。")


def _verify_v2_stop_semantics(votes, stop_reason, stop_ms, deadlines, rules):
    """Check a schema-v2 stop against its discrete round or final-settle wall."""
    tally = votes.get("tally")
    adopted = votes.get("adopted_stance")
    threshold = votes.get("threshold_required")
    status = votes.get("consensus_status")
    valid_count = votes.get("valid_vote_count")
    if (
        not isinstance(tally, dict)
        or not tally
        or any(type(count) is not int or count < 0 for count in tally.values())
        or type(stop_ms) is not int
        or type(threshold) is not int
        or type(valid_count) is not int
        or valid_count < 0
    ):
        raise RunVerificationError("v2 停止紀錄的票數與時間欄位必須是非負整數。")
    leader_count = max(tally.values())
    adopted_count = tally.get(adopted, 0)
    first_round = rules.vote_rounds[0]

    if stop_reason == UNANIMOUS_BLIND_PASS:
        wall = deadlines.seal_ms + first_round.open_offset_ms
        valid = (
            deadlines.seal_ms <= stop_ms <= wall
            and threshold == len(SEAT_IDS)
            and adopted_count == len(SEAT_IDS)
            and sum(tally.values()) == len(SEAT_IDS)
            and valid_count == len(SEAT_IDS)
            and status == "consensus"
        )
        if not valid:
            raise RunVerificationError("盲投直過的停止語意與票數不一致。")
        return

    vote_round = next(
        (
            item
            for item in rules.vote_rounds
            if stop_reason == "consensus_{}_votes".format(item.threshold)
        ),
        None,
    )
    if vote_round is not None:
        valid = (
            stop_ms == deadlines.seal_ms + vote_round.open_offset_ms
            and threshold == vote_round.threshold
            and adopted_count >= vote_round.threshold
            and status == "consensus"
        )
        if not valid:
            raise RunVerificationError(
                "辯論門檻、停止時間或採用立場與票數不一致。"
            )
        return

    forced = rules.vote_rounds[-1].threshold
    settle_ms = deadlines.seal_ms + rules.final_settle_offset_ms
    if stop_reason == "forced_stop_{}_votes".format(forced):
        valid = (
            stop_ms == settle_ms
            and threshold == forced
            and adopted_count >= forced
            and status == "consensus"
        )
    elif stop_reason == "forced_stop_no_consensus":
        valid = (
            stop_ms == settle_ms
            and threshold == forced
            and valid_count >= forced
            and leader_count < forced
            and adopted is None
            and status == "no_consensus"
        )
    elif stop_reason == "forced_stop_insufficient_valid_votes":
        valid = (
            stop_ms == settle_ms
            and threshold == forced
            and valid_count < forced
            and adopted is None
            and status == "failed_insufficient_valid_votes"
        )
    else:
        raise RunVerificationError("未知辯論停止原因。")
    if not valid:
        raise RunVerificationError("最終結算的停止語意與票數不一致。")


def _verify_report_lineage(
    run_dir, evidence, debate, votes, presentation_version, rules
):
    """Check report.json, report.md and report.html trace back to the artifacts.

    ``rules`` 沒有預設值，理由同上。``None``＝該 run 沒有記錄規則，此時往下傳
    :data:`~.report_contract.RULES_NOT_RECORDED`，報告契約就會**只**略過「燈號
    不得高於上限」那一項——證據回查、票數交叉比對、渲染一致性以及燈號自己的級
    別與圖示全部照驗。略過的那一項列在 summary 的
    ``rule_checks_without_run_rules`` 裡。
    """
    report_path = run_dir / "report.json"
    _require_regular_file(report_path, run_dir)
    report = _read_json(report_path)
    sources = {
        "evidence": evidence,
        "debate": [entry for entry in debate if entry.get("seat_id")],
        "votes": votes,
    }
    try:
        validate_market_report(
            report,
            sources,
            rules=rules.confidence if rules is not None else RULES_NOT_RECORDED,
        )
    except ReportContractError as exc:
        raise RunVerificationError("report.json 無法回查正式 artifacts：{}".format(exc)) from exc
    expected_markdown = render_market_markdown(report).encode("utf-8")
    expected_html = render_market_html(
        report, sources if presentation_version == PRESENTATION_VERSION else None
    ).encode("utf-8")
    if (run_dir / "report.md").read_bytes() != expected_markdown:
        raise RunVerificationError("report.md 不是由正式 report.json 產生。")
    if (run_dir / "report.html").read_bytes() != expected_html:
        raise RunVerificationError("report.html 不是由正式 report.json 產生。")
    if presentation_version == PRESENTATION_VERSION:
        expected_debate_html = render_debate_html(report, sources).encode("utf-8")
        if (run_dir / "debate.html").read_bytes() != expected_debate_html:
            raise RunVerificationError("debate.html 不是由正式公開辯論 artifacts 產生。")


def _verify_real_provider_lineage(data_root, manifest):
    try:
        roster = load_frozen_roster()
    except ValueError as exc:
        raise RunVerificationError("frozen roster 無法驗證。") from exc
    seats = manifest["seats"]
    expected_by_id = {seat["seat_id"]: seat for seat in roster["seats"]}
    for seat in seats:
        expected = expected_by_id[seat["seat_id"]]
        if (
            seat.get("provider") != expected["provider"]
            or seat.get("target_model") != expected["target_model"]
            or not _actual_model_matches(expected, seat.get("actual_model"))
        ):
            raise RunVerificationError(
                "real run 席位 {} 的 provider/target/actual model 不符。".format(
                    seat["seat_id"]
                )
            )

    lineage = manifest.get("provider_preflight_lineage")
    required = {"system_preflight_id", "manifest_path", "sha256"}
    if not isinstance(lineage, dict) or set(lineage) != required:
        raise RunVerificationError("real run 缺少可稽核 provider preflight lineage。")
    preflight_id = lineage["system_preflight_id"]
    if (
        not isinstance(preflight_id, str)
        or not preflight_id
        or preflight_id in (".", "..")
        or Path(preflight_id).name != preflight_id
    ):
        raise RunVerificationError("provider preflight ID 不安全。")
    expected_relative = "preflight/{}/manifest.json".format(preflight_id)
    if lineage.get("manifest_path") != expected_relative:
        raise RunVerificationError("provider preflight manifest path 不一致。")
    path = data_root / expected_relative
    _require_regular_file(path, data_root)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != lineage.get("sha256"):
        raise RunVerificationError("provider preflight manifest hash 不一致。")
    preflight = _read_json(path)
    if preflight.get("mode") != "real":
        raise RunVerificationError("provider preflight 未證明真實七席能力。")
    if preflight.get("provider_capabilities_ready") is not True:
        raise RunVerificationError("provider preflight 未證明真實七席能力。")
    checks = preflight.get("checks")
    if (
        not isinstance(checks, list)
        or [item.get("check_id") for item in checks if isinstance(item, dict)]
        != list(REQUIRED_CHECK_IDS)
    ):
        raise RunVerificationError("provider preflight checks 不完整或順序不符。")
    by_check = {item["check_id"]: item for item in checks}
    failed = [
        check_id
        for check_id in REQUIRED_CHECK_IDS
        if by_check[check_id].get("ok") is not True
    ]
    expected_advisories = [
        check_id for check_id in failed if check_id in NON_BLOCKING_CHECK_IDS
    ]
    expected_blockers = [
        check_id for check_id in failed if check_id not in NON_BLOCKING_CHECK_IDS
    ]
    if preflight.get("blockers") != expected_blockers:
        raise RunVerificationError("provider preflight blockers 與 checks 不一致。")
    if preflight.get("advisories") != expected_advisories:
        raise RunVerificationError("provider preflight advisories 與 checks 不一致。")
    expected_ready = not expected_blockers
    if (
        preflight.get("ready") is not expected_ready
        or preflight.get("status") != ("READY" if expected_ready else "NOT_READY")
    ):
        raise RunVerificationError("provider preflight READY 狀態與 blockers 不一致。")
    failed_provider_checks = set(expected_blockers) - RUN_SCOPED_CHECK_IDS
    if failed_provider_checks:
        raise RunVerificationError("provider preflight 仍有 capability blocker。")
    preflight_generated = _parse_utc(preflight.get("generated_at_utc"), "provider preflight time")
    run_started = _parse_utc(manifest.get("started_at_utc"), "run start time")
    if preflight_generated > run_started:
        raise RunVerificationError("provider preflight 不得晚於 competition run。")
    matrix_by_seat = _verify_provider_matrix(preflight.get("provider_matrix"), roster)
    for seat in seats:
        if matrix_by_seat[seat["seat_id"]].get("actual_model") != seat.get("actual_model"):
            raise RunVerificationError(
                "real run 席位 {} actual model 與 preflight 不一致。".format(
                    seat["seat_id"]
                )
            )
    authorization = _verify_competition_authorization(
        data_root,
        manifest,
        preflight_id,
        preflight,
        preflight_generated,
        run_started,
    )
    authorization["preflight_advisories"] = expected_advisories
    return authorization


def _verify_competition_authorization(
    data_root,
    run_manifest,
    preflight_id,
    preflight,
    preflight_generated,
    run_started,
):
    lineage = preflight.get("competition_authorization")
    required = {
        "status",
        "authorized_run_id",
        "competition_challenge_sha256",
        "path",
        "sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != required:
        raise RunVerificationError("provider preflight 缺少 competition authorization lineage。")
    run_id = run_manifest["run_id"]
    expected_path = "preflight/{}/competition-authorization.json".format(preflight_id)
    if (
        lineage.get("status") != "AUTHORIZED"
        or lineage.get("authorized_run_id") != run_id
        or lineage.get("path") != expected_path
    ):
        raise RunVerificationError("competition authorization 未綁定本次 run。")
    path = data_root / expected_path
    _require_regular_file(path, data_root)
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != lineage.get("sha256"):
        raise RunVerificationError("competition authorization hash 不一致。")
    authorization = _read_json(path)
    expected_fields = {
        "schema_version",
        "status",
        "system_preflight_id",
        "authorized_run_id",
        "competition_challenge",
        "issued_at_utc",
    }
    if not isinstance(authorization, dict) or set(authorization) != expected_fields:
        raise RunVerificationError("competition authorization contract 不完整。")
    challenge = authorization.get("competition_challenge")
    if (
        authorization.get("schema_version") != "1.0.0"
        or authorization.get("status") != "AUTHORIZED"
        or authorization.get("system_preflight_id") != preflight_id
        or authorization.get("authorized_run_id") != run_id
        or not _valid_challenge(challenge)
        or hashlib.sha256(challenge.encode("utf-8")).hexdigest()
        != lineage.get("competition_challenge_sha256")
    ):
        raise RunVerificationError("competition authorization identity/challenge 不符。")
    issued = _parse_utc(authorization.get("issued_at_utc"), "authorization issued time")
    if issued != preflight_generated or issued > run_started:
        raise RunVerificationError("competition authorization 時間與 preflight/run 不一致。")

    matching_authorizations = 0
    preflight_root = data_root / "preflight"
    for candidate in preflight_root.glob("*/competition-authorization.json"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate_value = _read_json(candidate)
        except RunVerificationError:
            continue
        if candidate_value.get("authorized_run_id") == run_id:
            matching_authorizations += 1
    if matching_authorizations != 1:
        raise RunVerificationError("competition run_id 必須只有一份 preflight authorization。")
    return {
        "system_preflight_id": preflight_id,
        "run_id": run_id,
        "competition_challenge": challenge,
    }


def _verify_provider_matrix(matrix, roster):
    if not isinstance(matrix, list) or len(matrix) != 8:
        raise RunVerificationError("provider preflight matrix 必須包含 Core 加七席。")
    by_seat = {}
    for row in matrix:
        if not isinstance(row, dict) or not isinstance(row.get("seat_id"), str):
            raise RunVerificationError("provider matrix row 無效。")
        if row["seat_id"] in by_seat:
            raise RunVerificationError("provider matrix 席位重複。")
        by_seat[row["seat_id"]] = row
    core = by_seat.pop("core", None)
    if (
        core is None
        or core.get("provider") != "codex"
        or core.get("target_model") != "gpt-5.6-sol"
        or core.get("actual_model") != "gpt-5.6-sol"
        or core.get("model_confirmation_source", "runtime")
        not in ("runtime", "operator_ui")
    ):
        raise RunVerificationError("provider matrix Core model 不符。")
    expected_seats = {seat["seat_id"]: seat for seat in roster["seats"]}
    if set(by_seat) != set(expected_seats):
        raise RunVerificationError("provider matrix 未完整保留固定七席。")
    for seat_id, expected in expected_seats.items():
        row = by_seat[seat_id]
        if (
            row.get("provider") != expected["provider"]
            or row.get("target_model") != expected["target_model"]
            or not _actual_model_matches(expected, row.get("actual_model"))
        ):
            raise RunVerificationError("provider matrix {} model 不符。".format(seat_id))
    return by_seat


def _verify_real_run_receipts(
    run_dir,
    manifest,
    artifact_index,
    timeline,
    evidence,
    debate,
    votes,
    authorization,
):
    lineage = manifest.get("provider_receipts")
    if (
        not isinstance(lineage, list)
        or len(lineage) != len(SEAT_IDS)
        or [item.get("seat_id") for item in lineage if isinstance(item, dict)]
        != list(SEAT_IDS)
    ):
        raise RunVerificationError("real run 必須提供固定七席 provider receipt lineage。")
    seats = {seat["seat_id"]: seat for seat in manifest["seats"]}
    started = _parse_utc(manifest.get("started_at_utc"), "run start time")
    unique_ids = {
        name: set()
        for name in ("attempt", "receipt", "dispatch", "completion", "search")
    }
    unique_paths = set()
    payloads = []

    for item in lineage:
        if set(item) != {"seat_id", "path", "sha256"}:
            raise RunVerificationError("provider receipt lineage 欄位不符。")
        seat_id = item["seat_id"]
        expected_receipt_path = "provider-receipts/{}.json".format(seat_id)
        if item.get("path") != expected_receipt_path:
            raise RunVerificationError("{} provider receipt path 不一致。".format(seat_id))
        receipt_path = run_dir / expected_receipt_path
        _verify_indexed_artifact(
            receipt_path,
            run_dir,
            artifact_index,
            expected_receipt_path,
            item.get("sha256"),
        )
        receipt = _read_json(receipt_path)
        if set(receipt) != _REAL_RECEIPT_FIELDS or receipt.get("schema_version") != "1.0.0":
            raise RunVerificationError("{} provider receipt contract 不完整。".format(seat_id))
        seat = seats[seat_id]
        attempt_id = seat.get("attempt_id")
        if not _safe_segment(attempt_id):
            raise RunVerificationError("{} real run 缺少安全 attempt_id。".format(seat_id))
        _add_unique(unique_ids["attempt"], attempt_id, "real attempt_id")
        adopted_evidence = _verify_adopted_attempt_lineage(
            seat_id,
            attempt_id,
            evidence,
            debate,
            votes,
        )
        expected_values = {
            "system_preflight_id": authorization["system_preflight_id"],
            "run_id": authorization["run_id"],
            "competition_challenge": authorization["competition_challenge"],
            "seat_id": seat_id,
            "attempt_id": attempt_id,
            "provider": seat["provider"],
            "target_model": seat["target_model"],
            "actual_model": seat["actual_model"],
        }
        if any(receipt.get(field) != value for field, value in expected_values.items()):
            raise RunVerificationError("{} provider receipt 未綁定 run/seat/model/challenge。".format(seat_id))
        _add_unique(unique_ids["receipt"], receipt.get("receipt_id"), "provider receipt_id")
        _verify_phase_receipt(
            receipt.get("dispatch"),
            unique_ids["dispatch"],
            started,
            expected_elapsed=0,
            label="{} dispatch".format(seat_id),
        )
        completion_elapsed = timeline["seat_completion_ms"][seat_id]
        _verify_phase_receipt(
            receipt.get("completion"),
            unique_ids["completion"],
            started,
            expected_elapsed=completion_elapsed,
            label="{} completion".format(seat_id),
        )

        attempt_root = "agents/{}/attempts/{}/".format(seat_id, attempt_id)
        search = _verify_search_receipt(
            run_dir,
            artifact_index,
            receipt,
            expected_values,
            started,
            completion_elapsed,
            unique_ids["search"],
            attempt_root,
            unique_paths,
        )
        raw_text = _verify_receipt_payload(
            run_dir,
            artifact_index,
            receipt,
            "raw_transcript_path",
            "raw_transcript_sha256",
            attempt_root,
            unique_paths,
        )
        output_text = _verify_receipt_payload(
            run_dir,
            artifact_index,
            receipt,
            "output_path",
            "output_sha256",
            attempt_root,
            unique_paths,
        )
        _verify_structured_output(output_text, adopted_evidence, seat_id, attempt_id)
        payloads.extend((receipt, search, raw_text, output_text))
    return payloads


def _verify_adopted_attempt_lineage(seat_id, attempt_id, evidence, debate, votes):
    adopted_evidence = [
        record
        for record in evidence
        if record.get("seat_id") == seat_id and record.get("attempt_id") == attempt_id
    ]
    evidence_attempts = {
        record.get("attempt_id")
        for record in evidence
        if record.get("seat_id") == seat_id
    }
    if not adopted_evidence or evidence_attempts != {attempt_id}:
        raise RunVerificationError(
            "{} receipt attempt_id 不等於 adopted evidence attempt lineage。".format(
                seat_id
            )
        )
    debate_attempts = {
        record.get("attempt_id")
        for record in debate
        if record.get("event") == "seat_message" and record.get("seat_id") == seat_id
    }
    if attempt_id not in debate_attempts:
        raise RunVerificationError(
            "{} receipt attempt_id 不存在於正式 debate messages。".format(seat_id)
        )
    vote_records = [
        record
        for record in votes.get("votes", [])
        if isinstance(record, dict) and record.get("seat_id") == seat_id
    ]
    if (
        len(vote_records) != 1
        or not isinstance(vote_records[0].get("attempt_ids"), list)
        or attempt_id not in vote_records[0]["attempt_ids"]
    ):
        raise RunVerificationError(
            "{} receipt attempt_id 不存在於正式 votes attempt_ids。".format(seat_id)
        )
    return adopted_evidence


def _verify_structured_output(output_text, adopted_evidence, seat_id, attempt_id):
    try:
        output = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RunVerificationError("{} structured output 不是 JSON。".format(seat_id)) from exc
    if not isinstance(output, list) or any(not isinstance(record, dict) for record in output):
        raise RunVerificationError("{} structured output 必須為 evidence records。".format(seat_id))
    if _canonical_records(output) != _canonical_records(adopted_evidence):
        raise RunVerificationError(
            "{} structured output 與 adopted evidence attempt {} 不一致。".format(
                seat_id,
                attempt_id,
            )
        )


def _canonical_records(records):
    normalized = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    return tuple(sorted(normalized))


def _verify_phase_receipt(value, unique_ids, started, expected_elapsed, label):
    if not isinstance(value, dict) or set(value) != _PHASE_RECEIPT_FIELDS:
        raise RunVerificationError("{} receipt contract 不完整。".format(label))
    _add_unique(unique_ids, value.get("receipt_id"), "{} receipt_id".format(label))
    elapsed = value.get("elapsed_ms")
    if type(elapsed) is not int or elapsed != expected_elapsed:
        raise RunVerificationError("{} elapsed 與正式 timeline 不一致。".format(label))
    observed = _parse_utc(value.get("at_utc"), "{} time".format(label))
    if observed != started + timedelta(milliseconds=elapsed):
        raise RunVerificationError("{} timestamp 與 monotonic elapsed 不一致。".format(label))


def _verify_search_receipt(
    run_dir,
    artifact_index,
    receipt,
    expected_values,
    started,
    completion_elapsed,
    unique_ids,
    attempt_root,
    unique_paths,
):
    path_value = receipt.get("search_receipt_path")
    _require_attempt_artifact_path(path_value, attempt_root, "search receipt")
    if path_value in unique_paths:
        raise RunVerificationError("real receipt payload path 重複。")
    unique_paths.add(path_value)
    path = run_dir / path_value
    _verify_indexed_artifact(
        path,
        run_dir,
        artifact_index,
        path_value,
        receipt.get("search_receipt_sha256"),
    )
    search = _read_json(path)
    if set(search) != _SEARCH_RECEIPT_FIELDS or search.get("schema_version") != "1.0.0":
        raise RunVerificationError("search receipt contract 不完整。")
    for field in (
        "run_id",
        "seat_id",
        "attempt_id",
        "provider",
        "competition_challenge",
    ):
        if search.get(field) != expected_values[field]:
            raise RunVerificationError("search receipt 未綁定 {}。".format(field))
    _add_unique(unique_ids, search.get("receipt_id"), "search receipt_id")
    elapsed = search.get("elapsed_ms")
    if (
        search.get("succeeded") is not True
        or search.get("tool") != _SEARCH_TOOLS[expected_values["provider"]]
        or type(elapsed) is not int
        or not 0 <= elapsed <= completion_elapsed
    ):
        raise RunVerificationError("search receipt 未證明該席在截止前完成搜尋。")
    observed = _parse_utc(search.get("completed_at_utc"), "search completion time")
    if observed != started + timedelta(milliseconds=elapsed):
        raise RunVerificationError("search receipt timestamp 與 elapsed 不一致。")
    return search


def _verify_receipt_payload(
    run_dir,
    artifact_index,
    receipt,
    path_field,
    hash_field,
    attempt_root,
    unique_paths,
):
    path_value = receipt.get(path_field)
    _require_attempt_artifact_path(path_value, attempt_root, path_field)
    if path_value in unique_paths:
        raise RunVerificationError("real receipt payload path 重複。")
    unique_paths.add(path_value)
    path = run_dir / path_value
    _verify_indexed_artifact(
        path,
        run_dir,
        artifact_index,
        path_value,
        receipt.get(hash_field),
    )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise RunVerificationError("real receipt payload 必須為 UTF-8 公開內容。") from exc
    if not text.strip():
        raise RunVerificationError("real receipt payload 不得為空。")
    return text


def _require_attempt_artifact_path(path_value, attempt_root, label):
    if not isinstance(path_value, str) or "\\" in path_value:
        raise RunVerificationError("{} 不在該席 attempt 目錄。".format(label))
    path = PurePosixPath(path_value)
    root = PurePosixPath(attempt_root)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) <= len(root.parts)
        or path.parts[: len(root.parts)] != root.parts
    ):
        raise RunVerificationError("{} 不在該席 attempt 目錄。".format(label))


def _verify_indexed_artifact(path, run_dir, artifact_index, relative, expected_sha):
    _require_regular_file(path, run_dir)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    indexed = artifact_index.get(relative)
    if (
        not isinstance(indexed, dict)
        or indexed.get("path") != relative
        or indexed.get("sha256") != digest
        or expected_sha != digest
    ):
        raise RunVerificationError("run receipt artifact hash/index 不一致：{}".format(relative))


def _add_unique(values, value, label):
    if not isinstance(value, str) or not value or value in values:
        raise RunVerificationError("{} 缺少或重複。".format(label))
    values.add(value)


def _reject_shipped_fake_markers(*values):
    if any(_contains_fake_marker(value) for value in values):
        raise RunVerificationError("real competition bundle 含 shipped fake/fixture marker。")


def _contains_fake_marker(value):
    if isinstance(value, dict):
        return any(
            _contains_fake_marker(key) or _contains_fake_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_fake_marker(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in _SHIPPED_FAKE_MARKERS)
    return False


def _valid_challenge(value):
    return (
        isinstance(value, str)
        and 24 <= len(value) <= 128
        and all(
            character.isascii() and (character.isalnum() or character in "-_")
            for character in value
        )
    )


def _safe_segment(value):
    return (
        isinstance(value, str)
        and bool(value)
        and value not in (".", "..")
        and Path(value).name == value
        and "/" not in value
        and "\\" not in value
    )


def _actual_model_matches(expected, actual_model):
    if not isinstance(actual_model, str):
        return False
    if expected["provider"] == "claude":
        lowered = actual_model.lower()
        return lowered == "opus" or lowered.startswith("claude-opus-")
    return actual_model == expected["target_model"]


def _parse_utc(value, label):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RunVerificationError("{} 必須為 UTC ISO-8601。".format(label))
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RunVerificationError("{} 必須為有效 UTC ISO-8601。".format(label)) from exc


def _require_regular_file(path, run_dir):
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(run_dir.resolve()):
        raise RunVerificationError("缺少或不安全的 artifact：{}".format(path.name))


def _read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunVerificationError("無法解析 {}。".format(path.name)) from exc
    if not isinstance(value, dict):
        raise RunVerificationError("{} 必須為 JSON object。".format(path.name))
    return value


def _read_jsonl(path):
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise RunVerificationError("無法解析 {}。".format(path.name)) from exc
    if any(not isinstance(record, dict) for record in records):
        raise RunVerificationError("{} 必須是一行一個 JSON object。".format(path.name))
    return records
