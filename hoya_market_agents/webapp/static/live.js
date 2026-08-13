
(function () {
  "use strict";
  var launchForm = document.getElementById("launch-form");
  var launchSubmit = document.getElementById("launch-submit");
  var launchStatus = document.getElementById("launch-status");
  var launchPending = false;
  // 等待動畫掛在這個 class 上，由 site.css 播。不從這裡播是因為 site.css 已經有一條
  // prefers-reduced-motion 規則管住頁面上每一條 CSS animation，而 JavaScript 播的
  // Web Animations 不在那條規則管得到的地方 —— 它還會每送出一次就多留一個沒有人保存
  // handle、因此再也停不下來的無限迴圈。class 沒有 handle 可以遺失：拿掉就停。
  var PENDING_CLASS = "launch-pending";

  function oneLine(value, fallback) {
    var line = String(value || fallback || "").replace(/\s+/g, " ").trim();
    return line || fallback;
  }
  function paintLaunch(words, className) {
    if (!launchStatus) { return; }
    launchStatus.textContent = words;
    launchStatus.className = className;
  }
  function holdLaunchForm(held) {
    launchPending = held;
    if (launchSubmit) { launchSubmit.disabled = held; }
  }
  function showLaunchPending() {
    holdLaunchForm(true);
    paintLaunch("啟動中…", PENDING_CLASS);
  }
  function showLaunchReady() {
    holdLaunchForm(false);
    paintLaunch("", "");
  }
  function showLaunchNotice(sentence, retry) {
    paintLaunch("", "");
    if (!launchStatus) { return; }
    launchStatus.append(document.createTextNode(sentence + "　"));
    var button = el("button", "", "[重試]");
    button.type = "button";
    button.addEventListener("click", retry);
    launchStatus.append(button);
  }
  // 這次要求已經有結論，而結論是它沒有（或不再有）自己的 run。表單還回來，重試就是
  // 重新送一次。
  function showLaunchError(reason) {
    holdLaunchForm(false);
    showLaunchNotice("啟動失敗：" + oneLine(reason, "啟動程序失敗。"), function () {
      launchForm.requestSubmit();
    });
  }
  // 讀不到狀態不是啟動失敗：token 已經核發，child 很可能正在跑。這時候重新 POST
  // /launch 只會換到 busy 409，而原本那個 token 從此沒有人再問 —— 頁面永遠拿不到精確
  // run_id，那一場就變成沒有人在看的 run。所以表單不還，重試是拿同一個 token 繼續問。
  function showLaunchStalled(token) {
    showLaunchNotice("暫時讀不到啟動狀態，啟動可能仍在進行。", function () {
      showLaunchPending();
      pollLaunch(token);
    });
  }
  // 兩條路都要「HTTP 狀態碼和 body 一起看」才答得出事情：202 配 pending 是核發了
  // token，409 配 busy 是這台伺服器已經有一場在跑。所以兩邊讀的是同一個形狀。
  function withBody(response) {
    return response.json().then(function (body) {
      return {status: response.status, body: body};
    });
  }
  // catch 只包住「把答案拿回來」這一段，所以 null 的意思很窄：這一趟沒有帶回任何關於
  // 這個 token 的說法。伺服器真的說了話 —— unknown、failed —— 是下面那個函式的事。
  function pollLaunch(token) {
    fetch("/launch/status?token=" + encodeURIComponent(token), {cache: "no-store"})
      .then(withBody)
      .catch(function () { return null; })
      .then(function (reply) {
        if (reply === null) { showLaunchStalled(token); return; }
        readLaunchStatus(token, reply);
      });
  }
  function readLaunchStatus(token, reply) {
    if (reply.status === 404 || reply.body.status === "unknown") {
      showLaunchError("找不到這次啟動狀態。");
    } else if (reply.body.status === "pending") {
      window.setTimeout(function () { pollLaunch(token); }, 250);
    } else if (reply.body.status === "launched") {
      showLaunchReady();
      connectRun(reply.body.run_id);
    } else {
      showLaunchError(reply.body.reason || "啟動程序失敗。");
    }
  }
  if (launchForm) {
    launchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (launchPending) { return; }
      showLaunchPending();
      fetch("/launch", {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
        },
        body: new URLSearchParams(new FormData(launchForm)).toString()
      }).then(withBody).then(function (result) {
        if (result.status === 202 && result.body.status === "pending") {
          pollLaunch(result.body.launch_token);
        } else {
          showLaunchError(result.body.reason || "啟動要求未被接受。");
        }
        // 這一趟根本沒送出去，所以沒有 token 可以續問 —— 重試只能重新送一次。
      }).catch(function () { showLaunchError("無法送出啟動要求。"); });
    });
  }
  var picker = document.getElementById("run-picker");
  if (picker) {
    picker.addEventListener("change", function () {
      if (picker.value) { location.search = "?run=" + encodeURIComponent(picker.value); }
    });
  }
  var feed = document.getElementById("live-feed");
  if (!feed || typeof EventSource === "undefined") { return; }
  var connection = document.getElementById("live-connection");
  var stateBox = document.getElementById("live-state");
  var roundBox = document.getElementById("live-round");
  var elapsedBox = document.getElementById("live-elapsed");
  var tallyBox = document.getElementById("live-tally");
  var seatBox = document.getElementById("live-seats");
  var outcomeBox = document.getElementById("live-outcome");
  var currentRunBox = document.getElementById("live-run-id");
  var reportLink = document.getElementById("live-report-link");
  var debateLink = document.getElementById("live-debate-link");
  var totalRemaining = document.getElementById("live-total-remaining");
  var debateRemaining = document.getElementById("live-debate-remaining");
  var focusTally = document.querySelector(".focus-bar .focus-tally");
  var jump = document.getElementById("feed-jump");
  var stream = null;
  var activeRun = feed.dataset.runId || null;
  var currentElapsed = Number(elapsedBox && elapsedBox.dataset.elapsedMs || 0);
  var currentDebateRemaining = debateRemaining && debateRemaining.dataset.remainingMs !== ""
    ? Number(debateRemaining.dataset.remainingMs) : null;
  var debateStarted = currentDebateRemaining === null;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function clock(ms) {
    var seconds = Math.max(0, Math.floor((ms || 0) / 1000));
    var minutes = Math.floor(seconds / 60);
    return ("0" + minutes).slice(-2) + ":" + ("0" + (seconds % 60)).slice(-2);
  }
  function countdownClock(ms) {
    var seconds = Math.max(1, Math.ceil(Number(ms) / 1000));
    var minutes = Math.floor(seconds / 60);
    return ("0" + minutes).slice(-2) + ":" + ("0" + (seconds % 60)).slice(-2);
  }
  function clear(node) {
    while (node && node.firstChild) { node.removeChild(node.firstChild); }
  }
  // 跟 pages._message 同兩種形狀：概述等於全文就一段，不相等就摺起來。概述是
  // 伺服器算好送來的，這裡不斷句，只把全文扣掉概述已經顯示的部分；概述被字數
  // 上限截斷時結尾補的 … 是記號不是原文，所以要扣掉它才接得回全文。
  function reason(item) {
    var label = el("strong", "", "判斷／挑戰理由：");
    if (item.public_brief === item.public_reason) {
      var one = el("p", "message-reason");
      one.append(label);
      one.append(document.createTextNode(item.public_reason));
      return one;
    }
    var folded = el("details", "message-reason");
    var summary = el("summary");
    var brief = item.public_brief;
    var truncated = brief.slice(-1) === "…";
    summary.append(label);
    summary.append(document.createTextNode(truncated ? brief.slice(0, -1) : brief));
    if (truncated) { summary.append(el("span", "reason-ellipsis", "…")); }
    summary.append(el("span", "reason-hint", "顯示全文"));
    summary.append(el("span", "reason-fold", "收合"));
    folded.append(summary);
    folded.append(el("p", "", reasonTail(item)));
    return folded;
  }
  function reasonTail(item) {
    var brief = item.public_brief;
    var shown = brief.slice(-1) === "…" ? brief.length - 1 : brief.length;
    return item.public_reason.slice(shown);
  }
  function message(item) {
    var card = el("article", "message " + item.provider);
    card.dataset.seq = String(item.seq);
    var head = el("div", "message-head");
    var speaker = el("div", "speaker");
    var avatar = el("span", "speaker-avatar", item.avatar);
    avatar.setAttribute("aria-hidden", "true");
    speaker.append(avatar);
    var names = el("div");
    names.append(el("strong", "", item.agent_name));
    names.append(el("small", "", item.agent_number + "｜" + item.seat_label));
    speaker.append(names);
    head.append(speaker);
    var meta = el("div", "message-meta");
    meta.append(el("span", "badge " + item.stance_class, item.stance_label));
    meta.append(el("time", "", "T+" + clock(item.elapsed_ms)));
    head.append(meta);
    card.append(head);
    card.append(reason(item));
    if (item.change_label) {
      var change = el("p", "stance-change" + (item.changed ? " changed" : ""));
      change.append(el("strong", "", "是否變更立場："));
      change.append(document.createTextNode(item.change_label));
      card.append(change);
    }
    if (item.evidence_ids && item.evidence_ids.length) {
      var ev = el("p", "message-evidence");
      ev.append(el("strong", "", "引用證據："));
      ev.append(document.createTextNode(item.evidence_ids.join("、")));
      card.append(ev);
    }
    return card;
  }
  function pinned() {
    return feed.scrollHeight - feed.scrollTop - feed.clientHeight < 64;
  }
  function appendMessages(messages) {
    if (!messages.length) { return; }
    var empty = document.getElementById("feed-empty");
    if (empty) { empty.remove(); }
    var wasPinned = pinned();
    messages.forEach(function (item) { feed.append(message(item)); });
    if (wasPinned) {
      feed.scrollTop = feed.scrollHeight;
      if (jump) { jump.hidden = true; }
    } else if (jump) {
      jump.hidden = false;
    }
  }
  function drawTally(entries) {
    if (!tallyBox || !entries) { return; }
    clear(tallyBox);
    entries.forEach(function (entry) {
      var cell = el("div", entry["class"]);
      cell.append(el("span", "tally-label", entry.label));
      cell.append(el("strong", "", entry.count));
      tallyBox.append(cell);
    });
    syncFocus(entries);
  }
  function syncFocus(entries) {
    if (!focusTally || !entries.length) { return; }
    focusTally.textContent = entries.map(function (entry) {
      return entry.label + " " + entry.count;
    }).join("｜");
  }
  function tick(box) {
    if (!box) { return; }
    var from = Number(box.dataset.countdownFrom || 0);
    var elapsed = Number(elapsedBox && elapsedBox.dataset.elapsedMs || 0);
    box.textContent = clock(Math.max(0, from - elapsed));
  }
  function drawSeats(seats) {
    if (!seatBox || !seats) { return; }
    clear(seatBox);
    seats.forEach(function (seat) {
      var card = el("article", "agent " + seat.provider);
      card.dataset.seatId = seat.seat_id;
      var headline = el("div", "agent-head");
      var avatar = el("span", "avatar", seat.avatar);
      avatar.setAttribute("aria-hidden", "true");
      headline.append(avatar);
      var names = el("div");
      names.append(el("h3", "", seat.agent_name));
      names.append(el("small", "", seat.agent_number + "｜" + seat.seat_label));
      headline.append(names);
      headline.append(el("p", "stance " + seat.stance_class, seat.stance_label));
      headline.append(el("span", "status", seat.status));
      card.append(headline);
      // 說明跟名稱同在這一個 seat 物件裡，所以重畫永遠是整組換，配不出
      // 「這一趟的名字＋上一趟的說明」。
      if (seat.seat_blurb) { card.append(el("p", "agent-blurb", seat.seat_blurb)); }
      seatBox.append(card);
    });
  }
  function drawOutcome(outcome) {
    if (!outcomeBox || !outcome) { return; }
    clear(outcomeBox);
    var line = el("p", "focus-detail");
    line.append(el("span", "", "燈號 " + (outcome.confidence_level || "—")));
    line.append(el("span", "", "　" + (outcome.consensus_label || "—")));
    outcomeBox.append(line);
    var link = document.createElement("a");
    link.href = outcome.run_href;
    link.textContent = "開啟這一場的 run 詳情";
    var holder = el("p");
    holder.append(link);
    outcomeBox.append(holder);
  }
  function drawCompletion(completion) {
    if (!outcomeBox || !completion) { return; }
    clear(outcomeBox);
    var line = el("p", "", "分析完成　");
    var link = el("a", "", "查看市場報告");
    link.href = completion.report_href;
    line.append(link);
    outcomeBox.append(line);
  }
  function artifactLink(node, href) {
    if (!node) { return null; }
    var replacement = el(href ? "a" : "span", "", node.textContent);
    replacement.id = node.id;
    if (href) {
      replacement.href = href;
    } else {
      replacement.setAttribute("role", "link");
      replacement.setAttribute("aria-disabled", "true");
    }
    node.replaceWith(replacement);
    return replacement;
  }
  function resetArtifactLinks() {
    reportLink = artifactLink(reportLink, null);
    debateLink = artifactLink(debateLink, null);
  }
  // 兩個分頁開哪個檔案，是 completion 說的，不是這裡拼的。completion 只列出這一場
  // 自己的 manifest 用雜湊綁住的記錄：報告寫出來了才有 report_href，完整辯論記錄沒
  // 寫（或被改過）時 debate_href 就是 null。照著 run_id 拼一個路徑出來，等於替一個
  // 從來沒寫過的檔案掛上連結，讀者點開會拿到一個披著本場結論外皮的 404。
  function completeArtifactLinks(completion) {
    if (!completion || !completion.report_href) { return; }
    reportLink = artifactLink(reportLink, completion.report_href);
    debateLink = artifactLink(debateLink, completion.debate_href || null);
  }
  function applyClock(payload) {
    var incomingElapsed = Number(payload.elapsed_ms);
    if (Number.isFinite(incomingElapsed)) {
      currentElapsed = Math.max(currentElapsed, incomingElapsed);
    }
    if (payload.debate_started || payload.debate_start_remaining_ms === null) {
      debateStarted = true;
      currentDebateRemaining = null;
    } else if (!debateStarted && Number.isFinite(Number(payload.debate_start_remaining_ms))) {
      var incomingRemaining = Number(payload.debate_start_remaining_ms);
      currentDebateRemaining = currentDebateRemaining === null
        ? incomingRemaining : Math.min(currentDebateRemaining, incomingRemaining);
    }
    if (elapsedBox) {
      elapsedBox.dataset.elapsedMs = String(currentElapsed);
      elapsedBox.textContent = clock(currentElapsed);
    }
    if (debateRemaining) {
      debateRemaining.dataset.remainingMs = currentDebateRemaining === null
        ? "" : String(currentDebateRemaining);
      debateRemaining.textContent = debateStarted
        ? "辯論已開始" : countdownClock(currentDebateRemaining);
    }
  }
  // 換 run 會 close 舊的 EventSource，但已經排隊的事件仍可能送進舊 handler。所以
  // 兩道都要擋：handler 綁的那條連線必須就是現在這條，payload 也必須是現在這個
  // run。少了前者，上一場的 done 會把 artifact links、時鐘與結果寫進新 run 的畫面。
  function isCurrent(source, payload) {
    if (source !== stream) { return false; }
    if (!payload || !payload.run_id || !activeRun) { return true; }
    return payload.run_id === activeRun;
  }
  function apply(payload, replace, source) {
    if (!isCurrent(source, payload)) { return false; }
    if (replace) {
      clear(feed);
      feed.append(el("p", "feed-empty", "尚未開始辯論。"));
      document.querySelector(".feed-empty").id = "feed-empty";
    }
    appendMessages(payload.messages || []);
    drawTally(payload.tally);
    drawSeats(payload.seats);
    if (payload.cursor) { feed.dataset.cursor = payload.cursor; }
    if (roundBox && payload.round !== undefined && payload.round !== null) {
      roundBox.textContent = "第 " + payload.round + " 輪";
    }
    applyClock(payload);
    return true;
  }

  if (jump) {
    jump.addEventListener("click", function () {
      feed.scrollTop = feed.scrollHeight;
      jump.hidden = true;
    });
    feed.addEventListener("scroll", function () {
      if (pinned()) { jump.hidden = true; }
    });
  }

  // 換 run 時每一格 run-bound surface 讀什麼，是伺服器在 data-reset 上標好的
  // （pages.live_page._fresh_run_words）。這一場的 frame 只會帶 feed、席位、票數、
  // 輪次與時鐘，其餘每一格都還是上一場的答案，而且不會有任何 frame 來更正；照標記
  // 清，房間就不會多出第二套「還不知道」的講法，client 也不必自己藏一份會跟頁面走
  // 散的 id 清單。
  function resetMarkedSurfaces() {
    var marked = document.querySelectorAll("[data-reset]");
    for (var index = 0; index < marked.length; index += 1) {
      marked[index].textContent = marked[index].dataset.reset || "";
    }
  }
  function resetRun(runId) {
    activeRun = runId;
    resetMarkedSurfaces();
    currentElapsed = 0;
    currentDebateRemaining = null;
    debateStarted = false;
    feed.dataset.cursor = "";
    feed.dataset.runId = runId;
    if (currentRunBox) { currentRunBox.textContent = runId; }
    if (picker) { picker.value = ""; }
    if (elapsedBox) {
      elapsedBox.dataset.elapsedMs = "0";
      elapsedBox.textContent = "00:00";
    }
    if (totalRemaining) {
      var duration = Number(
        totalRemaining.dataset.durationMs || totalRemaining.dataset.countdownFrom || 0
      );
      totalRemaining.dataset.countdownFrom = String(duration);
      totalRemaining.textContent = clock(duration);
    }
    if (debateRemaining) {
      debateRemaining.dataset.remainingMs = "";
      debateRemaining.textContent = "等待狀態";
    }
    if (stateBox) { stateBox.textContent = "進行中"; stateBox.dataset.state = "running"; }
    resetArtifactLinks();
    clear(outcomeBox);
  }
  function connectRun(runId) {
    if (!runId) { return; }
    if (stream) { stream.close(); }
    if (activeRun !== runId) { resetRun(runId); }
    history.replaceState(null, "", "/live?run=" + encodeURIComponent(runId));
    var url = "/live/events?run=" + encodeURIComponent(runId);
    if (feed.dataset.cursor) {
      url += "&after=" + encodeURIComponent(feed.dataset.cursor);
    }
    stream = new EventSource(url);
    wireStream(stream);
  }
  function wireStream(source) {
  source.addEventListener("open", function () {
    if (!isCurrent(source, null)) { return; }
    if (connection) { connection.textContent = "直播連線中"; }
  });
  source.addEventListener("snapshot", function (event) {
    if (!apply(JSON.parse(event.data), true, source)) { return; }
    if (stateBox) { stateBox.textContent = "進行中"; stateBox.dataset.state = "running"; }
    clear(outcomeBox);
  });
  source.addEventListener("append", function (event) {
    apply(JSON.parse(event.data), false, source);
  });
  source.addEventListener("done", function (event) {
    var payload = JSON.parse(event.data);
    if (!apply(payload, false, source)) { return; }
    drawOutcome(payload.outcome);
    drawCompletion(payload.completion);
    completeArtifactLinks(payload.completion);
    // 停錶，不是歸零：這一格是「十七分鐘剩餘時間」，而剛剛被接受的權威 elapsed 已經
    // 寫進 applyClock，所以 tick 讀到的就是 done 這一刻的 TOTAL_WINDOW 減 elapsed ——
    // 跟同一個 finalized run 重新整理後伺服器投影的是同一個數字。接著 state 變成
    // finished，每秒的 interval 就不再動它。寫死 00:00 會宣稱十七分鐘用完了，而提早
    // 結束的一場其實沒有。
    tick(totalRemaining);
    if (stateBox) { stateBox.textContent = "已完成"; stateBox.dataset.state = "finished"; }
    source.close();
  });
  source.addEventListener("error", function () {
    if (!isCurrent(source, null)) { return; }
    if (connection) { connection.textContent = "連線中斷，正在重連"; }
  });
  }
  if (activeRun) { connectRun(activeRun); }

  window.setInterval(function () {
    if (!elapsedBox || !stateBox || stateBox.dataset.state !== "running") { return; }
    currentElapsed += 1000;
    elapsedBox.dataset.elapsedMs = String(currentElapsed);
    elapsedBox.textContent = clock(currentElapsed);
    if (!debateStarted && currentDebateRemaining !== null) {
      if (currentDebateRemaining <= 1000) {
        debateStarted = true;
        currentDebateRemaining = null;
        debateRemaining.textContent = "辯論已開始";
        debateRemaining.dataset.remainingMs = "";
      } else {
        currentDebateRemaining -= 1000;
        debateRemaining.textContent = countdownClock(currentDebateRemaining);
        debateRemaining.dataset.remainingMs = String(currentDebateRemaining);
      }
    }
    tick(totalRemaining);
  }, 1000);
})();
