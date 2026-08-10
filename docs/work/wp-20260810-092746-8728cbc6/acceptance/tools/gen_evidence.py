"""Ticket 09：端到端驗收證據產生器。

只讀 repo、只寫證據輸出目錄。不改任何產品程式碼或測試。

重跑方式（WSL Ubuntu-24.04）：

    bash docs/work/wp-20260810-092746-8728cbc6/acceptance/tools/rerun.sh

Code Root 與輸出目錄都從**這個檔案自己的位置**推得，所以從 repo 任何地方呼叫都一樣；
要把輸出導到別處（例如複驗時不想覆蓋已凍結的證據）就設環境變數：

    HOYA_CODE_ROOT        Code Root，預設為 acceptance/tools 往上第五層
    HOYA_ACCEPTANCE_OUT   證據輸出目錄，預設為 acceptance/（本檔的上一層）

退出碼 0 代表所有判定 PASS，1 代表有 FAIL（哪一項看 stdout 與 checks.json）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 本票動工前的基準樹，寫進 checks.json 供對照。快照樹本身不寫在這裡：一個檔案
# 沒辦法記載「包含這個檔案的那棵樹」的雜湊，那個 SHA 記在 acceptance-summary.md。
BASELINE_TREE = "1ac0192e024320a913e3211c9c72c668b7c25432"

# acceptance/tools → acceptance → wp-… → work → docs → Code Root
DEFAULT_CODE_ROOT = HERE.parents[4]
DEFAULT_OUT = HERE.parent

CODE_ROOT = Path(os.environ.get("HOYA_CODE_ROOT", DEFAULT_CODE_ROOT)).resolve()
OUT = Path(os.environ.get("HOYA_ACCEPTANCE_OUT", DEFAULT_OUT)).resolve()
RENDERED = OUT / "rendered"

if not (CODE_ROOT / "hoya_market_agents").is_dir():
    raise SystemExit(
        "Code Root 不對：{} 底下沒有 hoya_market_agents。"
        "請從 repo 內執行，或設 HOYA_CODE_ROOT。".format(CODE_ROOT)
    )

sys.path.insert(0, str(CODE_ROOT))
sys.path.insert(0, str(CODE_ROOT / "tests"))

from evidence_lib import (  # noqa: E402
    contrast_ratio,
    header_of,
    labels_of,
    nav_named,
    read_controls,
    relative_luminance,
    sha256_of,
)

from hoya_market_agents import design_tokens, report_renderer, seats  # noqa: E402
from hoya_market_agents.question import (  # noqa: E402
    ASSET_CLASS_CRYPTO,
    ASSET_CLASS_OPEN,
    ASSET_CLASS_TW_STOCK,
    ASSET_CLASS_US_STOCK,
    ASSET_CLASSES,
)
from hoya_market_agents.run_index import rebuild_index  # noqa: E402
from hoya_market_agents.webapp import pages, server as server_module, settings, views  # noqa: E402

import test_frontend_redesign_acceptance as acc  # noqa: E402
from test_webapp import write_run  # noqa: E402

CHECKS = []


def check(cid, description, ok, detail=""):
    CHECKS.append(
        {"id": cid, "description": description, "ok": bool(ok), "detail": str(detail)}
    )
    print("[{}] {} {}".format("PASS" if ok else "FAIL", cid, description))
    if not ok:
        print("      detail: {}".format(detail))
    return bool(ok)


class Harness(acc.MenuRunFixture, unittest.TestCase):
    def runTest(self):  # pragma: no cover - fixture only
        return None


def new_harness():
    harness = Harness("runTest")
    harness.setUp()
    return harness


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def checks_document():
    """checks.json 的內容：怎麼重跑，加上 11 項判定本身。

    有了 ``generator`` 與 ``command`` 這兩欄，這份結果才真的「可重跑」而不只是
    「可讀」——Reviewer B 的 F-B09-1 講的就是這件事。``snapshot_tree`` 不在這裡：
    快照樹涵蓋這個檔案，檔案沒辦法記住包含自己的那棵樹的雜湊；它記在
    ``acceptance-summary.md`〈重跑與快照〉。
    """
    return {
        "generator": "docs/work/wp-20260810-092746-8728cbc6/acceptance/tools/gen_evidence.py",
        "runner": "docs/work/wp-20260810-092746-8728cbc6/acceptance/tools/rerun.sh",
        "command": (
            "bash docs/work/wp-20260810-092746-8728cbc6/acceptance/tools/rerun.sh"
        ),
        "environment": "WSL Ubuntu-24.04、Python 3.12.3",
        "baseline_tree": BASELINE_TREE,
        "snapshot_tree": "見 acceptance-summary.md〈重跑與快照〉",
        "full_suite": "python3 -m unittest discover -s tests（結果見 full-suite.txt）",
        "note": (
            "重跑會覆寫 acceptance/ 底下的證據；不想覆寫就設 HOYA_ACCEPTANCE_OUT "
            "指到別的目錄。歷史頁與未知鍵設定頁會印出當次的暫存 Data Root 路徑，"
            "那幾份 HTML 每跑一次就換一個路徑，其餘檔案逐位元組相同。"
        ),
        "checks": CHECKS,
    }


# -- 導覽讀法 ---------------------------------------------------------------

BROWSE_LABELS = ["即時辯論", "歷史與命中率", "市場報告", "完整辯論"]
SETTINGS_LABEL = "設定"
STOP_LABEL = "關閉伺服器"


def header_controls(body):
    """header 裡所有互動控制，照文件順序：[(kind, label), ...]。"""
    return read_controls(header_of(body)).controls


def header_navs(body):
    return read_controls(header_of(body)).navs


def nav_items(body):
    """header 兩個導覽群組合起來的項目清單。"""
    found = []
    for nav in header_navs(body):
        found.extend(nav["items"])
    return found


def item_named(body, label):
    for item in nav_items(body):
        if item["label"] == label:
            return item
    return None


def report_targets(body):
    """兩個報告分頁的 href（停用時為 None）。"""
    return {
        label: (item_named(body, label) or {}).get("href")
        for label in ("市場報告", "完整辯論")
    }


def audit_header(name, body, expect_run_id, lines):
    """一頁 header 的五導覽、設定位置與報告導覽指向。"""
    controls = header_controls(body)
    labels = [label for _, label in controls]
    items = nav_items(body)
    five = [item["label"] for item in items]
    targets = report_targets(body)

    ok_five = five == BROWSE_LABELS + [SETTINGS_LABEL]
    ok_order = labels[-2:] == [SETTINGS_LABEL, STOP_LABEL]
    if expect_run_id is None:
        ok_target = all(
            target is None for target in targets.values()
        ) and all(
            item["kind"] == "disabled"
            for item in items
            if item["label"] in ("市場報告", "完整辯論")
        )
        expected_text = "停用樣式（role=link, aria-disabled=true, 無 href）"
    else:
        expected = {
            "市場報告": "/run/{}/report.html".format(expect_run_id),
            "完整辯論": "/run/{}/debate.html".format(expect_run_id),
        }
        ok_target = targets == expected
        expected_text = "{} / {}".format(expected["市場報告"], expected["完整辯論"])

    lines.append("  {}".format(name))
    lines.append("    五導覽（依序）        ：{}".format(" ｜ ".join(five)))
    lines.append("    header 控制順序        ：{}".format(" → ".join(labels)))
    lines.append("    報告導覽指向          ：{}".format(targets))
    lines.append("    期望                  ：{}".format(expected_text))
    lines.append(
        "    判定                  ：五導覽 {}／設定在關閉伺服器左邊 {}／指向 {}".format(
            "PASS" if ok_five else "FAIL",
            "PASS" if ok_order else "FAIL",
            "PASS" if ok_target else "FAIL",
        )
    )
    return ok_five and ok_order and ok_target


# -- 一個市場的完整頁面組 ----------------------------------------------------

STALE_RUN = "20260101T000000Z-none-zzzz99"


def render_market(prefix, asset_class, harness, run):
    """一個市場的所有 webapp 頁面（渲染後 HTML），照使用者會看的順序。"""
    rendered = {}
    rendered["home-waiting"] = harness.get("/?run={}".format(STALE_RUN)).body
    rendered["room"] = harness.room(run)
    rendered["history"] = harness.get("/history").body
    rendered["settings"] = harness.get("/settings").body
    rendered["run_detail"] = harness.get("/run/{}".format(run.run_id)).body
    rendered["not_found"] = harness.get("/no-such-page").body
    rendered["launch_problem"] = harness.submit(
        question="測試：缺標的的送出", asset_class=asset_class, target=None
    ).body
    rendered["error_500"] = render_error_page(harness)
    rendered["closed"] = harness.post(server_module.SHUTDOWN_PATH, {}).body
    for name, body in rendered.items():
        write(RENDERED / "{}-{}.html".format(prefix, name), body)
    return rendered


def render_error_page(harness):
    """把某一頁的組版打斷，好取得請求邊界那一頁 500 的實際輸出。

    只在這個行程裡替換一個函式，檔案不動；換回來之後其他頁面照舊。
    """
    original = pages.render_history_page

    def explode(_data):
        raise RuntimeError("Ticket 09 驗收：刻意讓這一頁組版失敗")

    pages.render_history_page = explode
    try:
        response = harness.get("/history")
    finally:
        pages.render_history_page = original
    if response.status != 500:
        raise AssertionError("預期 500，實得 {}".format(response.status))
    return response.body


def market_run(prefix, harness):
    if prefix == "tw_stock":
        return harness.tw_stock_run()
    return harness.crypto_run()


# -- 主流程 -----------------------------------------------------------------


def main():
    RENDERED.mkdir(parents=True, exist_ok=True)
    nav_lines = ["Ticket 09 驗收條件 2：五導覽、設定位置與報告導覽指向", "=" * 72, ""]
    nav_ok = True
    markets = {}

    for prefix, asset_class in (
        ("tw_stock", ASSET_CLASS_TW_STOCK),
        ("crypto", ASSET_CLASS_CRYPTO),
    ):
        harness = new_harness()
        run = market_run(prefix, harness)
        rendered = render_market(prefix, asset_class, harness, run)
        markets[prefix] = {
            "harness": harness,
            "run": run,
            "asset_class": asset_class,
            "rendered": rendered,
        }

        nav_lines.append("{}（run_id={}）".format(prefix, run.run_id))
        # 這個 Data Root 只有這一趟 run，所以「最新有報告的 run」與「這一頁自己的
        # run」是同一個；兩條規則的分辨由 tests/test_webapp 的 HeaderFixture 釘住，
        # 這裡驗的是整包接起來以後每一頁真的指得到。
        for name in (
            "home-waiting",
            "room",
            "history",
            "settings",
            "run_detail",
            "not_found",
            "launch_problem",
        ):
            nav_ok &= audit_header(name, rendered[name], run.run_id, nav_lines)
        nav_ok &= audit_header("error_500", rendered["error_500"], None, nav_lines)

        closed_controls = header_controls(rendered["closed"])
        closed_ok = closed_controls == []
        nav_lines.append("  closed（伺服器已關閉頁）")
        nav_lines.append("    header 控制            ：{}".format(closed_controls or "（無）"))
        nav_lines.append(
            "    判定                  ：無導覽、無關閉鈕 {}".format(
                "PASS" if closed_ok else "FAIL"
            )
        )
        nav_lines.append("")
        nav_ok &= closed_ok

    # 無報告 Data Root：兩個報告分頁必須是停用樣式
    empty = new_harness()
    empty_pages = {
        "home": empty.get("/").body,
        "history": empty.get("/history").body,
        "settings": empty.get("/settings").body,
    }
    nav_lines.append("empty（同一份程式、Data Root 沒有任何 run）")
    for name, body in empty_pages.items():
        write(RENDERED / "empty-{}.html".format(name), body)
        nav_ok &= audit_header(name, body, None, nav_lines)
    nav_lines.append("")

    check("AC2-nav", "每頁五導覽、設定在關閉伺服器左邊、報告導覽指向正確", nav_ok)

    # run 詳情頁指向自身、其他頁指向最新有報告 run：兩個市場各自成立
    for prefix, bundle in markets.items():
        run_id = bundle["run"].run_id
        latest = views.latest_report_run(bundle["harness"].data_root)
        same = latest is not None and latest["run_id"] == run_id
        check(
            "AC2-latest-{}".format(prefix),
            "{}：latest_report_run 就是這一趟 run".format(prefix),
            same,
            latest,
        )
    write(OUT / "nav-audit.txt", "\n".join(nav_lines) + "\n")

    for name, section in (
        ("settings", settings_evidence),
        ("injection", injection_evidence),
        ("design", design_evidence),
        ("roster", roster_evidence),
        ("ask-menu", ask_menu_evidence),
        ("protected-zone", protected_zone_evidence),
        ("a5", a5_evidence),
    ):
        try:
            section(markets)
        except Exception as exc:  # noqa: BLE001 - 一段爆掉不該吃掉其他段的證據
            import traceback

            traceback.print_exc()
            check(
                "SECTION-{}".format(name),
                "{} 證據段落執行到底".format(name),
                False,
                "{}: {}".format(type(exc).__name__, exc),
            )

    for bundle in markets.values():
        bundle["harness"].doCleanups()
    empty.doCleanups()

    write(
        OUT / "checks.json",
        json.dumps(checks_document(), ensure_ascii=False, indent=2) + "\n",
    )
    failed = [entry for entry in CHECKS if not entry["ok"]]
    print("\n檢查總數 {}｜PASS {}｜FAIL {}".format(len(CHECKS), len(CHECKS) - len(failed), len(failed)))
    for entry in failed:
        print("FAILED: {} {}".format(entry["id"], entry["description"]))
    return 0 if not failed else 1


# -- 驗收條件 1：設定頁白話中文 ----------------------------------------------

SPEC_SECTION_LABELS = {
    "": "基本",
    "timeline_ms": "時間軸（毫秒）",
    "vote_thresholds": "票數門檻",
    "confidence": "燈號規則",
    "confidence.light_scale[]": "燈號階梯",
    "confidence.downgrades.few_independent_domains": "降級：獨立來源不足",
    "confidence.downgrades.low_trust_source": "降級：低可信來源",
}

SPEC_FIELD_TABLE = [
    ("schema_version", "規則檔版本", "規則檔的格式版本，目前僅支援 1，平常不需改動"),
    ("timeline_ms.debate_start", "證據封存時刻", "開賽後多久結束研究、封存證據（毫秒），之後進入辯論"),
    ("timeline_ms.round_one_window", "第一輪挑戰時窗", "證據封存後，留給第一輪反方挑戰的時間長度（毫秒）"),
    ("timeline_ms.reduced_threshold_from", "門檻下調時刻", "從此時間點起，過關票數由「初始」降為「下調後」"),
    ("timeline_ms.final_round_start", "最終輪開始", "最後一輪辯論的開始時間"),
    ("timeline_ms.final_round_end", "最終輪結束", "最後一輪辯論的結束時間"),
    ("timeline_ms.force_stop", "強制結算時刻", "時間到就強制結算：達強停票數採納立場，否則未達共識"),
    ("vote_thresholds.unanimous_blind_pass", "盲投直過票數", "開場盲投全數同立場達此票數，直接產出藍燈報告、不進辯論"),
    ("vote_thresholds.initial", "初始過關票數", "辯論開始時，達成共識所需的有效票數"),
    ("vote_thresholds.reduced", "下調後過關票數", "門檻下調時刻後，達成共識所需的有效票數"),
    ("vote_thresholds.forced_stop", "強停採納票數", "強制結算時至少要這麼多票才採納立場，否則未達共識"),
    ("confidence.light_scale[].min_votes", "最低票數", "拿到至少這麼多有效票，燈號落在這一級"),
    ("confidence.light_scale[].level", "燈色", "這一級對應的燈色（blue／green／yellow／orange／red）"),
    ("confidence.downgrades.few_independent_domains.levels", "降幾級", "獨立來源網站太少時，燈號往下降的級數"),
    (
        "confidence.downgrades.few_independent_domains.min_independent_domains",
        "最低獨立網域數",
        "採納立場引用的來源至少要來自幾個不同網站",
    ),
    ("confidence.downgrades.low_trust_source.levels", "降幾級", "引用低可信來源時，燈號往下降的級數"),
    (
        "confidence.downgrades.low_trust_source.trusted_source_tiers",
        "可信來源等級",
        "視為可信的來源等級清單（逗號分隔）",
    ),
    (
        "confidence.downgrades.low_trust_source.exempt_seat_ids",
        "豁免席位",
        "不受此降級約束的席位（輿情席職責即蒐集輿情）",
    ),
]


def settings_evidence(markets):
    harness = markets["tw_stock"]["harness"]
    body = markets["tw_stock"]["rendered"]["settings"]
    data = settings.settings_data(harness.data_root)
    lines = ["Ticket 09 驗收條件 1：設定頁逐鍵中文標籤、白話說明與未知鍵 fallback", "=" * 72, ""]

    # 分組標題
    lines.append("一、分組標題（渲染後的 <legend> 可見文字）")
    rendered_sections = {section["path"]: section["label"] for section in data["sections"]}
    section_ok = True
    for path, section in sorted(rendered_sections.items()):
        expected = SPEC_SECTION_LABELS.get(
            settings._generic(path) if path else "", None
        )
        good = section == expected
        section_ok &= good
        lines.append(
            "  {:<52} → {:<20} 期望 {:<20} {}".format(
                path or "（最外層）", section, str(expected), "PASS" if good else "FAIL"
            )
        )
        good_visible = section in acc.visible_text(body)
        section_ok &= good_visible
    lines.append("  分組標題全部為 Spec 指定中文且出現在渲染後頁面：{}".format(
        "PASS" if section_ok else "FAIL"))
    lines.append("")

    # 逐鍵
    lines.append("二、逐鍵中文標籤與白話說明（Spec R-001 逐鍵表 vs 渲染後頁面）")
    visible = acc.visible_text(body)
    rendered_fields = {}
    for section in data["sections"]:
        for field in section["fields"]:
            rendered_fields[settings._generic(field["path"])] = (
                field["label"],
                field["description"],
                field["path"],
            )
    field_ok = True
    for generic, label, description in SPEC_FIELD_TABLE:
        got = rendered_fields.get(generic)
        if got is None:
            field_ok = False
            lines.append("  {:<58} 未出現在設定頁 FAIL".format(generic))
            continue
        good = got[0] == label and got[1] == description
        on_page = label in visible and description in visible
        field_ok &= good and on_page
        lines.append(
            "  {:<58} 標籤={:<12} 說明對應 Spec={} 渲染後可見={} {}".format(
                generic,
                got[0],
                "是" if got[1] == description else "否（{}）".format(got[1]),
                "是" if on_page else "否",
                "PASS" if good and on_page else "FAIL",
            )
        )
    covered = set(rendered_fields) - {generic for generic, _, _ in SPEC_FIELD_TABLE}
    lines.append("  Spec 逐鍵表未涵蓋而頁面上有的鍵：{}".format(sorted(covered) or "（無）"))
    field_ok &= not covered
    lines.append("  逐鍵標籤與白話說明：{}".format("PASS" if field_ok else "FAIL"))
    lines.append("")

    check("AC1-labels", "設定頁逐鍵中文標籤＋白話說明、分組標題中文", section_ok and field_ok)

    # 未知鍵 fallback
    lines.append("三、未知鍵 fallback（測試設定檔，不動 config/debate_rules.json）")
    tmp = Path(tempfile.mkdtemp(prefix="t09-rules-"))
    try:
        source = json.loads(
            (CODE_ROOT / "config" / "debate_rules.json").read_text(encoding="utf-8")
        )
        source["brand_new_knob"] = 5
        source["experimental_group"] = {"tuning_knob": 3}
        probe = tmp / "debate_rules.json"
        probe.write_text(
            json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        probe_data = settings.settings_data(harness.data_root, rules_path=probe)
        probe_body = pages.render_settings_page(probe_data)
        write(RENDERED / "settings-unknown-key.html", probe_body)
        probe_visible = acc.visible_text(probe_body)

        found = {}
        for section in probe_data["sections"]:
            for field in section["fields"]:
                found[field["path"]] = field
        knob = found.get("brand_new_knob")
        nested = found.get("experimental_group.tuning_knob")
        unknown_section = [
            section
            for section in probe_data["sections"]
            if section["path"] == "experimental_group"
        ]
        import re as _re

        tag = _re.search(r'<input [^>]*id="brand_new_knob"[^>]*>', probe_body)
        editable = tag is not None and 'value="5"' in tag.group(0)
        disabled_free = tag is not None and "disabled" not in tag.group(0)
        marks = [
            ("原鍵名 brand_new_knob 出現在畫面上", "brand_new_knob" in probe_visible),
            ("該鍵標為尚未翻譯", knob is not None and knob["untranslated"]),
            (
                "該鍵沒有被塞進任何白話說明",
                knob is not None and knob["description"] is None,
            ),
            ("「尚未翻譯」四個字出現在畫面上", settings.UNTRANSLATED_NOTE in probe_visible),
            ("該鍵仍是可編輯的 text input（帶原值）", editable),
            ("該控制沒有被 disabled", disabled_free),
            (
                "未知分組也用原路徑當標題並標尚未翻譯",
                bool(unknown_section) and unknown_section[0]["untranslated"],
            ),
            (
                "未知分組裡的鍵照樣有控制",
                nested is not None and nested["untranslated"],
            ),
            (
                "已翻譯的鍵不受影響（仍是中文標籤）",
                found.get("vote_thresholds.initial", {}).get("label") == "初始過關票數",
            ),
        ]
        fallback_ok = all(ok for _, ok in marks)
        for text, ok in marks:
            lines.append("  {:<44} {}".format(text, "PASS" if ok else "FAIL"))
        lines.append("")
        lines.append("  註：載入器本來就會拒絕未知欄位，所以這一份測試設定檔的頁面上會多一段")
        lines.append("      載入器的紅字（見 rendered/settings-unknown-key.html）。R-001 要求的")
        lines.append("      「不 fail-closed」是**設定頁**照樣把鍵畫出來且可編輯，上面九條就是它；")
        lines.append("      載入器對未知欄位的態度不在本工作包範圍。")
        lines.append("  載入器對這份檔案的說法：{}".format(probe_data["problem"]))
        check("AC1-fallback", "未知鍵顯示原鍵名＋尚未翻譯且可編輯", fallback_ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    write(OUT / "settings-labels.txt", "\n".join(lines) + "\n")


# -- 驗收條件 3：離線頁導覽注入 ----------------------------------------------


def injection_evidence(markets):
    lines = ["Ticket 09 驗收條件 3：離線兩頁經伺服器瀏覽的導覽注入", "=" * 72, ""]
    ok = True
    for prefix, bundle in markets.items():
        harness = bundle["harness"]
        run = bundle["run"]
        lines.append("{}（run_id={}）".format(prefix, run.run_id))
        for name in ("report.html", "debate.html"):
            path = run.run_dir / name
            before = (sha256_of(path), path.stat().st_size)
            disk = path.read_bytes()
            response = harness.get("/run/{}/{}".format(run.run_id, name))
            after = (sha256_of(path), path.stat().st_size)
            served = response.body
            served_bytes = served.encode("utf-8")
            write(RENDERED / "{}-served-{}".format(prefix, name), served)
            write(RENDERED / "{}-offline-{}".format(prefix, name), disk.decode("utf-8"))

            # 插入點自己數一次，不借用受測模組的函式。
            lower = disk.lower()
            cut = lower.find(b">", lower.find(b"<body")) + 1

            site_nav = nav_named(read_controls(served), server_module.SITE_NAV_LABEL)
            bundle_navs = [
                nav
                for nav in read_controls(served).navs
                if nav["label"] != server_module.SITE_NAV_LABEL
            ]
            five = labels_of(site_nav) if site_nav else []
            targets = {
                item["label"]: item["href"]
                for item in (site_nav["items"] if site_nav else [])
            }
            checks = [
                ("回應狀態 200", response.status == 200),
                ("站內導覽存在且是五個", five == BROWSE_LABELS + [SETTINGS_LABEL]),
                (
                    "四個站內連結可回站內",
                    targets.get("即時辯論") == "/"
                    and targets.get("歷史與命中率") == "/history"
                    and targets.get("設定") == "/settings",
                ),
                (
                    "兩個報告分頁指向這一趟 run 自己的檔案",
                    targets.get("市場報告") == "/run/{}/report.html".format(run.run_id)
                    and targets.get("完整辯論")
                    == "/run/{}/debate.html".format(run.run_id),
                ),
                ("離線頁自己的兩分頁導覽仍在", bool(bundle_navs)),
                ("注入區零 script", "<script" not in server_module.site_nav_fragment(
                    run.run_id, server_module.run_artifacts(harness.data_root, run.run_id)
                )),
                ("磁碟檔案雜湊不變", before[0] == after[0]),
                ("磁碟檔案位元組數不變", before[1] == after[1]),
                (
                    "送出的位元組＝原檔在 <body> 之後插入一段，原檔位元組一個不動",
                    len(served_bytes) > len(disk)
                    and served_bytes.startswith(disk[:cut])
                    and served_bytes.endswith(disk[cut:]),
                ),
            ]
            for text, good in checks:
                ok &= good
                lines.append("  {:<20} {:<44} {}".format(name, text, "PASS" if good else "FAIL"))
            lines.append(
                "    磁碟 sha256 前：{}｜後：{}｜位元組 {} → {}".format(
                    before[0][:16], after[0][:16], before[1], after[1]
                )
            )
            lines.append("    站內導覽指向：{}".format(targets))
        lines.append("")

    # 無插入點：原樣送出
    nav = server_module.site_nav_fragment("any-run", None)
    passthrough = [
        ("完全沒有 <body>", b"<p>\xe6\xb2\x92\xe6\x9c\x89 body</p>"),
        ("只有註解裡的 <body>", b"<!-- <body> -->\n<p>only a comment</p>"),
        ("註解沒有關閉", b"<!-- <body>\n<p>never closed</p>"),
        ("屬性沒有收尾", b'<html><body data-note="1 > 0'),
    ]
    lines.append("無插入點的 HTML 原樣送出（artifact_with_site_nav 的略過路徑）")
    for text, payload in passthrough:
        same = server_module.artifact_with_site_nav(payload, nav) == payload
        ok &= same
        lines.append("  {:<24} 原樣送出 {}".format(text, "PASS" if same else "FAIL"))
    inserted = server_module.artifact_with_site_nav(b"<html><body>x</body></html>", nav)
    grew = inserted != b"<html><body>x</body></html>" and inserted.startswith(
        b"<html><body>"
    )
    ok &= grew
    lines.append("  {:<24} 有插入點時才插入 {}".format("鑑別力", "PASS" if grew else "FAIL"))
    lines.append("")

    # 內嵌（預覽面板）不注入
    bundle = markets["tw_stock"]
    embedded = bundle["harness"].get(
        "/run/{}/report.html".format(bundle["run"].run_id),
        headers=[("Sec-Fetch-Dest", "iframe")],
    )
    embedded_clean = server_module.SITE_NAV_LABEL not in embedded.body
    ok &= embedded_clean
    lines.append(
        "預覽面板（Sec-Fetch-Dest: iframe）不加站內導覽：{}".format(
            "PASS" if embedded_clean else "FAIL"
        )
    )

    write(OUT / "nav-injection.txt", "\n".join(lines) + "\n")
    check("AC3-injection", "經伺服器瀏覽離線頁有五導覽、磁碟位元組不變、無插入點原樣送出", ok)


# -- 驗收條件 4：白底新設計與對比度 ------------------------------------------


def colours_in(text):
    """一段文字裡出現過的所有色值（hex 與 rgba），正規化後的集合。"""
    import re

    found = {match.group(0).lower() for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b", text)}
    found |= {
        match.group(0).replace(" ", "").lower()
        for match in re.finditer(r"rgba?\([^)]*\)", text)
    }
    return found


def design_evidence(markets):
    lines = ["Ticket 09 驗收條件 4：白底單一設計、無深色 media query、WCAG AA 實測", "=" * 72, ""]
    ok = True

    site_css = pages.stylesheet()
    offline_report = markets["tw_stock"]["run"].artifact("report.html")
    offline_debate = markets["tw_stock"]["run"].artifact("debate.html")
    injected = server_module.site_nav_fragment("any", None)
    surfaces = {
        "webapp 樣式表 pages.stylesheet()": site_css,
        "離線市場報告 report.html": offline_report,
        "離線完整辯論 debate.html": offline_debate,
        "注入導覽自帶 <style>": injected,
    }
    lines.append("一、深色模式退場：成品樣式裡沒有 prefers-color-scheme")
    for name, text in surfaces.items():
        clean = "prefers-color-scheme" not in text
        ok &= clean
        lines.append("  {:<40} {}".format(name, "PASS（0 命中）" if clean else "FAIL"))
    for prefix, bundle in markets.items():
        for name, body in bundle["rendered"].items():
            clean = "prefers-color-scheme" not in body
            ok &= clean
            if not clean:
                lines.append("  {}-{} FAIL".format(prefix, name))
    lines.append("  全部已渲染 webapp 頁面：{}".format("PASS（0 命中）" if ok else "FAIL"))
    lines.append("")

    lines.append("二、單一白底 palette（design_tokens.PALETTE 只有一套）")
    lines.append("  page（畫布）  ：{}".format(design_tokens.PALETTE["page"]))
    lines.append("  surface（卡片）：{}".format(design_tokens.PALETTE["surface"]))
    lines.append("  毛玻璃（原值） ：{}".format(design_tokens.PALETTE["glass_surface"]))
    lines.append(
        "  毛玻璃（合成後）：{}（{} 疊在 {} 上）".format(
            design_tokens.MEASURED_COLOURS["glass_surface"],
            design_tokens.PALETTE["glass_surface"],
            design_tokens.PALETTE["page"],
        )
    )
    light = relative_luminance(design_tokens.MEASURED_COLOURS["page"]) > 0.5
    ok &= light
    lines.append(
        "  畫布相對亮度 {:.4f} > 0.5（白底而非深底）：{}".format(
            relative_luminance(design_tokens.MEASURED_COLOURS["page"]),
            "PASS" if light else "FAIL",
        )
    )
    lines.append("")

    lines.append("三、全站與新 run 離線頁同一套色（每個色值都由 design_tokens 擁有）")
    owned = {value.lower() for value in design_tokens.PALETTE.values()}
    owned |= {value.lower() for value in design_tokens.MEASURED_COLOURS.values()}
    for name, text in (
        ("webapp 樣式表", site_css),
        ("離線市場報告 report.html", offline_report),
        ("離線完整辯論 debate.html", offline_debate),
        ("注入導覽自帶 <style>", injected),
    ):
        used = colours_in(text)
        stray = sorted(colour for colour in used if colour not in owned)
        ok &= not stray
        lines.append(
            "  {:<30} 用到 {:>2} 個色值｜不屬於 design_tokens 的：{}".format(
                name, len(used), stray or "（無）"
            )
        )
    lines.append("  webapp 與離線兩頁用的是同一組色值：{}".format(
        "PASS" if colours_in(site_css) == colours_in(offline_report) | colours_in(
            offline_debate
        ) else "兩邊色值集合不同（見上方逐項）"
    ))
    stack_ok = design_tokens.SCALE["font_sans"].startswith('"Microsoft JhengHei"')
    on_pages = all(
        design_tokens.SCALE["font_sans"] in text
        for text in (site_css, offline_report, offline_debate)
    )
    ok &= stack_ok and on_pages
    lines.append(
        "  字體堆疊微軟正黑體優先且三處同一份：{}".format(
            "PASS" if stack_ok and on_pages else "FAIL"
        )
    )
    lines.append("  字體堆疊：{}".format(design_tokens.SCALE["font_sans"]))
    lines.append("  毛玻璃用原生 backdrop-filter，模糊值 {}；零外部資源（樣式裡沒有 @import／url()）：{}".format(
        design_tokens.SCALE["glass_blur"],
        "PASS" if all(
            "@import" not in text and "url(" not in text
            for text in (site_css, offline_report, offline_debate)
        ) else "FAIL",
    ))
    ok &= all(
        "@import" not in text and "url(" not in text
        for text in (site_css, offline_report, offline_debate)
    )
    lines.append("")

    lines.append("四、對比度實測（WCAG 2.x 相對亮度公式，本檔獨立實作一次）")
    lines.append("")
    lines.append(
        "  {:<16} {:<18} {:<9} {:<9} {:>7} {:>7}  {}".format(
            "前景", "背景", "前景色", "背景色", "實測", "門檻", "判定"
        )
    )
    rows = 0
    worst = None
    for foreground, background, minimum in design_tokens.CONTRAST_REQUIREMENTS:
        fore = design_tokens.MEASURED_COLOURS[foreground]
        back = design_tokens.MEASURED_COLOURS[background]
        ratio = contrast_ratio(fore, back)
        good = ratio >= minimum
        ok &= good
        rows += 1
        margin = ratio - minimum
        if worst is None or margin < worst[0]:
            worst = (margin, foreground, background, ratio, minimum)
        lines.append(
            "  {:<16} {:<18} {:<9} {:<9} {:>7.2f} {:>7.2f}  {}".format(
                foreground, background, fore, back, ratio, minimum,
                "PASS" if good else "FAIL",
            )
        )
    lines.append("")
    lines.append("  共 {} 組配對，全部達 WCAG AA：{}".format(rows, "PASS" if ok else "FAIL"))
    lines.append(
        "  餘裕最小的一組：{} on {} = {:.2f}（門檻 {:.1f}，餘裕 {:+.2f}）".format(
            worst[1], worst[2], worst[3], worst[4], worst[0]
        )
    )
    lines.append(
        "  毛玻璃列（glass_surface）用的是合成後實色 {}，不是 rgba 本身。".format(
            design_tokens.MEASURED_COLOURS["glass_surface"]
        )
    )
    write(OUT / "contrast-table.txt", "\n".join(lines) + "\n")
    check("AC4-design", "白底單一設計、無深色 media query、對比度全數達 WCAG AA", ok)


# -- 驗收條件 5：席位名稱與 blurb --------------------------------------------


def roster_evidence(markets):
    lines = ["Ticket 09 驗收條件 5：席位定案名、白話說明與 roster fail-closed", "=" * 72, ""]
    ok = True
    lines.append("一、席位卡定案名與 blurb（roster 為唯一權威，這裡逐席讀渲染後的字）")
    for prefix, bundle in markets.items():
        run = bundle["run"]
        asset_class = bundle["asset_class"]
        room_text = acc.visible_text(bundle["rendered"]["room"])
        profiles = seats.seat_profiles(asset_class)
        names = seats.seat_display_names(asset_class)
        lines.append("")
        lines.append("  {}（套組：{}）".format(prefix, seats.profile_set_for(asset_class)))
        for seat_id in seats.SEAT_IDS:
            profile = profiles[seat_id]
            name_ok = profile.display_name in room_text
            blurb_ok = profile.blurb in room_text
            ok &= name_ok and blurb_ok
            lines.append(
                "    {:<18} {:<16} 名稱在席位卡 {}｜blurb 在席位卡 {}".format(
                    seat_id,
                    profile.display_name,
                    "PASS" if name_ok else "FAIL",
                    "PASS" if blurb_ok else "FAIL",
                )
            )
            lines.append("      blurb：{}".format(profile.blurb))
        shown = acc.names_shown_per_seat(room_text)
        exactly_one = all(
            shown[seat_id] == {names[seat_id]} for seat_id in seats.SEAT_IDS
        )
        ok &= exactly_one
        lines.append(
            "    每一席恰好只出現這一套的名稱（沒有混套）：{}".format(
                "PASS" if exactly_one else "FAIL"
            )
        )
        offline = acc.names_shown_per_seat(acc.visible_text(run.artifact("report.html")))
        agree = all(offline[seat_id] == shown[seat_id] for seat_id in seats.SEAT_IDS)
        ok &= agree
        lines.append(
            "    離線報告席名與 webapp 逐席一致：{}".format("PASS" if agree else "FAIL")
        )

    lines.append("")
    lines.append("二、美股與台股共用同一套（Spec R-005：stock 套為台股與美股共用）")
    same_set = seats.seat_display_names(ASSET_CLASS_US_STOCK) == seats.seat_display_names(
        ASSET_CLASS_TW_STOCK
    )
    ok &= same_set
    lines.append("  seat_display_names(us_stock) == seat_display_names(tw_stock)：{}".format(
        "PASS" if same_set else "FAIL"))
    for seat_id, name in seats.seat_display_names(ASSET_CLASS_US_STOCK).items():
        lines.append("    {:<18} {}".format(seat_id, name))

    lines.append("")
    lines.append("三、roster fail-closed（改測試副本，config/agent_roster.json 不動）")
    tmp = Path(tempfile.mkdtemp(prefix="t09-roster-"))
    try:
        original = json.loads(
            (CODE_ROOT / "config" / "agent_roster.json").read_text(encoding="utf-8")
        )
        cases = []

        missing_set = json.loads(json.dumps(original))
        del missing_set["seats"][0]["profiles"]["crypto"]
        cases.append(("缺 crypto 套組", missing_set))

        missing_blurb = json.loads(json.dumps(original))
        del missing_blurb["seats"][2]["profiles"]["stock"]["blurb"]
        cases.append(("stock 套缺 blurb", missing_blurb))

        blank_blurb = json.loads(json.dumps(original))
        blank_blurb["seats"][4]["profiles"]["crypto"]["blurb"] = "   "
        cases.append(("crypto 套 blurb 是空白", blank_blurb))

        for name, document in cases:
            probe = tmp / "roster.json"
            probe.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                seats.load_roster(probe)
            except seats.RosterError as exc:
                readable = str(exc)
                good = bool(readable.strip())
            else:
                readable = "（沒有拋出 RosterError）"
                good = False
            ok &= good
            lines.append("  {:<20} 載入失敗 {}".format(name, "PASS" if good else "FAIL"))
            lines.append("    錯誤訊息：{}".format(readable))
        healthy = seats.load_roster(CODE_ROOT / "config" / "agent_roster.json")
        good = len(healthy) == len(seats.SEAT_IDS)
        ok &= good
        lines.append(
            "  {:<20} 現行 roster 照樣載得進來（{} 席）{}".format(
                "鑑別力", len(healthy), "PASS" if good else "FAIL"
            )
        )
        lines.append("  roster_version：{}".format(original.get("roster_version")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    write(OUT / "seat-roster.txt", "\n".join(lines) + "\n")
    check("AC5-roster", "席位定案名＋blurb、兩處一致、roster fail-closed", ok)


# -- 驗收條件 6：發問選單與歷史開放題 ----------------------------------------


def ask_menu_evidence(markets):
    lines = ["Ticket 09 驗收條件 6：發問選單無開放題、歷史開放題 run 可回看", "=" * 72, ""]
    ok = True
    room = markets["tw_stock"]["rendered"]["room"]

    reader = _options_of(room, pages.ASSET_CLASS_CONTROL_ID)
    offered_values = [value for value, _ in reader if value]
    offered_labels = [label for value, label in reader if value]
    placeholder = [label for value, label in reader if not value]
    lines.append("一、發問區「資產類別」選單（渲染後的 <option>）")
    for value, label in reader:
        lines.append(
            "  value={:<10} 畫面上的字＝{}".format(value or "（空）", label)
        )
    three = set(offered_values) == {
        ASSET_CLASS_TW_STOCK,
        ASSET_CLASS_US_STOCK,
        ASSET_CLASS_CRYPTO,
    }
    no_open = ASSET_CLASS_OPEN not in offered_values and "開放題" not in offered_labels
    ok &= three and no_open
    lines.append(
        "  可選的三個市場恰為台股／美股／幣：{}（另有一個未選擇的提示項「{}」，不是市場）".format(
            "PASS" if three else "FAIL", "".join(placeholder)
        )
    )
    lines.append("  沒有開放題（值與字都沒有）：{}".format("PASS" if no_open else "FAIL"))
    boxes = [
        asset_class
        for asset_class in ASSET_CLASSES
        if 'id="asset-{}"'.format(asset_class) in room
    ]
    good = ASSET_CLASS_OPEN not in boxes and set(boxes) == set(offered_values)
    ok &= good
    lines.append(
        "  標的輸入框與選單同一組市場、沒有開放題：{}（{}）".format(
            "PASS" if good else "FAIL", boxes
        )
    )
    lines.append("")

    lines.append("二、歷史頁的資產類別篩選（回看入口，Spec 明文保留開放題）")
    history = markets["tw_stock"]["rendered"]["history"]
    filters = _options_of(history, "asset_class")
    for value, label in filters:
        lines.append("  value={:<10} 畫面上的字＝{}".format(value, label))
    has_open = ASSET_CLASS_OPEN in [value for value, _ in filters]
    ok &= has_open
    lines.append("  歷史篩選仍提供開放題：{}".format("PASS" if has_open else "FAIL"))
    lines.append("")

    lines.append("三、歷史開放題 run 仍可開啟回看")
    harness = markets["tw_stock"]["harness"]
    open_run_id = "20260210T031500Z-open-oo0011"
    write_run(
        harness.data_root,
        open_run_id,
        "美國通膨會不會在下一季轉向",
        assets=("美國通膨",),
        asset_class=ASSET_CLASS_OPEN,
        level="green",
    )
    rebuild_index(harness.data_root)
    detail = harness.get("/run/{}".format(open_run_id))
    listed = harness.get("/history?asset_class={}".format(ASSET_CLASS_OPEN))
    write(RENDERED / "open-run_detail.html", detail.body)
    write(RENDERED / "open-history-filtered.html", listed.body)
    detail_text = acc.visible_text(detail.body)
    marks = [
        ("run 詳情頁回應 200", detail.status == 200),
        ("詳情頁寫著開放題（中文）", pages.asset_class_label(ASSET_CLASS_OPEN) in detail_text),
        ("歷史頁用開放題篩選找得到它", open_run_id in harness.listed_run_ids(listed.body)),
        ("詳情頁讀得到這個 run 的題目", "美國通膨會不會在下一季轉向" in detail_text),
    ]
    for text, good in marks:
        ok &= good
        lines.append("  {:<32} {}".format(text, "PASS" if good else "FAIL"))
    lines.append("  run_id：{}".format(open_run_id))

    write(OUT / "ask-menu.txt", "\n".join(lines) + "\n")
    check("AC6-menu", "發問選單只有三市場、歷史開放題 run 可回看", ok)


def _options_of(body, select_id):
    """某個 <select> 的 (value, 畫面上的字)，照出現順序。"""
    import re

    found = re.search(
        r'<select[^>]*id="{}"[^>]*>(.*?)</select>'.format(re.escape(select_id)),
        body,
        re.DOTALL,
    )
    if not found:
        return []
    return [
        (value, acc.visible_text(text))
        for value, text in re.findall(
            r'<option value="([^"]*)"[^>]*>(.*?)</option>', found.group(1), re.DOTALL
        )
    ]


# -- 驗收條件 7（前半）：保護區行為 ------------------------------------------


def protected_zone_evidence(markets):
    lines = ["Ticket 09 驗收條件 7：保護區（聊天室、燈位、三種票數）行為回歸", "=" * 72, ""]
    ok = True
    for prefix, bundle in markets.items():
        harness = bundle["harness"]
        run = bundle["run"]
        lines.append("")
        lines.append("{}（run_id={}）".format(prefix, run.run_id))

        votes = json.loads((run.run_dir / "votes.json").read_text(encoding="utf-8"))
        expected = {
            stance: votes["tally"].get(stance, 0)
            for stance in run.package.stance_options
        }
        shown = harness.tally_shown(run)
        labels = run.package.stance_labels
        lines.append("  一、三種票數：公開紀錄 votes.json 的數字 vs 畫面上的數字")
        for stance in run.package.stance_options:
            good = expected[stance] == shown.get(stance)
            ok &= good
            lines.append(
                "    {:<18}（{}）紀錄 {} ｜ 畫面 {} {}".format(
                    labels[stance],
                    stance,
                    expected[stance],
                    shown.get(stance),
                    "PASS" if good else "FAIL",
                )
            )
        three = len(expected) == 3
        distinct = len(set(expected.values())) != 1
        ok &= three and distinct
        lines.append(
            "    恰好三種立場 {}｜三個數字不全等（逐項比對有鑑別力）{}".format(
                "PASS" if three else "FAIL", "PASS" if distinct else "FAIL"
            )
        )
        english = acc.english_values_in(
            harness.region(run, harness.TALLY_REGION_ID, "票數區"),
            set(run.package.stance_options),
        )
        ok &= english == []
        lines.append(
            "    票數區沒有英文立場原值：{}（{}）".format(
                "PASS" if english == [] else "FAIL", english or "0 命中"
            )
        )

        events = [
            json.loads(line)
            for line in run.artifact("events.jsonl").splitlines()
            if line.strip()
        ]
        spoke = {
            entry["seat_id"] for entry in events if entry.get("event") == "seat_message"
        }
        feed = harness.region(run, harness.FEED_REGION_ID, "聊天室")
        feed_names = acc.names_shown_per_seat(feed)
        identities = seats.seat_display_names(bundle["asset_class"])
        all_seven = spoke == set(seats.SEAT_IDS)
        carried = all(
            feed_names[seat_id] == {identities[seat_id]} for seat_id in seats.SEAT_IDS
        )
        ok &= all_seven and carried
        lines.append("  二、聊天室")
        lines.append(
            "    events.jsonl 有發言的席位共 {} 席 {}".format(
                len(spoke), "PASS" if all_seven else "FAIL"
            )
        )
        lines.append(
            "    每一席都在聊天室裡、且用這個市場那一套名字：{}".format(
                "PASS" if carried else "FAIL"
            )
        )

        report = json.loads((run.run_dir / "report.json").read_text(encoding="utf-8"))
        level = report["confidence"]["level"]
        word = pages.CONFIDENCE_WORDS[level]
        history_text = acc.visible_text(bundle["rendered"]["history"])
        detail_text = acc.visible_text(bundle["rendered"]["run_detail"])
        light_ok = word in history_text and word in detail_text
        english_light = acc.english_values_in(history_text, {level})
        ok &= light_ok and english_light == []
        lines.append("  三、燈位")
        lines.append("    report.json 的燈號：{}（畫面上的詞：{}）".format(level, word))
        lines.append(
            "    合併頁與 run 詳情頁都寫這個詞 {}｜英文原值 0 命中 {}".format(
                "PASS" if light_ok else "FAIL",
                "PASS" if english_light == [] else "FAIL",
            )
        )

    write(OUT / "protected-zone.txt", "\n".join(lines) + "\n")
    check("AC7-protected", "保護區三種票數、聊天室、燈位行為與紀錄一致", ok)


# -- 驗收條件 7（後半）：A5 繁中 grep ----------------------------------------


def a5_evidence(markets):
    values = acc.enumerated_data_values()
    lines = [
        "Ticket 09 驗收條件 7：A5 標準（渲染後繁體中文）掃描結果",
        "=" * 72,
        "",
        "讀法：以 html.parser 剖析渲染後的頁面，跳過 <style> 與 <script>，只取文字節點，",
        "不讀任何屬性，再以完整英文單詞邊界搜尋。屬性刻意排除：value=\"tw_stock\" 是表單",
        "送回去的機器值，A5 管的是畫面上讀到的字。",
        "",
        "受掃描的枚舉值（{} 個，全部由權威推導：question.ASSET_CLASSES／".format(len(values)),
        "report_contract.CONFIDENCE_LEVELS／run_index.OUTCOME_STATES／",
        "report_renderer.CONSENSUS_LABELS／debate_state_machine.STANCES_BY_QUESTION_TYPE）：",
        "  " + "、".join(sorted(values)),
        "",
        "唯一豁免（Ticket 04 裁決，窄到只有一個節點）：設定頁 note-confidence.light_scale[*].level",
        "那幾段說明的子樹。Spec R-001 逐字指定該欄說明要列出合法燈色，與 A5 直接衝突，依 Spec",
        "自身衝突原則採 R-001；豁免綁在節點上而不是對整頁字串做替換，所以同一句話出現在別處",
        "照樣會被抓到（tests/test_frontend_redesign_acceptance.py 有四條守衛釘住這件事）。",
        "",
        "依 Spec A5 明文不在此限（屬資料本體）：標的代號、run_id、seat_id、evidence ID。",
        "離線報告版面依 A5 原文不納入本條，列在最後供參。",
        "",
        "-" * 72,
    ]
    ok = True
    for prefix, bundle in markets.items():
        lines.append("")
        lines.append("{}（run_id={}）".format(prefix, bundle["run"].run_id))
        lines.append("")
        for name in sorted(bundle["rendered"]):
            body = bundle["rendered"][name]
            text = acc.scannable_text(body)
            hits = acc.english_values_in(text, values)
            ok &= hits == []
            lines.append(
                "  {:<16} 可見字 {:>6} 字元｜英文枚舉值命中 {}{}".format(
                    name,
                    len(text),
                    len(hits),
                    "" if not hits else "：{}".format(hits),
                )
            )
    # 豁免真的在做事，而且只有那麼窄
    settings_body = markets["tw_stock"]["rendered"]["settings"]
    without = acc.english_values_in(acc.visible_text(settings_body), values)
    with_exemption = acc.english_values_in(acc.scannable_text(settings_body), values)
    exempt_text = set(acc.exempt_notes(settings_body))
    planted_same = acc.english_values_in(
        acc.scannable_text(
            settings_body.replace(
                "</main>", "<p>{}</p></main>".format(sorted(exempt_text)[0])
            )
        ),
        values,
    )
    planted_other = acc.english_values_in(
        acc.scannable_text(settings_body.replace("</main>", "<p>燈號：green</p></main>")),
        values,
    )
    discrimination = acc.english_values_in(
        acc.visible_text("<p>資產類別：tw_stock｜燈號：green｜命中狀態：pending</p>"), values
    )
    marks = [
        ("豁免前的設定頁確實有那五個燈色詞", sorted(without) == ["blue", "green", "orange", "red", "yellow"]),
        ("豁免後設定頁 0 命中", with_exemption == []),
        (
            "豁免掉的正是 Spec R-001 逐字指定的那一句",
            exempt_text == {"這一級對應的燈色（blue／green／yellow／orange／red）"},
        ),
        ("同一句話出現在別的節點照樣被抓到", sorted(planted_same) == ["blue", "green", "orange", "red", "yellow"]),
        ("同一頁其他地方植入 green 照樣被抓到", planted_other == ["green"]),
        ("讀法有能力失敗（植入三個值都抓到）", discrimination == ["green", "pending", "tw_stock"]),
    ]
    lines.append("")
    lines.append("-" * 72)
    lines.append("豁免的鑑別力（同一份讀法，六個方向）")
    for text, good in marks:
        ok &= good
        lines.append("  {:<40} {}".format(text, "PASS" if good else "FAIL"))
    lines.append("  被豁免的字：{}".format(sorted(exempt_text)))
    lines.append("  豁免節點 id：{}".format(acc.exempt_note_ids(settings_body)))

    lines.append("")
    lines.append("-" * 72)
    lines.append("離線兩頁（A5 原文不納入本條驗收，僅供參考）")
    for prefix, bundle in markets.items():
        for name in ("report.html", "debate.html"):
            text = acc.visible_text(bundle["run"].artifact(name))
            hits = acc.english_values_in(text, values)
            lines.append(
                "  {:<10} {:<14} 可見字 {:>6} 字元｜英文枚舉值命中 {}{}".format(
                    prefix, name, len(text), len(hits), "：{}".format(hits) if hits else ""
                )
            )

    write(OUT / "a5-grep.txt", "\n".join(lines) + "\n")
    check("AC7-a5", "A5 標準渲染後繁中 grep 通過（含 Ticket 04 窄豁免判讀）", ok)


if __name__ == "__main__":
    sys.exit(main())
