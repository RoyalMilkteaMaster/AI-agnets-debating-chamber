
(function () {
  "use strict";
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
  var totalRemaining = document.getElementById("live-total-remaining");
  var reportRemaining = document.getElementById("live-report-remaining");
  var focusTally = document.querySelector(".focus-bar .focus-tally");
  var jump = document.getElementById("feed-jump");

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
  function apply(payload, replace) {
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
    if (elapsedBox && payload.elapsed_ms !== undefined) {
      elapsedBox.dataset.elapsedMs = String(payload.elapsed_ms);
      elapsedBox.textContent = clock(payload.elapsed_ms);
    }
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

  var url = "/live/events";
  if (feed.dataset.cursor) {
    url += "?after=" + encodeURIComponent(feed.dataset.cursor);
  }
  var stream = new EventSource(url);
  stream.addEventListener("open", function () {
    if (connection) { connection.textContent = "直播連線中"; }
  });
  stream.addEventListener("snapshot", function (event) {
    apply(JSON.parse(event.data), true);
  });
  stream.addEventListener("append", function (event) {
    apply(JSON.parse(event.data), false);
  });
  stream.addEventListener("done", function (event) {
    var payload = JSON.parse(event.data);
    apply(payload, false);
    drawOutcome(payload.outcome);
    if (stateBox) { stateBox.textContent = "已完成"; stateBox.dataset.state = "finished"; }
    stream.close();
  });
  stream.addEventListener("error", function () {
    if (connection) { connection.textContent = "連線中斷，正在重連"; }
  });

  window.setInterval(function () {
    if (!elapsedBox || !stateBox || stateBox.dataset.state !== "running") { return; }
    var next = Number(elapsedBox.dataset.elapsedMs || 0) + 1000;
    elapsedBox.dataset.elapsedMs = String(next);
    elapsedBox.textContent = clock(next);
    tick(totalRemaining);
    tick(reportRemaining);
  }, 1000);
})();
