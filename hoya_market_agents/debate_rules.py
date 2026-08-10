"""The single authority for debate deadlines, vote thresholds and lights.

`config/debate_rules.json` is the only place these numbers exist. Every caller
— the state machine, the driver, the verifier, the dashboard and their tests —
asks :func:`debate_rules` instead of copying a literal, exactly the way
:func:`research_scheduler.research_deadlines` owns the research instants.

Loading is fail-closed: a missing field, a timeline that does not increase or
an impossible vote count refuses to start and names the offending field, so a
bad edit is caught at load time rather than halfway through a live run.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .seats import CODE_ROOT, SEAT_IDS

RULES_PATH = CODE_ROOT / "config" / "debate_rules.json"
SUPPORTED_SCHEMA_VERSION = 1

# 2026-08-02 使用者核准的修訂時間表（設定檔的預設值就是它）。研究讓出一分鐘
# 後，辯論從 T+4:00 起跑，每一道牆都往後挪，好讓兩次真實 CLI 呼叫（開場
# 50-60 秒、第一輪 30-60 秒）各自有完整的時間，而不是擠在同一段 90 秒裡互相
# 踩死。
#
# 起跑點是「該 run 實際封存的那一刻」，不是 debate_start：兩幣比較題的研究窗
# 到 T+4:30（research_scheduler.research_deadlines 是封存時刻的唯一權威）。
# debate_start 只是單幣預設，建構 DebateStateMachine 時可覆寫，且必須與
# RunStore 的 seal 完全一致。
#
# 第一輪的牆因此是相對的：封存時刻 ＋ round_one_window，所以封存晚 30 秒的比
# 較題第一輪也拿得到同樣長的視窗。這道牆不是「辯論何時結束」——湊滿門檻票數
# 的那一刻就結束了。它是慢席的截止線，而慢席投不投得到票，決定的是六票同向
# 能不能成立。實測 run 20260802T055930Z 的教訓：Claude 席開場落在封存＋40
# 秒，第一輪呼叫要讀完整證據快照與全場逐字稿、再花 50-70 秒；120 秒的窗只留
# 給它們 73 秒，三席同時掉隊，有效票剩四張，於是既湊不到六票（無法提早結
# 束），信心也被壓成紅燈。180 秒讓「開場＋挑戰＋relay」那條鏈完整放得下；七
# 席都投得到票時，共識反而在 T+6:30 前就成立，比原本乾等到 T+10 更早收工。
#
# 順序就是規則：時間軸必須嚴格遞增，違反的那一對欄位會被逐字指名。
_TIMELINE_INSTANTS = (
    "debate_start",
    "reduced_threshold_from",
    "final_round_start",
    "final_round_end",
    "force_stop",
)
_TIMELINE_FIELDS = _TIMELINE_INSTANTS + ("round_one_window",)
# 同樣是順序即規則：票數階梯必須嚴格遞減，否則 ``consensus_<n>_votes`` 這個停止
# 原因會有兩種讀法，run_verifier 也就無從判斷哪一道門檻真的生效。
_VOTE_LADDER_FIELDS = ("initial", "reduced", "forced_stop")
# 盲投直過（Ticket 03）的門檻不屬於那條階梯：階梯是「辯論打到第幾分鐘要幾票」，
# 這一個是「開場盲投當下要幾票才不必辯論」，兩者的計票對象不同（有效票 vs 開
# 場票），停止原因也是自己的字串 ``unanimous_blind_pass``，不會和
# ``consensus_<n>_votes`` 撞名。因此它只受 1..席位數 的票數校驗，不加入遞減檢
# 查——設成低於 initial 是合法設定（操作者要更寬鬆的直過就會這樣填），語意是
# 定義好的：開場票數達到它就直過，不是意外。
_VOTE_FIELDS = ("unanimous_blind_pass",) + _VOTE_LADDER_FIELDS
_CONFIDENCE_FIELDS = ("light_scale", "downgrades")
_TOP_LEVEL_FIELDS = (
    "schema_version",
    "timeline_ms",
    "vote_thresholds",
    "confidence",
)
_LIGHT_STEP_FIELDS = ("min_votes", "level")
# ADR 0003 的兩條降級，各自帶自己的欄位。新增第三條降級必然要寫評估它的程式，
# 所以這張表跟著程式走，不是純資料。
#
# ``exempt_seat_ids`` **只屬於 low_trust_source**。豁免的意思是「這一席貢獻的
# 證據卡不受這條規則判定」，只有逐卡判定的規則說得通：來源等級是逐卡的，輿情
# 席職責就是蒐集三手輿情訊號，對它套用來源等級降級屬於誤殺。
# ``few_independent_domains`` 數的是**集合基數**，把某席的卡片移出判定集合只會
# 讓網域集合變小、降級**更容易**觸發——一個叫「豁免」的旋鈕做出相反的事。與其
# 驗證它或替它改名，這裡讓那個狀態無法表達：欄位不存在，填了就是未知欄位。
_DOWNGRADE_FIELDS = {
    "few_independent_domains": ("min_independent_domains",),
    "low_trust_source": ("trusted_source_tiers", "exempt_seat_ids"),
}


class DebateRulesError(RuntimeError):
    """The rule file is missing, unreadable or illegal; nothing may start."""


@dataclass(frozen=True)
class LightStep:
    """One rung of the vote-count light ladder: "this many votes → this light"."""

    min_votes: int
    level: str


@dataclass(frozen=True)
class DowngradeRule:
    """One reason to move the light ``levels`` rungs toward the worse end.

    ``exempt_seat_ids`` 是不受這條規則牽連的席位（ADR 0003：輿情席職責就是蒐
    集三手輿情，對它套用來源等級降級屬於誤殺），只有逐卡判定的規則設得起——
    見 :data:`_DOWNGRADE_FIELDS`。每條規則自己的參數各有專屬欄位，只有該規則
    會填——條件本身是程式碼，硬要做成通用資料只會生出一個沒人能評估的假抽象。
    """

    rule: str
    levels: int
    exempt_seat_ids: tuple = ()
    min_independent_domains: int = None
    trusted_source_tiers: tuple = None


@dataclass(frozen=True)
class ConfidenceRules:
    """The light scale and its downgrades (ADR 0003).

    ``light_scale`` 由好到壞排序（藍→紅），所以它同時就是「降一級」的順序表：
    降級＝往後移，末端夾住。兩者留空代表尚未設定，載入器接受；一旦有值，鍵名
    與型別就要通過驗證。

    消費端是 ``report_contract.confidence_cap``。級別字串的合法集合與順序由
    ``report_contract.confidence_scale`` 把關，不在這個載入器裡——理由見
    :func:`_light_scale`。
    """

    light_scale: tuple = ()
    downgrades: tuple = ()

    @property
    def configured(self):
        """True once the scale carries at least one rung."""
        return bool(self.light_scale)


@dataclass(frozen=True)
class DebateRules:
    """One immutable answer to "which wall and how many votes, at T+n?"."""

    debate_start_ms: int
    round_one_window_ms: int
    reduced_threshold_from_ms: int
    final_round_start_ms: int
    final_round_end_ms: int
    force_stop_ms: int
    unanimous_blind_pass_votes: int
    initial_votes: int
    reduced_votes: int
    forced_stop_votes: int
    confidence: ConfidenceRules

    def challenge_deadline_ms(self, debate_start_ms=None):
        """The first-round wall for a run that sealed at ``debate_start_ms``.

        牆是相對的：晚封存的題型（兩幣比較題）第一輪一樣拿得到整個窗。省略
        參數時用單幣預設封存時刻。
        """
        start = self.debate_start_ms if debate_start_ms is None else debate_start_ms
        return start + self.round_one_window_ms

    def required_votes_at(self, elapsed_ms):
        """The absolute vote threshold in force at an elapsed time."""
        if elapsed_ms >= self.force_stop_ms:
            return self.forced_stop_votes
        if elapsed_ms >= self.reduced_threshold_from_ms:
            return self.reduced_votes
        return self.initial_votes

    def phase_at(self, elapsed_ms, challenge_deadline_ms=None):
        """Name the deadline window an elapsed time falls in.

        第一輪牆是相對的，所以晚封存的題型可能把它推到
        ``reduced_threshold_from_ms`` 之後。重疊時**較晚的絕對牆優先命名**：
        ``five_vote_threshold`` 蓋過 ``first_round_closed``，後者在該設定下不
        出現。收件規則不受影響——``DebateStateMachine`` 判定第幾輪用的仍是該
        run 自己的第一輪牆，所以慢席不會因為門檻降低就被提早關門。
        """
        if challenge_deadline_ms is None:
            challenge_deadline_ms = self.challenge_deadline_ms()
        if elapsed_ms >= self.force_stop_ms:
            return "forced_stop"
        if elapsed_ms > self.final_round_end_ms:
            return "after_final_round"
        if elapsed_ms >= self.final_round_start_ms:
            return "final_round"
        if elapsed_ms >= self.reduced_threshold_from_ms:
            return "five_vote_threshold"
        if elapsed_ms >= challenge_deadline_ms:
            return "first_round_closed"
        return "first_round"


def load_debate_rules(path):
    """Read one rule file, validate it whole, and freeze it."""
    document = _read_document(Path(path))
    _reject_unknown_keys(document, _TOP_LEVEL_FIELDS, "辯論規則設定檔最外層")
    _require_schema_version(document)
    timeline = _require_section(document, "timeline_ms", _TIMELINE_FIELDS)
    votes = _require_section(document, "vote_thresholds", _VOTE_FIELDS)
    confidence = _require_section(document, "confidence", _CONFIDENCE_FIELDS)

    for name in _TIMELINE_FIELDS:
        _require_non_negative_int(timeline[name], "timeline_ms.{}".format(name))
    _require_increasing(timeline, "timeline_ms", _TIMELINE_INSTANTS)
    _require_positive_window(timeline)

    for name in _VOTE_FIELDS:
        _require_vote_count(votes[name], "vote_thresholds.{}".format(name))
    _require_decreasing(votes, "vote_thresholds", _VOTE_LADDER_FIELDS)

    return DebateRules(
        debate_start_ms=timeline["debate_start"],
        round_one_window_ms=timeline["round_one_window"],
        reduced_threshold_from_ms=timeline["reduced_threshold_from"],
        final_round_start_ms=timeline["final_round_start"],
        final_round_end_ms=timeline["final_round_end"],
        force_stop_ms=timeline["force_stop"],
        unanimous_blind_pass_votes=votes["unanimous_blind_pass"],
        initial_votes=votes["initial"],
        reduced_votes=votes["reduced"],
        forced_stop_votes=votes["forced_stop"],
        confidence=_build_confidence(confidence),
    )


_CACHED_RULES = None


def debate_rules():
    """Return the frozen rules every caller must consult.

    這是全系統唯一的辯論規則權威：狀態機、driver、verifier、看板與測試一律
    查這裡，不得自己抄字面值。檔案在第一次查詢時載入並驗證；非法設定在這一
    刻就拒絕，不會讓 run 跑到一半才炸。設定改動後由 :func:`reload_debate_rules`
    換上新的一份。

    全域只讀一次：分兩次讀（``if`` 一次、``return`` 再一次）的話，中間若有人
    reload，同一次呼叫的判斷依據與回傳值會來自不同的兩份設定。回傳的永遠是一
    份完整、已通過驗證的規則。
    """
    rules = _CACHED_RULES
    if rules is None:
        return reload_debate_rules()
    return rules


def reload_debate_rules(path=None):
    """Validate a rule file and publish it as the new system-wide answer.

    設定頁存檔後，**讓新規則生效只要呼叫這一個**：``_CACHED_RULES`` 是私有的，
    本函式是它唯一的寫入點，消費端一律走 :func:`debate_rules`，而
    ``report_contract`` 的燈號階梯不另存快取（每次都從現行規則算），所以沒有第
    二個東西要清。省略 ``path`` 讀出貨設定 ``RULES_PATH``；給了 ``path`` 就發佈
    那一份（測試與候選檔案用）。

    **切換是原子的。** 新規則先在區域變數上完整載入並驗證，通過之後才做一次名
    稱重綁；``DebateRules`` 是 frozen dataclass，已發佈的物件不會被就地改寫。
    因此讀取者在任何時刻拿到的都是一份完整的規則——不是舊的那份就是新的那份，
    不可能是兩者的混合，也不會是 ``None``。不需要鎖：要保護的是「同時只有一個
    名字被重綁」，而那本來就是一次賦值。

    **原子的界線講清楚：保證發布的是完整快照，不保證「較晚開始的呼叫勝出」。**
    兩個並行 reload 是 last-completion-wins——一個讀檔較慢的 A 可以在 B 返回之後
    才覆寫 B，於是設定頁看到 B 成功、系統跑的卻是 A。讀取者在任何時刻仍然只會
    看到某一份完整規則，所以這不違反上面那條保證；但設定頁若允許並行存檔，就
    得自己把「寫檔＋reload」序列化，或改用版本號／CAS。

    **已知落差（Ticket 09／10／11 的硬前置，本票不處理）：** 進行中的 run 在
    ``run_after_seal`` 起點取一份快照並用到結束，而看板每次 build 都現讀全域。所
    以辯論途中 reload 之後，畫面上公開的門檻會是**新的**，而那場 run 實際遵守的
    是**舊的**——兩者各自都正確，合起來不一致。manifest 目前也沒有記錄該場 run
    用的規則版本，事後無從還原。要對齊需要：run 建立時保存規則 SHA／版本、看板
    與歷史 verifier 改用該 run 的快照、等待畫面才用目前的全域規則、設定頁的文案
    不能只寫「立即生效」。那是 run artifact 的資料格式變更，會動到 Ticket 07 的
    逐檔一致契約，所以不在這裡做。

    **失敗時 fail-closed。** 載入或驗證丟出**任何**例外時（設定檔的問題一律是
    :class:`DebateRulesError`——``_read_document`` 會把 OSError 與 JSON 錯誤都轉
    成它；連 ``KeyboardInterrupt`` 這種不繼承 ``Exception`` 的中斷也一樣），賦
    值那一行根本沒有執行到，既有規則原封不動地留著，例外照原樣往上丟——設定頁
    看得到錯誤訊息，系統繼續用舊規則跑。保護來自「切換排在載入之後」這個順序，
    不是靠 ``except``。

    驗證的範圍就是 :func:`load_debate_rules` 的範圍，不多也不少。燈號級別字串是
    否落在核准集合內**不在這一關**，由消費端 ``report_contract.confidence_scale``
    把關——理由見 :func:`_light_scale`。因此一份結構合法、卻把 ``level`` 寫成核
    准集合以外字串的檔案，會在這裡發佈成功、在第一次評燈時才被擋下來。
    """
    global _CACHED_RULES
    rules = load_debate_rules(RULES_PATH if path is None else path)
    _CACHED_RULES = rules
    return rules


# -- validation -------------------------------------------------------------


def _read_document(path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DebateRulesError(
            "無法讀取辯論規則設定檔 {}：{}".format(path, exc)
        ) from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise DebateRulesError(
            "辯論規則設定檔 {} 不是合法 JSON：{}".format(path, exc)
        ) from exc
    if not isinstance(document, dict):
        raise DebateRulesError(
            "辯論規則設定檔 {} 的最外層必須是 object。".format(path)
        )
    return document


def _require_schema_version(document):
    if "schema_version" not in document:
        raise DebateRulesError("辯論規則設定檔缺少必要欄位：schema_version")
    version = document["schema_version"]
    # 型別要先於值：Python 的 bool 是 int 的子型別、1.0 == 1，所以只比值的話
    # true 與 1.0 都會冒充成支援的版本。這個檔案裡每一處數值校驗都用
    # ``type(x) is int`` 這種精確比對，理由都是同一個。
    if type(version) is not int or version != SUPPORTED_SCHEMA_VERSION:
        raise DebateRulesError(
            "schema_version 必須為整數 {}，收到 {!r}。".format(
                SUPPORTED_SCHEMA_VERSION, version
            )
        )


def _require_section(document, section, fields):
    """Return one section, refusing missing, misshapen or unknown fields."""
    if section not in document:
        raise DebateRulesError("辯論規則設定檔缺少必要欄位：{}".format(section))
    value = document[section]
    if not isinstance(value, dict):
        raise DebateRulesError("{} 必須是 object。".format(section))
    for name in fields:
        if name not in value:
            raise DebateRulesError(
                "辯論規則設定檔缺少必要欄位：{}.{}".format(section, name)
            )
    _reject_unknown_keys(value, fields, section)
    return value


def _reject_unknown_keys(mapping, allowed, label):
    """Refuse keys nobody reads, at every level of the document.

    以底線開頭的鍵是 JSON 沒有的註解；其餘不認得的鍵一律拒絕，免得改錯鍵名
    的人以為自己已經改到了。最外層與各 section 套用同一條規則。
    """
    unknown = sorted(
        name
        for name in mapping
        if name not in allowed and not name.startswith("_")
    )
    if unknown:
        raise DebateRulesError(
            "{} 含未知欄位：{}".format(label, "、".join(unknown))
        )


def _require_non_negative_int(value, field):
    if type(value) is not int or value < 0:
        raise DebateRulesError(
            "{} 必須是非負整數毫秒，收到 {!r}。".format(field, value)
        )


def _require_vote_count(value, field):
    if type(value) is not int or not 1 <= value <= len(SEAT_IDS):
        raise DebateRulesError(
            "{} 必須是 1 到 {} 之間的整數票數，收到 {!r}。".format(
                field, len(SEAT_IDS), value
            )
        )


def _require_increasing(values, section, fields):
    for earlier, later in zip(fields, fields[1:]):
        if values[later] <= values[earlier]:
            raise DebateRulesError(
                "{section}.{later}（{later_value}）必須大於 {section}.{earlier}"
                "（{earlier_value}）；時間軸必須嚴格遞增。".format(
                    section=section,
                    later=later,
                    later_value=values[later],
                    earlier=earlier,
                    earlier_value=values[earlier],
                )
            )


def _require_decreasing(values, section, fields):
    for earlier, later in zip(fields, fields[1:]):
        if values[later] >= values[earlier]:
            raise DebateRulesError(
                "{section}.{later}（{later_value}）必須小於 {section}.{earlier}"
                "（{earlier_value}）；票數階梯必須嚴格遞減。".format(
                    section=section,
                    later=later,
                    later_value=values[later],
                    earlier=earlier,
                    earlier_value=values[earlier],
                )
            )


def _build_confidence(section):
    """Validate the light block and freeze it; empty means "not filled yet"."""
    scale = section["light_scale"]
    downgrades = section["downgrades"]
    if not isinstance(scale, list):
        raise DebateRulesError("confidence.light_scale 必須是 array。")
    if not isinstance(downgrades, dict):
        raise DebateRulesError("confidence.downgrades 必須是 object。")
    return ConfidenceRules(
        light_scale=_light_scale(scale),
        downgrades=_downgrade_rules(downgrades),
    )


def _light_scale(entries):
    """Read the vote-count ladder, best rung first, ending at a total fallback."""
    steps = []
    previous = None
    seen_levels = set()
    for index, entry in enumerate(entries):
        field = "confidence.light_scale[{}]".format(index)
        if not isinstance(entry, dict):
            raise DebateRulesError("{} 必須是 object。".format(field))
        _reject_unknown_keys(entry, _LIGHT_STEP_FIELDS, field)
        for name in _LIGHT_STEP_FIELDS:
            if name not in entry:
                raise DebateRulesError(
                    "辯論規則設定檔缺少必要欄位：{}.{}".format(field, name)
                )
        min_votes = entry["min_votes"]
        if type(min_votes) is not int or not 0 <= min_votes <= len(SEAT_IDS):
            raise DebateRulesError(
                "{}.min_votes 必須是 0 到 {} 之間的整數，收到 {!r}。".format(
                    field, len(SEAT_IDS), min_votes
                )
            )
        if previous is not None and min_votes >= previous:
            raise DebateRulesError(
                "{}.min_votes（{}）必須小於前一級（{}）；燈號階梯必須由好到壞"
                "嚴格遞減。".format(field, min_votes, previous)
            )
        level = entry["level"]
        # 燈號字串不在這裡綁 enum：那份 enum 住在 report_contract，而它會把整條
        # 研究管線拉進這個載入器。合法集合與順序由消費端
        # ``report_contract.confidence_scale`` 把關（Ticket 04），所以這裡通過
        # 的字串不等於系統接受它——只代表結構合法。
        if not isinstance(level, str) or not level.strip():
            raise DebateRulesError(
                "{}.level 必須是非空字串，收到 {!r}。".format(field, level)
            )
        if level in seen_levels:
            raise DebateRulesError("{}.level 重複：{}".format(field, level))
        seen_levels.add(level)
        previous = min_votes
        steps.append(LightStep(min_votes=min_votes, level=level))
    if steps and steps[-1].min_votes != 0:
        raise DebateRulesError(
            "confidence.light_scale 最後一級的 min_votes 必須是 0；否則票數低於 "
            "{} 時沒有燈號可用。".format(steps[-1].min_votes)
        )
    return tuple(steps)


def _downgrade_rules(section):
    """Read the named downgrade rules, each with its own validated fields."""
    _reject_unknown_keys(section, tuple(_DOWNGRADE_FIELDS), "confidence.downgrades")
    rules = []
    for name, parameters in _DOWNGRADE_FIELDS.items():
        if name not in section:
            continue
        field = "confidence.downgrades.{}".format(name)
        entry = section[name]
        if not isinstance(entry, dict):
            raise DebateRulesError("{} 必須是 object。".format(field))
        allowed = ("levels",) + parameters
        _reject_unknown_keys(entry, allowed, field)
        for key in allowed:
            if key not in entry:
                raise DebateRulesError(
                    "辯論規則設定檔缺少必要欄位：{}.{}".format(field, key)
                )
        levels = entry["levels"]
        if type(levels) is not int or not 1 <= levels <= len(SEAT_IDS):
            raise DebateRulesError(
                "{}.levels 必須是 1 到 {} 之間的整數，收到 {!r}。".format(
                    field, len(SEAT_IDS), levels
                )
            )
        rules.append(
            DowngradeRule(
                rule=name,
                levels=levels,
                **{
                    key: _DOWNGRADE_VALIDATORS[key](entry[key], field)
                    for key in parameters
                }
            )
        )
    return tuple(rules)


def _exempt_seat_ids(value, field):
    if not isinstance(value, list):
        raise DebateRulesError("{}.exempt_seat_ids 必須是 array。".format(field))
    unknown = sorted(str(item) for item in value if item not in SEAT_IDS)
    if unknown:
        raise DebateRulesError(
            "{}.exempt_seat_ids 含未知席位：{}".format(field, "、".join(unknown))
        )
    return tuple(value)


def _positive_int(value, field):
    if type(value) is not int or value < 1:
        raise DebateRulesError(
            "{}.min_independent_domains 必須是正整數，收到 {!r}。".format(field, value)
        )
    return value


def _source_tiers(value, field):
    if (
        not isinstance(value, list)
        or not value
        or any(type(item) is not int or item < 1 for item in value)
    ):
        raise DebateRulesError(
            "{}.trusted_source_tiers 必須是至少一個正整數等級的 array，收到"
            " {!r}。".format(field, value)
        )
    return tuple(value)


_DOWNGRADE_VALIDATORS = {
    "min_independent_domains": _positive_int,
    "trusted_source_tiers": _source_tiers,
    "exempt_seat_ids": _exempt_seat_ids,
}


def _require_positive_window(timeline):
    """The first-round window must exist; where its wall lands is not our call.

    這裡刻意只驗證窗本身為正。牆的落點＝該 run 實際封存時刻＋窗，而封存時刻
    的唯一權威是 ``research_scheduler.research_deadlines``（比較題晚 30 秒）。
    要在載入時驗「牆一定早於 reduced_threshold_from」，這個載入器就得反過來
    依賴 research_scheduler，而它會再拉進 contract_validator 與 question —
    一個設定載入器不該為了一條非 Spec 的檢查變成整條研究管線的下游。牆落在
    門檻降低之後是合法設定，其行為由 :meth:`DebateRules.phase_at` 明確定義。
    """
    window = timeline["round_one_window"]
    if window <= 0:
        raise DebateRulesError(
            "timeline_ms.round_one_window（{}）必須為正整數毫秒。".format(window)
        )
