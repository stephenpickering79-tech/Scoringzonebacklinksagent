/* Scoring Zone backlink dashboard — render logic + nav + sortable submissions table. */
(function () {
  const $ = (id) => document.getElementById(id);
  let DATA = null;
  // Approve token: load the dashboard as …/?k=YOURTOKEN and it's sent with submissions.
  const APPROVE_TOKEN = new URLSearchParams(location.search).get("k") || "";
  const TITLES = {
    output: ["Output", "Latest proposals"],
    livelinks: ["Live Links", "Verified backlinks"],
    approved: ["Approved", "Approved — to submit"],
    sessions: ["Sessions", "Steel automation runs"],
    submissions: ["Submissions", "Directory backlinks"],
  };
  const VIEWS = Object.keys(TITLES);

  BL.loadAll("data/").then((data) => {
    DATA = data;
    renderKpis(data);
    renderProposals(data);
    renderLiveLinks(data);
    renderRuns(data);
    renderApproved(data);
    renderSessions(data);
    renderSubmissions(data, { key: "dr", dir: "desc" });
    renderChrome(data);
    wireNav();
  });

  // Identity for a target, mirroring the backend's de-dupe (URL if present, else name).
  function approvalKey(name, url) {
    const u = (url || "").trim().toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/+$/, "");
    if (u) return u;
    return (name || "").trim().toLowerCase().replace(/\s+/g, " ");
  }

  // Map of approvalKey -> approval record, for quick "already approved?" lookups.
  function approvalIndex(data) {
    const idx = {};
    (data.approvals || []).forEach((a) => { idx[approvalKey(a.name, a.url)] = a; });
    return idx;
  }

  function renderChrome(data) {
    const when = data.meta && data.meta.generated_at ? BL.fmtDateTime(data.meta.generated_at) : "—";
    $("updated").textContent = "Updated " + when;
    $("gen").textContent = "gen " + when;
  }

  // Single source of truth for tab switching. Tabs route via location.hash so the
  // active tab survives refresh and back/forward work. Only the hash is touched —
  // location.search carries the ?k= approve token and must never be rewritten.
  function setView(view) {
    if (!TITLES[view]) view = "output";
    document.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view));
    VIEWS.forEach((v) => $("view-" + v).classList.toggle("hidden", v !== view));
    $("crumb").textContent = TITLES[view][0];
    $("title").textContent = TITLES[view][1];
  }

  function wireNav() {
    document.querySelectorAll(".nav-item").forEach((btn) =>
      btn.addEventListener("click", () => { location.hash = btn.dataset.view; }));
    window.addEventListener("hashchange", () => setView(location.hash.slice(1)));
    setView(location.hash.slice(1) || "output");
  }

  // Tiny toast system: errors and confirmations surface here instead of hiding in
  // button title attributes. textContent only — no HTML injection possible.
  function toast(msg, kind) {
    let wrap = document.getElementById("toasts");
    if (!wrap) { wrap = document.createElement("div"); wrap.id = "toasts"; document.body.appendChild(wrap); }
    const el = document.createElement("div");
    el.className = "toast " + (kind === "error" ? "error" : "success");
    el.setAttribute("role", "status");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 350); }, 4000);
  }

  function emptyState(glyph, title, hint) {
    return `<div class="empty"><span class="empty-ic">${glyph}</span>
      <div class="empty-title">${BL.escapeHtml(title)}</div>
      ${hint ? `<div class="empty-hint">${BL.escapeHtml(hint)}</div>` : ""}</div>`;
  }

  function renderKpis(data) {
    const k = BL.kpis(data);
    const cards = [
      { v: k.live, k: "Live backlinks", accent: true },
      { v: k.avgDr != null ? k.avgDr : "—", k: "Avg DR" },
      { v: k.submissions, k: "Submissions" },
      { v: k.shortlist, k: "On shortlist" },
      { v: k.sessions, k: "Sessions" },
    ];
    $("kpis").innerHTML = cards.map(
      (c) => `<div class="kpi ${c.accent ? "accent" : ""}"><div class="v">${c.v}</div><div class="k">${c.k}</div></div>`
    ).join("");
  }

  function renderProposals(data) {
    const p = data.proposals || { items: [] };
    $("proposalMeta").textContent = p.date ? "run " + p.date : "no run yet";
    const items = p.items || [];
    if (!items.length) {
      $("proposals").innerHTML = emptyState("◷", "Awaiting the first propose run",
        "The daily research run fills this shortlist");
      return;
    }
    const idx = approvalIndex(data);
    // Split into a decision queue: curated/already-approved vs fresh discoveries to review.
    const curated = [], discoveries = [];
    items.forEach((it) => {
      const isCurated = /target list/i.test(it.source || "") || !!idx[approvalKey(it.name, it.url)];
      (isCurated ? curated : discoveries).push(it);
    });
    const section = (label, list, opts) => list.length
      ? `<div class="group-label">${label} <span>${list.length}</span></div>` + proposalTable(list, idx, opts)
      : "";
    $("proposals").innerHTML =
      section("Curated &amp; approved", curated) +
      // Discoveries are dismissable: by construction they're neither curated nor approved.
      section("New discoveries — review", discoveries, { dismissable: true });
    wireApproveButtons($("proposals"));
    wireDismissButtons($("proposals"));
  }

  // Build one proposals table. Both groups render identically; the discoveries
  // group additionally gets a quiet ✕ Dismiss control per row.
  function proposalTable(list, idx, opts) {
    const rows = list.map((it, i) => {
      const label = (it.name && it.name.trim()) ? it.name : BL.host(it.url) || it.url || "—";
      const t = it.url ? `<a href="${BL.escapeHtml(it.url)}" target="_blank" rel="noopener">${BL.escapeHtml(label)}</a>` : BL.escapeHtml(label);
      const existing = idx[approvalKey(it.name, it.url)];
      const why = BL.cleanMd(it.justification);
      const whyShort = why.length > 160 ? why.slice(0, 160).trimEnd() + "…" : why;
      const dismiss = opts && opts.dismissable
        ? `<button class="dismiss-btn" title="Dismiss — exclude from future runs" aria-label="Dismiss ${BL.escapeHtml(label)}" data-name="${BL.escapeHtml(it.name)}" data-url="${BL.escapeHtml(it.url || "")}">✕</button>`
        : "";
      return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
        <td class="t-name">${t}</td>
        <td class="cell-score">${scoreChip(it.score)}</td>
        <td>${qualityCell(it)}</td>
        <td class="cell-why" title="${BL.escapeHtml(why + (it.source ? "  —  " + it.source : ""))}"><span class="why-text">${BL.escapeHtml(whyShort)}</span></td>
        <td class="cell-approve">${approveControl(it, existing)}${dismiss}</td>
      </tr>`;
    }).join("");
    return `<table>
      <thead><tr><th>Target</th><th class="cell-score">Score</th><th>Quality</th><th>Why it fits</th><th>Action</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function wireDismissButtons(scope) {
    scope.querySelectorAll(".dismiss-btn").forEach((btn) => {
      btn.addEventListener("click", () => onDismiss(btn));
    });
  }

  // Dismiss a suggestion: excludes it from future research runs (persistent —
  // recoverable only by editing data/excluded.json on the server).
  function onDismiss(btn) {
    const name = btn.dataset.name || "", url = btn.dataset.url || "";
    if (!confirm(`Dismiss "${name || url}"?\nIt will be excluded from future research runs.`)) return;
    btn.disabled = true;
    fetch("api/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Approve-Token": APPROVE_TOKEN },
      body: JSON.stringify({ name, url }),
    })
      .then((res) => { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(() => {
        const tr = btn.closest("tr");
        if (tr) tr.classList.add("row-out");
        setTimeout(() => {
          const key = approvalKey(name, url);
          DATA.proposals.items = (DATA.proposals.items || [])
            .filter((it) => approvalKey(it.name, it.url) !== key);
          renderProposals(DATA);
          renderKpis(DATA);
          toast("Dismissed — won't be suggested again");
        }, 280);
      })
      .catch((e) => {
        btn.disabled = false;
        toast("Dismiss failed: " + e.message, "error");
      });
  }

  // Score as a visual signal — tinted by band so the strongest targets pop.
  function scoreChip(score) {
    const n = parseInt(score, 10);
    let band = "low";
    if (!isNaN(n)) band = n >= 85 ? "high" : n >= 75 ? "mid" : "low";
    return `<span class="score-chip band-${band}">${BL.escapeHtml(isNaN(n) ? (score || "—") : n)}</span>`;
  }

  // Quality cell: tier label coloured by spam risk so risky targets stand out at a glance.
  function qualityCell(it) {
    const tier = it.quality_tier || "—";
    const risk = (it.spam_risk || "").toLowerCase();
    let cls = "default";
    if (risk === "low" || tier === "Curated") cls = "live";
    else if (risk === "medium") cls = "pending";
    else if (risk === "high" || tier === "Avoid") cls = "blocked";
    const riskNote = risk ? ` · ${risk} risk` : "";
    return `<span class="badge ${cls}" title="${BL.escapeHtml(tier + riskNote)}">${BL.escapeHtml(tier)}</span>`;
  }

  // The Approve cell: a button if not yet queued, else a status badge.
  function approveControl(it, existing) {
    if (existing) {
      return `<span class="badge ${approvalBadgeClass(existing.status)}">${BL.escapeHtml(approvalStatusLabel(existing.status))}</span>`;
    }
    return `<button class="approve-btn" data-name="${BL.escapeHtml(it.name)}" data-url="${BL.escapeHtml(it.url || "")}">Approve</button>`;
  }

  function approvalStatusLabel(status) {
    const map = {
      approved: "✓ Approved",
      submitting: "Submitting…",
      submitted: "✓ Submitted",
      needs_manual_submit: "Manual submit",
      error: "Error",
    };
    return map[status] || status || "Approved";
  }

  function approvalBadgeClass(status) {
    if (status === "submitted") return "live";
    if (status === "error") return "blocked";
    if (status === "needs_manual_submit") return "default";
    return "pending"; // approved / queued / submitting
  }

  // While the agent is mid-submit, poll approvals.json so status updates live (no manual refresh).
  let _approvalPollTimer = null;
  function maybePollApprovals(data) {
    const active = (data.approvals || []).some((a) => a.status === "submitting");
    if (!active || _approvalPollTimer) return;
    _approvalPollTimer = setTimeout(() => {
      _approvalPollTimer = null;
      fetch("data/approvals.json", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((list) => {
          if (Array.isArray(list)) {
            DATA.approvals = list;
            renderApproved(DATA);
            renderProposals(DATA);
          }
        })
        .catch(() => {});
    }, 5000);
  }

  function wireApproveButtons(scope) {
    scope.querySelectorAll(".approve-btn").forEach((btn) => {
      btn.addEventListener("click", () => onApprove(btn));
    });
  }

  function onApprove(btn) {
    const name = btn.dataset.name || "";
    const url = btn.dataset.url || "";
    btn.disabled = true;
    btn.textContent = "Approving…";
    fetch("api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Approve-Token": APPROVE_TOKEN },
      body: JSON.stringify({ name, url }),
    })
      .then((res) => { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then((resp) => {
        // Reflect locally without a full reload: add to the in-memory queue, re-render.
        // If the server kicked off submission, show "submitting" so the live poll picks it up.
        const status = resp && resp.submitting ? "submitting" : "approved";
        const key = approvalKey(name, url);
        const existing = (DATA.approvals || []).find((a) => approvalKey(a.name, a.url) === key);
        if (existing) {
          existing.status = status;
        } else {
          DATA.approvals = (DATA.approvals || []).concat([{ name, url, status, approved_at: new Date().toISOString() }]);
        }
        renderProposals(DATA);
        renderApproved(DATA);
        toast(resp && resp.submitting ? "Approved — agent is submitting now" : "Approved — queued for submission");
      })
      .catch((e) => {
        btn.disabled = false;
        btn.textContent = "Approve";
        toast("Could not approve (is the live server running?): " + e.message, "error");
      });
  }

  function renderApproved(data) {
    const items = (data.approvals || []).slice();
    $("approvedMeta").textContent = items.length ? items.length + " queued" : "nothing approved yet";
    if (!items.length) {
      $("approved").innerHTML = emptyState("✓", "No approvals yet",
        "Hit Approve on a proposal in Output to queue it");
      return;
    }
    const rows = items.map((a, i) => {
      const name = a.url ? `<a href="${BL.escapeHtml(a.url)}" target="_blank" rel="noopener">${BL.escapeHtml(a.name)}</a>` : BL.escapeHtml(a.name);
      const when = a.updated_at || a.approved_at;
      // Let the user record a manually-completed submission (not for ones already submitted/in-flight).
      const canMark = a.status !== "submitted" && a.status !== "submitting";
      const action = canMark
        ? `<button class="mark-btn" data-name="${BL.escapeHtml(a.name)}" data-url="${BL.escapeHtml(a.url || "")}">Mark submitted</button>`
        : "";
      return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
        <td class="t-name">${name}</td>
        <td><span class="badge ${approvalBadgeClass(a.status)}">${BL.escapeHtml(approvalStatusLabel(a.status))}</span></td>
        <td class="cell-date">${BL.fmtDateTime(when)}</td>
        <td class="cell-notes">${BL.escapeHtml(a.detail || "")}</td>
        <td class="cell-approve">${action}</td>
      </tr>`;
    }).join("");
    $("approved").innerHTML = `<table>
      <thead><tr><th>Target</th><th>Status</th><th>When</th><th>Detail</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    wireMarkButtons($("approved"));
    maybePollApprovals(data);
  }

  function wireMarkButtons(scope) {
    scope.querySelectorAll(".mark-btn").forEach((btn) => {
      btn.addEventListener("click", () => onMarkSubmitted(btn));
    });
  }

  function onMarkSubmitted(btn) {
    const name = btn.dataset.name || "", url = btn.dataset.url || "";
    btn.disabled = true;
    btn.textContent = "Saving…";
    fetch("api/mark-submitted", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Approve-Token": APPROVE_TOKEN },
      body: JSON.stringify({ name, url }),
    })
      .then((res) => { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(() => {
        const key = approvalKey(name, url);
        const a = (DATA.approvals || []).find((x) => approvalKey(x.name, x.url) === key);
        if (a) { a.status = "submitted"; a.detail = "Marked submitted manually."; a.updated_at = new Date().toISOString(); }
        renderApproved(DATA);
        renderProposals(DATA);
        toast("Marked submitted");
      })
      .catch((e) => {
        btn.disabled = false;
        btn.textContent = "Mark submitted";
        toast("Could not mark submitted: " + e.message, "error");
      });
  }

  function renderRuns(data) {
    const runs = data.runs || [];
    if (!runs.length) {
      $("runs").innerHTML = emptyState("◷", "No runs recorded yet",
        "The daily propose run logs here");
      return;
    }
    const series = runs.map((r) => r.shortlist_size || 0);
    const trend = `<div class="trend-card">
        <div><div class="panel-meta">Shortlist size over time</div></div>
        ${BL.sparkline(series, { width: 240, height: 40 })}</div>`;
    const rows = runs.slice().reverse().map((r) => `<div class="run-row">
        <span class="rdate">${BL.escapeHtml(r.date || "—")}</span>
        <span class="rmode">${BL.escapeHtml(r.mode || "")}</span>
        <span class="rfig">${r.candidates_found} found → <strong>${r.shortlist_size}</strong> shortlisted</span>
      </div>`).join("");
    $("runs").innerHTML = trend + `<div class="runs-list">${rows}</div>`;
  }

  // Linkify a session target only when it's an actual URL (submit runs); research targets are
  // plain descriptions ("Daily research — …") and must render as text, not a broken link.
  function sessionTarget(target) {
    if (!target) return "—";
    if (/^https?:\/\//i.test(target)) {
      return `<a href="${BL.escapeHtml(target)}" target="_blank" rel="noopener">${BL.escapeHtml(BL.host(target))}</a>`;
    }
    return BL.escapeHtml(target);
  }

  function renderSessions(data) {
    const s = data.sessions || [];
    if (!s.length) {
      $("sessions").innerHTML = emptyState("⦿", "No Steel sessions recorded yet",
        "Research and submission runs appear here");
      return;
    }
    const rows = s.map((x, i) => `<tr class="reveal" style="animation-delay:${i * 0.025}s">
      <td class="cell-date">${BL.fmtDateTime(x.timestamp)}</td>
      <td>${sessionTarget(x.target)}</td>
      <td class="mono" style="font-size:12px;color:var(--muted)">${BL.escapeHtml(x.mode || "")}</td>
      <td><span class="badge ${BL.statusClass(x.outcome)}">${BL.escapeHtml(x.outcome)}</span></td>
      <td class="num">${x.screenshot_count || 0}</td>
      <td class="mono" style="font-size:12px;color:var(--faint)">${BL.escapeHtml(x.session_id_short || "")}</td>
    </tr>`).join("");
    $("sessions").innerHTML = `<table>
      <thead><tr><th>When</th><th>Target</th><th>Mode</th><th>Outcome</th><th>Shots</th><th>Session</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  // ===== Live Links — the verification page ==================================

  function relBadge(rel) {
    const map = {
      dofollow: ["live", "dofollow"],
      nofollow: ["default", "nofollow"],
      redirect: ["pending", "via redirect"],
      unknown: ["default", "manual ✓"],
    };
    const [cls, label] = map[rel] || ["default", rel || "—"];
    return `<span class="badge ${cls}">${BL.escapeHtml(label)}</span>`;
  }

  function nextCheckCell(iso) {
    if (!iso) return "—";
    const today = new Date().toISOString().slice(0, 10);
    if (iso <= today) return `<span class="due-now">due now</span>`;
    return BL.escapeHtml(iso);
  }

  function fmtDay(iso) {
    return iso ? BL.escapeHtml(String(iso).slice(0, 10)) : "—";
  }

  function checkNowBtn(name, url) {
    return `<button class="approve-btn check-btn" data-name="${BL.escapeHtml(name)}" data-url="${BL.escapeHtml(url || "")}">Check now</button>`;
  }

  function renderLiveLinks(data) {
    const lc = data.livecheck || { summary: {}, live: [], watching: [], attention: [] };
    const sum = lc.summary || {};

    $("llSummary").innerHTML = [
      { n: sum.live || 0, l: "live", cls: "live" },
      { n: sum.watching || 0, l: "watching", cls: "watching" },
      { n: sum.attention || 0, l: "need attention", cls: "attention" },
    ].map((s) => `<div class="ll-stat ${s.cls}"><span class="n">${s.n}</span><span class="l">${s.l}</span></div>`).join("");

    // Live — the wins. Newest first (server-sorted by found_at).
    $("llLiveMeta").textContent = lc.generated_at ? "checked " + BL.fmtDateTime(lc.generated_at) : "";
    if (!(lc.live || []).length) {
      $("llLive").innerHTML = emptyState("⛳", "No live links verified yet",
        "Approved targets show here the moment a backlink is found");
    } else {
      const rows = lc.live.map((x, i) => {
        const name = x.listing_url
          ? `<a href="${BL.escapeHtml(x.listing_url)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>`
          : BL.escapeHtml(x.name);
        const action = x.tracked && x.listing_url ? checkNowBtn(x.name, x.listing_url) : "";
        return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
          <td class="t-name">${name}</td>
          <td>${relBadge(x.rel)}</td>
          <td class="cell-date cell-found">${fmtDay(x.found_at)}</td>
          <td class="cell-date">${fmtDay(x.last_checked)}</td>
          <td class="cell-date">${nextCheckCell(x.next_check_due)}</td>
          <td class="cell-approve">${action}</td>
        </tr>`;
      }).join("");
      $("llLive").innerHTML = `<table>
        <thead><tr><th>Directory</th><th>Link</th><th class="cell-found">Found</th><th>Last checked</th><th>Next check</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>`;
    }

    // Watching — submitted, link not live yet. Soonest check first (server-sorted).
    $("llWatchMeta").textContent = (lc.watching || []).length
      ? "re-checked every few days" : "";
    if (!(lc.watching || []).length) {
      $("llWatching").innerHTML = emptyState("◷", "Nothing being watched",
        "Submitted targets are monitored here until their link appears");
    } else {
      const rows = lc.watching.map((x, i) => {
        const name = x.url
          ? `<a href="${BL.escapeHtml(x.url)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>`
          : BL.escapeHtml(x.name);
        const waiting = x.days_waiting != null ? ` <span class="waiting">(${x.days_waiting}d)</span>` : "";
        const action = x.url ? checkNowBtn(x.name, x.url) : "";
        return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
          <td class="t-name">${name}</td>
          <td><span class="badge ${BL.statusClass(x.status_label)}">${BL.escapeHtml(BL.statusLabel(x.status_label))}</span></td>
          <td class="cell-date">${fmtDay(x.submitted_date)}${waiting}</td>
          <td class="cell-date">${fmtDay(x.last_checked)}</td>
          <td class="num cell-attempts">${x.attempts || 0}</td>
          <td class="cell-date">${nextCheckCell(x.next_check_due)}</td>
          <td class="cell-approve">${action}</td>
        </tr>`;
      }).join("");
      $("llWatching").innerHTML = `<table>
        <thead><tr><th>Directory</th><th>Status</th><th>Submitted</th><th>Last checked</th><th class="cell-attempts">Checks</th><th>Next check</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>`;
    }

    // Attention — lost links / gave up. Positive empty state when clear.
    $("llAttnMeta").textContent = "";
    if (!(lc.attention || []).length) {
      $("llAttention").innerHTML = emptyState("✓", "Nothing needs attention",
        "Lost or never-found links would be flagged here");
    } else {
      const rows = lc.attention.map((x, i) => {
        const href = x.listing_url || x.url;
        const name = href
          ? `<a href="${BL.escapeHtml(href)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>`
          : BL.escapeHtml(x.name);
        const action = (x.listing_url || x.url) ? checkNowBtn(x.name, x.listing_url || x.url) : "";
        return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
          <td class="t-name">${name}</td>
          <td><span class="badge blocked">${x.problem === "lost" ? "Link lost" : "No link found"}</span></td>
          <td class="cell-date">${fmtDay(x.last_checked)}</td>
          <td class="num cell-attempts">${x.attempts || 0}</td>
          <td class="cell-approve">${action}</td>
        </tr>`;
      }).join("");
      $("llAttention").innerHTML = `<table>
        <thead><tr><th>Directory</th><th>Problem</th><th>Last checked</th><th class="cell-attempts">Checks</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>`;
    }

    document.querySelectorAll("#view-livelinks .check-btn").forEach((btn) => {
      btn.addEventListener("click", () => onCheckLive(btn, refreshLivecheck));
    });
  }

  // Re-fetch the Live Links data after a check (the server rewrites livecheck.json
  // before responding) and re-render the page.
  function refreshLivecheck() {
    return fetch("data/livecheck.json", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((lc) => { if (lc) { DATA.livecheck = lc; renderLiveLinks(DATA); } })
      .catch(() => {});
  }

  let _subSort = { key: "dr", dir: "desc" };

  // A "Check now" live-link check makes sense for rows that were submitted but
  // aren't confirmed live (or where the link was lost) — and we need a URL to check.
  function canCheckLive(x) {
    if (!x.url) return false;
    const cls = BL.statusClass(x.status);
    const s = (x.status || "").toLowerCase();
    if (cls === "live") return false;
    if (s.includes("not submitted") || s.includes("to submit") || s.includes("needs manual") || s.includes("submitting")) return false;
    return cls === "pending" || s.includes("lost") || s.includes("no link");
  }

  function renderSubmissions(data, sort) {
    _subSort = sort;
    const cols = [
      { key: "name", label: "Directory" },
      { key: "date", label: "Date" },
      { key: "dr", label: "DR" },
      { key: "status", label: "Status" },
      { key: "notes", label: "Notes" },
    ];
    const subs = sortRows((data.submissions || []).slice(), sort);
    const maxDr = Math.max(...subs.map((s) => s.dr || 0), 100);

    const head = cols.map((c) => {
      const active = c.key === sort.key;
      const arrow = active ? (sort.dir === "asc" ? "↑" : "↓") : "↕";
      return `<th class="sortable" tabindex="0" role="button" data-key="${c.key}" ${active ? `aria-sort="${sort.dir === "asc" ? "ascending" : "descending"}"` : ""}>${c.label}<span class="arrow">${arrow}</span></th>`;
    }).join("") + "<th></th>";

    const rows = subs.map((x, i) => {
      const name = x.url ? `<a href="${BL.escapeHtml(x.url)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>` : BL.escapeHtml(x.name);
      const drDisplay = x.dr != null ? x.dr : BL.escapeHtml(x.dr_label || "—");
      const w = x.dr != null ? Math.round((x.dr / maxDr) * 100) : 0;
      const action = canCheckLive(x)
        ? `<button class="approve-btn check-btn" data-name="${BL.escapeHtml(x.name)}" data-url="${BL.escapeHtml(x.url || "")}">Check now</button>`
        : "";
      return `<tr class="reveal" style="animation-delay:${i * 0.015}s">
        <td class="t-name">${name}</td>
        <td class="cell-date">${BL.escapeHtml(x.date || "—")}</td>
        <td><div class="dr-cell"><span class="dr">${drDisplay}</span><span class="dr-track"><span class="dr-bar" style="--w:${w}%"></span></span></div></td>
        <td><span class="badge ${BL.statusClass(x.status)}">${BL.escapeHtml(BL.statusLabel(x.status))}</span></td>
        <td class="cell-notes">${BL.escapeHtml(BL.cleanMd(x.notes))}</td>
        <td class="cell-approve">${action}</td>
      </tr>`;
    }).join("");

    $("submissions").innerHTML = `<table>
      <thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;

    $("submissions").querySelectorAll("th.sortable").forEach((th) => {
      const toggle = () => {
        const key = th.dataset.key;
        const dir = sort.key === key && sort.dir === "desc" ? "asc" : "desc";
        renderSubmissions(DATA, { key, dir });
      };
      th.addEventListener("click", toggle);
      th.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
      });
    });
    $("submissions").querySelectorAll(".check-btn").forEach((btn) => {
      btn.addEventListener("click", () => onCheckLive(btn));
    });
  }

  // Live-link check: asks the server to look for our backlink on the target site now.
  // `after` (optional) runs on completion — the Live Links page passes refreshLivecheck.
  function onCheckLive(btn, after) {
    const name = btn.dataset.name || "", url = btn.dataset.url || "";
    btn.disabled = true;
    btn.textContent = "Checking…";
    fetch("api/check-live", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Approve-Token": APPROVE_TOKEN },
      body: JSON.stringify({ name, url }),
    })
      .then((res) => { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then((resp) => {
        if (resp.found && resp.status_written) {
          const key = approvalKey(name, url);
          const row = (DATA.submissions || []).find((x) => approvalKey(x.name, x.url) === key || approvalKey(x.name, "") === approvalKey(name, ""));
          if (row) {
            row.status = resp.status_written;
            if (resp.listing_url) row.url = resp.listing_url;
          }
          renderSubmissions(DATA, _subSort);
          renderKpis(DATA);
          toast("Link is LIVE ✓ — " + (name || url));
        } else if (resp.found) {
          toast("Still live ✓ — " + (name || url));
          btn.disabled = false;
          btn.textContent = "Check now";
        } else {
          btn.textContent = "No link yet";
          setTimeout(() => { btn.disabled = false; btn.textContent = "Check now"; }, 2500);
        }
        if (after) after();
      })
      .catch((e) => {
        btn.disabled = false;
        btn.textContent = "Check now";
        toast("Check failed: " + e.message, "error");
      });
  }

  function sortRows(rows, sort) {
    const { key, dir } = sort;
    const mul = dir === "asc" ? 1 : -1;
    return rows.sort((a, b) => {
      let av = a[key], bv = b[key];
      if (key === "dr") { av = av == null ? -1 : av; bv = bv == null ? -1 : bv; return (av - bv) * mul; }
      av = (av || "").toString().toLowerCase();
      bv = (bv || "").toString().toLowerCase();
      return av < bv ? -1 * mul : av > bv ? 1 * mul : 0;
    });
  }
})();
