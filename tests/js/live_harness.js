"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.resolve(__dirname, "../../hoya_market_agents/webapp/static/live.js"),
  "utf8"
);

class Element {
  constructor(tag, id = "") {
    this.tagName = tag;
    this.id = id;
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.className = "";
    this.disabled = false;
    this.hidden = false;
    this._text = "";
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.clientHeight = 100;
    this.animations = [];
  }
  // 頁面上會動的東西由 site.css 決定，而 site.css 有一條 prefers-reduced-motion
  // 規則管住每一條 CSS animation。從 JavaScript 播的 Web Animations 不受那條規則管，
  // 而且回傳的 handle 沒人保存就再也停不下來 —— 所以這裡把它記下來，讓「有沒有偷
  // 播」變成可以斷言的事，而不是一個測不到的差別。
  animate(...args) {
    this.animations.push(args);
    return {cancel: () => {}};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  get firstChild() { return this.children[0] || null; }
  append(child) { this.children.push(child); child.parent = this; }
  appendChild(child) { this.append(child); }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  remove() { if (this.parent) { this.parent.removeChild(this); } }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, event = {}) { (this.listeners[name] || []).forEach((fn) => fn(event)); }
  requestSubmit() { this.emit("submit", {preventDefault() {}}); }
}

class FakeEventSource {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  emit(name, payload) {
    this.listeners[name]({data: JSON.stringify(payload)});
  }
  close() { this.closed = true; }
}

function response(status, body) {
  return Promise.resolve({status, json: () => Promise.resolve(body)});
}

// A reply that never arrives: the request left and the answer did not come back.
// 用 function 包起來是因為 rejection 必須在 fetch 真的被呼叫的那一刻才建立 ——
// 提早建好會有一段沒有人接的空窗，Node 會當成 unhandled rejection 把整個 harness 拉掉。
function transportFailure() {
  return () => Promise.reject(new Error("status request did not come back"));
}

// 伺服器替每一格 run-bound surface 標上「對一個還不知道任何事的 run，這一格讀什麼」
// （pages.live_page._fresh_run_words）。換 run 的 frame 只帶 feed、席位、票數、輪次
// 與時鐘，其餘每一格都還是上一場畫上去的，沒有任何 frame 會更正它們，所以清成標記
// 的字是 client 唯一能做對的事。這裡的字面值只是伺服器標了什麼的樣本；哪些字是權威
// 由 RunLocalResetSurfaceTest 綁在 Python 那一側。
const RESET_WORDS = {
  "live-question": "等待新的市場題目",
  "live-round": "尚未進入辯論",
  "focus-asset": "市場",
  "focus-headline": "尚未形成單一領先",
  "focus-tally": "票數尚未開始累計",
  "focus-detail": "⚪ 信心尚未評估",
  "focus-action": "允許可信二手來源",
  "live-phase": "開始多方蒐證",
  "live-threshold": "尚未進入投票",
  "live-tally": "",
  "tally-note": "尚未開始投票。",
  "live-seats": "",
  "rules-detail-body": "規則時間線將在新的一場開始後顯示。",
  "vote-history-detail-body": "尚未投票。",
  "evidence-panel-body": "證據將在證據快照封存後顯示。",
};

function makeEnvironment(replies, initialRun = "run-a") {
  FakeEventSource.instances = [];
  const ids = {};
  const add = (id, tag = "div") => (ids[id] = new Element(tag, id));
  const filled = (id, tag, text) => { add(id, tag).textContent = text; return ids[id]; };
  const panel = (id, text) => {
    const body = add(id);
    body.append(new Element("p"));
    body.children[0].textContent = text;
    return body;
  };
  add("launch-form", "form");
  add("launch-submit", "button");
  add("launch-status", "span");
  add("run-picker", "select").value = initialRun;
  add("live-run-id", "code").textContent = initialRun || "尚未選定";
  add("live-report-link", "a").textContent = "市場報告";
  ids["live-report-link"].href = "/run/" + initialRun + "/report.html";
  add("live-debate-link", "a").textContent = "完整辯論";
  ids["live-debate-link"].href = "/run/" + initialRun + "/debate.html";
  add("live-feed").dataset = {runId: initialRun, cursor: initialRun ? initialRun + "@3" : ""};
  add("live-connection");
  add("live-state").dataset.state = "running";
  add("live-round");
  add("live-elapsed").dataset.elapsedMs = "100";
  add("live-tally");
  add("live-seats");
  add("live-outcome");
  add("live-total-remaining").dataset = {
    countdownFrom: "2857000", durationMs: "1020000"
  };
  ids["live-total-remaining"].textContent = "47:36";
  add("live-debate-remaining").dataset.remainingMs = "5000";
  add("feed-jump", "button");
  // Every other surface the room paints for one run. They carry the run being
  // watched when the page was drawn, which is what a switch has to get rid of.
  filled("live-question", "p", "上一場的題目：ETH 未來七天會不會漲");
  filled("focus-asset", "p", "ETH");
  filled("focus-headline", "span", "目前看多領先");
  filled("focus-tally", "span", "看多 4｜看空 1｜觀望 2");
  filled("focus-detail", "p", "🟢 高信心");
  filled("focus-action", "a", "第三輪投票");
  filled("live-phase", "strong", "封存後公開辯論");
  filled("live-threshold", "strong", "門檻 5 票");
  filled("tally-note", "p", "上一場已經投完。");
  panel("rules-detail-body", "T+09:00 第二輪投票（門檻 4 票）");
  panel("vote-history-detail-body", "T+03:00 現貨技術 首次表態：看多");
  panel("evidence-panel-body", "E-1 上一場封存的證據");
  Object.keys(RESET_WORDS).forEach((id) => {
    assert(ids[id], "the fixture is missing marked surface " + id);
    ids[id].dataset.reset = RESET_WORDS[id];
  });
  ids["live-feed"].append(new Element("article"));
  ids["live-feed"].children[0].textContent = "上一場的發言";
  ids["live-tally"].append(new Element("div"));
  ids["live-tally"].children[0].textContent = "看多 4";
  ids["live-seats"].append(new Element("article"));
  ids["live-seats"].children[0].textContent = "上一場的席位";
  ids["live-round"].textContent = "第 3 輪";
  ids["live-connection"].textContent = "連線中斷，正在重連";
  ids["feed-jump"].hidden = false;
  const historyCalls = [];
  const intervals = [];
  const intervalErrors = [];
  const document = {
    getElementById: (id) => ids[id] || null,
    createElement: (tag) => new Element(tag),
    createTextNode: (text) => { const node = new Element("text"); node.textContent = text; return node; },
    querySelector: (selector) => {
      if (selector === ".feed-empty") {
        return ids["live-feed"].children.find((child) => child.className === "feed-empty") || null;
      }
      return null;
    },
    // 標記是伺服器下的，所以 client 只能照標記找，不能自己收一份 id 清單 —— 那份
    // 清單會跟頁面各走各的。
    querySelectorAll: (selector) => {
      assert.strictEqual(selector, "[data-reset]", "unexpected selector " + selector);
      return Object.values(ids).filter((node) => node.dataset.reset !== undefined);
    },
  };
  Object.values(ids).forEach((node) => {
    node.replaceWith = (replacement) => {
      replacement.replaceWith = node.replaceWith;
      ids[node.id] = replacement;
    };
  });
  const calls = [];
  const context = {
    document,
    EventSource: FakeEventSource,
    FormData: class { *[Symbol.iterator]() { yield ["question", "BTC?"]; } },
    URLSearchParams,
    fetch: (url, options = {}) => {
      calls.push({url, options});
      assert(replies.length, "unexpected fetch " + url);
      const reply = replies.shift();
      return typeof reply === "function" ? reply() : reply;
    },
    history: {replaceState: (_a, _b, url) => historyCalls.push(url)},
    location: {search: ""},
    window: {
      setTimeout: (fn) => { Promise.resolve().then(fn); return 1; },
      setInterval: (fn) => {
        intervals.push(() => {
          try { fn(); } catch (error) { intervalErrors.push(error); }
        });
        return 1;
      },
    },
    console,
    encodeURIComponent,
    JSON,
    Number,
  };
  vm.runInNewContext(source, context, {filename: "production-live.js"});
  return {ids, calls, historyCalls, intervals, intervalErrors};
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function launchedScenario() {
  const env = makeEnvironment([
    response(202, {status: "pending", launch_token: "token-a"}),
    response(200, {status: "pending"}),
    response(200, {status: "launched", run_id: "run-b"}),
  ]);
  env.ids["live-state"].dataset.state = "finished";
  env.ids["live-state"].textContent = "已完成";
  env.ids["live-outcome"].append(new Element("a"));
  env.ids["live-outcome"].children[0].textContent = "查看舊報告";
  const original = FakeEventSource.instances[0];
  assert.strictEqual(original.url, "/live/events?run=run-a&after=run-a%403");

  env.ids["launch-form"].requestSubmit();
  env.ids["launch-form"].requestSubmit();
  // 等待動畫是 site.css 的一條 class 規則（#launch-status.launch-pending），不是這裡
  // 播的。site.css 已經有一條 prefers-reduced-motion 規則管住每一條 CSS animation；
  // 從 JavaScript 播的 Web Animations 繞過那條規則，而且每送出一次就多疊一個沒有人
  // 保存 handle、因此再也停不下來的無限迴圈。
  assert.strictEqual(env.ids["launch-status"].textContent, "啟動中…");
  assert.strictEqual(env.ids["launch-status"].className, "launch-pending");
  assert.deepStrictEqual(env.ids["launch-status"].animations, []);
  await flush(); await flush(); await flush();

  assert.strictEqual(env.calls.filter((call) => call.url === "/launch").length, 1);
  assert.strictEqual(original.closed, true);
  const stream = FakeEventSource.instances.at(-1);
  assert.strictEqual(stream.url, "/live/events?run=run-b");
  assert.strictEqual(env.historyCalls.at(-1), "/live?run=run-b");
  assert.strictEqual(env.ids["live-state"].dataset.state, "running");
  assert.strictEqual(env.ids["live-state"].textContent, "進行中");
  assert.strictEqual(env.ids["live-outcome"].textContent, "");
  assert.strictEqual(env.ids["live-run-id"].textContent, "run-b");
  assert.strictEqual(env.ids["launch-submit"].disabled, false);
  assert.strictEqual(env.ids["launch-status"].textContent, "");
  // 動畫跟著等待狀態一起結束：class 拿掉，格子就不再閃。
  assert.strictEqual(env.ids["launch-status"].className, "");
  assert.deepStrictEqual(env.ids["launch-status"].animations, []);
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "17:00");
  assert.strictEqual(env.ids["run-picker"].value, "");
  assert.strictEqual(env.ids["live-report-link"]["aria-disabled"], "true");
  assert.strictEqual(env.ids["live-debate-link"]["aria-disabled"], "true");
  // 這一場換到 run-b，但畫面上每一格非 feed／席位／票數／輪次／時鐘的欄位都還是
  // run-a 的答案，而 run-b 的 frame 不會提到它們。清成伺服器標記的字之前，題目、
  // 領先立場、信心、階段、門檻、規則、票數變化與證據都還在講上一場。
  Object.keys(RESET_WORDS).forEach((id) => {
    assert.strictEqual(env.ids[id].textContent, RESET_WORDS[id], id);
  });
  env.intervals[0]();
  assert.deepStrictEqual(env.intervalErrors, []);

  stream.emit("snapshot", {
    run_id: "run-b", messages: [], tally: [], seats: [], elapsed_ms: 1000,
    debate_started: false, debate_start_remaining_ms: 1, cursor: "run-b@1"
  });
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "00:01");
  stream.emit("error");
  assert.strictEqual(env.ids["live-connection"].textContent, "連線中斷，正在重連");
  stream.emit("open");
  assert.strictEqual(env.ids["live-connection"].textContent, "直播連線中");
  assert.strictEqual(stream.url, "/live/events?run=run-b");
  stream.emit("append", {
    run_id: "run-b", messages: [], tally: [], seats: [], elapsed_ms: 900,
    debate_started: false, debate_start_remaining_ms: 5000, cursor: "run-b@2"
  });
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "1000");
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "00:01");
  assert.strictEqual(env.ids["live-state"].dataset.state, "running");
  assert.strictEqual(env.ids["live-state"].textContent, "進行中");
  // 這一場只產出報告，沒有完整辯論記錄：completion 帶 report_href，debate_href 是
  // null。elapsed 是 manifest 凍結的權威值。
  stream.emit("done", {
    run_id: "run-b", messages: [], tally: [], seats: [], elapsed_ms: 812345,
    debate_started: true, debate_start_remaining_ms: null,
    completion: {report_href: "/run/run-b/report.html", debate_href: null}
  });
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "辯論已開始");
  assert.strictEqual(env.ids["live-outcome"].textContent, "分析完成　查看市場報告");
  assert.strictEqual(env.ids["live-outcome"].children[0].children[0].href, "/run/run-b/report.html");
  // 收到 done 就停錶，停在「總時程減掉這一刻被接受的權威 elapsed」——
  // 17:00 - 13:32.345 = 03:27，和重新整理同一個 finalized run 讀到的是同一個數字。
  // 直接寫 00:00 會宣稱十七分鐘用完了，而這一場其實提早結束。
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "03:27");
  assert.strictEqual(env.ids["live-report-link"].href, "/run/run-b/report.html");
  assert.strictEqual(env.ids["live-report-link"]["aria-disabled"], undefined);
  // 沒有寫出來的檔案不能給連結：那個分頁會開出一個披著本場結論外皮的 404。
  assert.strictEqual(env.ids["live-debate-link"].href, undefined);
  assert.strictEqual(env.ids["live-debate-link"]["aria-disabled"], "true");
  assert.strictEqual(stream.closed, true);
  // 停錶之後 interval 不能再走，重新整理才會對得上。
  env.intervals[0]();
  assert.deepStrictEqual(env.intervalErrors, []);
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "03:27");
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "812345");
}

// 換 run 時舊的 EventSource 會被 close，但瀏覽器仍可能把已經排隊的事件送進舊
// handler。這一場證明舊 run 的 snapshot／done／error 不能碰現在這個 run 的畫面，
// 而 run-b 自己完成時同一組欄位仍要正確換成 run-b 的值。
async function staleSourceCannotPaintTheCurrentRun() {
  const env = makeEnvironment([
    response(202, {status: "pending", launch_token: "token-a"}),
    response(200, {status: "launched", run_id: "run-b"}),
  ]);
  const stale = FakeEventSource.instances[0];
  assert.strictEqual(stale.url, "/live/events?run=run-a&after=run-a%403");

  env.ids["launch-form"].requestSubmit();
  await flush(); await flush(); await flush(); await flush();

  const live = FakeEventSource.instances.at(-1);
  assert.notStrictEqual(live, stale);
  assert.strictEqual(live.url, "/live/events?run=run-b");
  assert.strictEqual(stale.closed, true);

  live.emit("open");
  live.emit("snapshot", {
    run_id: "run-b", messages: [], tally: [], seats: [], elapsed_ms: 2000,
    debate_started: false, debate_start_remaining_ms: 60000, cursor: "run-b@1"
  });
  assert.strictEqual(env.ids["live-connection"].textContent, "直播連線中");
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "01:00");

  stale.emit("error");
  stale.emit("open");
  stale.emit("snapshot", {
    run_id: "run-a", messages: [], tally: [], seats: [], elapsed_ms: 900000,
    debate_started: true, debate_start_remaining_ms: null, cursor: "run-a@9"
  });
  stale.emit("done", {
    run_id: "run-a", messages: [], tally: [], seats: [], elapsed_ms: 1020000,
    debate_started: true, debate_start_remaining_ms: null,
    outcome: {
      confidence_level: "高信心", consensus_label: "看多", run_href: "/run/run-a"
    },
    completion: {report_href: "/run/run-a/report.html"}
  });

  assert.strictEqual(env.ids["live-state"].dataset.state, "running");
  assert.strictEqual(env.ids["live-state"].textContent, "進行中");
  assert.strictEqual(env.ids["live-outcome"].textContent, "");
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "17:00");
  assert.strictEqual(env.ids["live-report-link"]["aria-disabled"], "true");
  assert.strictEqual(env.ids["live-debate-link"]["aria-disabled"], "true");
  assert.strictEqual(env.ids["live-report-link"].href, undefined);
  assert.strictEqual(env.ids["live-debate-link"].href, undefined);
  assert.strictEqual(env.ids["live-connection"].textContent, "直播連線中");
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "2000");
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "01:00");
  assert.strictEqual(env.ids["live-feed"].dataset.cursor, "run-b@1");
  assert.strictEqual(env.ids["live-run-id"].textContent, "run-b");

  // 死掉的連線不能靠 payload 內容過關：沒有 run_id 的 frame 也一樣不准畫。
  stale.emit("done", {
    messages: [], tally: [], seats: [], elapsed_ms: 1020000,
    debate_started: true, debate_start_remaining_ms: null,
    completion: {report_href: "/run/run-a/report.html"}
  });
  assert.strictEqual(env.ids["live-state"].dataset.state, "running");
  assert.strictEqual(env.ids["live-outcome"].textContent, "");
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "17:00");
  assert.strictEqual(env.ids["live-report-link"]["aria-disabled"], "true");
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "2000");

  // 反過來，現在這條連線送來別的 run（例如 server 用 latest 猜錯）也不准畫，
  // 而且不能把還在跑的連線關掉。
  live.emit("done", {
    run_id: "run-z", messages: [], tally: [], seats: [], elapsed_ms: 1020000,
    debate_started: true, debate_start_remaining_ms: null,
    completion: {report_href: "/run/run-z/report.html"}
  });
  assert.strictEqual(env.ids["live-state"].dataset.state, "running");
  assert.strictEqual(env.ids["live-outcome"].textContent, "");
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "17:00");
  assert.strictEqual(env.ids["live-report-link"]["aria-disabled"], "true");
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "2000");
  assert.strictEqual(env.ids["live-run-id"].textContent, "run-b");
  assert.strictEqual(live.closed, false);

  // 這一場兩份記錄都寫出來了，所以兩個分頁都指向 manifest 綁住的那兩個檔案。
  live.emit("done", {
    run_id: "run-b", messages: [], tally: [], seats: [], elapsed_ms: 3000,
    debate_started: true, debate_start_remaining_ms: null,
    completion: {
      report_href: "/run/run-b/report.html",
      debate_href: "/run/run-b/debate.html"
    }
  });
  assert.strictEqual(env.ids["live-state"].dataset.state, "finished");
  assert.strictEqual(env.ids["live-state"].textContent, "已完成");
  assert.strictEqual(env.ids["live-outcome"].textContent, "分析完成　查看市場報告");
  assert.strictEqual(
    env.ids["live-outcome"].children[0].children[0].href, "/run/run-b/report.html"
  );
  assert.strictEqual(env.ids["live-total-remaining"].textContent, "16:57");
  assert.strictEqual(env.ids["live-report-link"].href, "/run/run-b/report.html");
  assert.strictEqual(env.ids["live-debate-link"].href, "/run/run-b/debate.html");
  assert.strictEqual(env.ids["live-debate-link"]["aria-disabled"], undefined);
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "3000");
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "辯論已開始");
  assert.strictEqual(live.closed, true);
}

async function failureAndRetryScenario() {
  const env = makeEnvironment([
    response(202, {status: "pending", launch_token: "bad"}),
    response(200, {status: "failed", reason: "Provider 不可用"}),
    response(202, {status: "pending", launch_token: "good"}),
    response(200, {status: "launched", run_id: "run-c"}),
  ], "");
  env.ids["launch-form"].requestSubmit();
  await flush(); await flush(); await flush(); await flush();
  const statusChildren = env.ids["launch-status"].children;
  assert.strictEqual(statusChildren[0].textContent, "啟動失敗：Provider 不可用　");
  // 這一次啟動有了結論，所以等待動畫停了、表單也還回來了。
  assert.strictEqual(env.ids["launch-status"].className, "");
  assert.deepStrictEqual(env.ids["launch-status"].animations, []);
  assert.strictEqual(env.ids["launch-submit"].disabled, false);
  const retry = statusChildren.at(-1);
  assert.strictEqual(retry.textContent, "[重試]");
  retry.emit("click");
  await flush(); await flush(); await flush(); await flush();
  assert.strictEqual(env.calls.filter((call) => call.url === "/launch").length, 2);
  assert.strictEqual(FakeEventSource.instances.at(-1).url, "/live/events?run=run-c");
  assert.strictEqual(env.ids["launch-status"].className, "");
  assert.deepStrictEqual(env.ids["launch-status"].animations, []);
}

// 一次 status request 沒回來，不代表這次 launch 失敗：token 已經核發，child 很可能正在
// 跑。這時候重新 POST /launch 只會換到 busy 409 —— 而原本那個 token 從此沒有人再問，
// 頁面永遠拿不到精確 run_id，那一場就變成沒有人在看的 run。所以讀不到狀態的重試是拿
// 同一個 token 繼續問，不是重新送一次。
async function aStatusTransportFailureKeepsTheIssuedToken() {
  const env = makeEnvironment([
    response(202, {status: "pending", launch_token: "token-a"}),
    transportFailure(),
    response(200, {status: "launched", run_id: "run-b"}),
  ], "");

  env.ids["launch-form"].requestSubmit();
  await flush(); await flush(); await flush();

  // 這次啟動還沒有結論，所以表單還握在手上，沒有人會以為可以重送。
  assert.strictEqual(env.ids["launch-submit"].disabled, true);
  const stalled = env.ids["launch-status"].children;
  assert.strictEqual(
    stalled[0].textContent, "暫時讀不到啟動狀態，啟動可能仍在進行。　"
  );
  assert.strictEqual(env.ids["launch-status"].className, "");
  const retry = stalled.at(-1);
  assert.strictEqual(retry.textContent, "[重試]");

  // 停在這個狀態上再送一次表單，也不會有第二個 /launch。
  env.ids["launch-form"].requestSubmit();
  await flush();
  assert.strictEqual(env.calls.filter((call) => call.url === "/launch").length, 1);

  retry.emit("click");
  assert.strictEqual(env.ids["launch-status"].textContent, "啟動中…");
  assert.strictEqual(env.ids["launch-status"].className, "launch-pending");
  await flush(); await flush(); await flush();

  assert.deepStrictEqual(env.calls.map((call) => call.url), [
    "/launch",
    "/launch/status?token=token-a",
    "/launch/status?token=token-a",
  ]);
  assert.strictEqual(FakeEventSource.instances.at(-1).url, "/live/events?run=run-b");
  assert.strictEqual(env.ids["live-run-id"].textContent, "run-b");
  assert.strictEqual(env.ids["launch-submit"].disabled, false);
  assert.strictEqual(env.ids["launch-status"].textContent, "");
  assert.strictEqual(env.ids["launch-status"].className, "");
  assert.deepStrictEqual(env.ids["launch-status"].animations, []);
}

// 「辯論已開始」是一句不能收回的話。同一條連線、同一場上，一個較舊的 frame 追上來說
// 還在倒數 —— 倒數是「還沒開始」的講法。讓它回來就是告訴讀者辯論還沒開始，而它已經開始
// 了，之後也不會有任何 frame 來更正這一格。
async function theStartedLatchNeverFallsBackToACountdown() {
  const env = makeEnvironment([]);
  const stream = FakeEventSource.instances[0];
  assert.strictEqual(stream.url, "/live/events?run=run-a&after=run-a%403");
  const frame = (extra) => Object.assign(
    {run_id: "run-a", messages: [], tally: [], seats: [], elapsed_ms: 1000}, extra
  );

  stream.emit("snapshot", frame({
    debate_started: false, debate_start_remaining_ms: 4000, cursor: "run-a@4"
  }));
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "00:04");

  stream.emit("append", frame({
    elapsed_ms: 2000, debate_started: true, debate_start_remaining_ms: null,
    cursor: "run-a@5"
  }));
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "辯論已開始");
  assert.strictEqual(env.ids["live-debate-remaining"].dataset.remainingMs, "");

  // 同一個 source、同一場，帶著 started=false 和一個還在數的 remaining。
  stream.emit("append", frame({
    elapsed_ms: 1500, debate_started: false, debate_start_remaining_ms: 3000,
    cursor: "run-a@6"
  }));
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "辯論已開始");
  assert.strictEqual(env.ids["live-debate-remaining"].dataset.remainingMs, "");
  assert.strictEqual(env.ids["live-elapsed"].dataset.elapsedMs, "2000");

  // 另一種舊 frame：根本沒提 debate_started，只帶一個更大的 remaining。
  stream.emit("append", frame({
    elapsed_ms: 1200, debate_start_remaining_ms: 9000, cursor: "run-a@7"
  }));
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "辯論已開始");
  assert.strictEqual(env.ids["live-debate-remaining"].dataset.remainingMs, "");

  // 每秒走一格的 interval 也不准把倒數叫回來。
  env.intervals[0]();
  assert.deepStrictEqual(env.intervalErrors, []);
  assert.strictEqual(env.ids["live-debate-remaining"].textContent, "辯論已開始");
  assert.strictEqual(env.ids["live-debate-remaining"].dataset.remainingMs, "");
}

async function busyScenario() {
  const env = makeEnvironment([
    response(409, {status: "busy", reason: "已有 run 執行中"}),
  ], "");
  env.ids["launch-form"].requestSubmit();
  await flush(); await flush();
  assert.strictEqual(
    env.ids["launch-status"].children[0].textContent,
    "啟動失敗：已有 run 執行中　"
  );
}

async function multilineFailureIsRenderedAsOneLine() {
  const env = makeEnvironment([
    response(400, {status: "failed", reason: "第一行\n第二行\t細節"}),
  ], "");
  env.ids["launch-form"].requestSubmit();
  await flush(); await flush();
  assert.strictEqual(
    env.ids["launch-status"].children[0].textContent,
    "啟動失敗：第一行 第二行 細節　"
  );
}

Promise.resolve()
  .then(launchedScenario)
  .then(staleSourceCannotPaintTheCurrentRun)
  .then(failureAndRetryScenario)
  .then(aStatusTransportFailureKeepsTheIssuedToken)
  .then(theStartedLatchNeverFallsBackToACountdown)
  .then(busyScenario)
  .then(multilineFailureIsRenderedAsOneLine)
  .then(() => console.log("LIVE_JS_VM_EXECUTED: submit pending launched busy failed retry one-line-error status-transport-retry css-pending-animation started-latch-no-downgrade snapshot append done reconnect run-switch state-reset stale-source-gate run-local-reset frozen-total-remaining exact-artifact-links"))
  .catch((error) => { console.error(error); process.exitCode = 1; });
