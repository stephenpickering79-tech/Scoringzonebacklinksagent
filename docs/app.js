/* Scoring Zone backlink dashboard — render logic + nav + sortable submissions table. */
(function () {
  const $ = (id) => document.getElementById(id);
  let DATA = null;
  // Approve token: load the dashboard as …/?k=YOURTOKEN and it's sent with submissions.
  const APPROVE_TOKEN = new URLSearchParams(location.search).get("k") || "";
  const TITLES = {
    output: ["Output", "Latest proposals"],
    approved: ["Approved", "Approved — to submit"],
    sessions: ["Sessions", "Steel automation runs"],
    submissions: ["Submissions", "Directory backlinks"],
  };

  BL.loadAll("data/").then((data) => {
    DATA = data;
    renderKpis(data);
    renderProposals(data);
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

  function wireNav() {
    const items = document.querySelectorAll(".nav-item");
    items.forEach((btn) => btn.addEventListener("click", () => {
      items.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const view = btn.dataset.view;
      ["output", "approved", "sessions", "submissions"].forEach((v) => {
        $("view-" + v).classList.toggle("hidden", v !== view);
      });
      $("crumb").textContent = TITLES[view][0];
      $("title").textContent = TITLES[view][1];
    }));
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
      $("proposals").innerHTML = `<div class="empty">Awaiting the first propose run</div>`;
      return;
    }
    const idx = approvalIndex(data);
    const rows = items.map((it, i) => {
      const t = it.url ? `<a href="${BL.escapeHtml(it.url)}" target="_blank" rel="noopener">${BL.escapeHtml(it.name)}</a>` : BL.escapeHtml(it.name);
      const existing = idx[approvalKey(it.name, it.url)];
      return `<tr class="reveal" style="animation-delay:${i * 0.025}s">
        <td class="num" style="color:var(--faint)">${i + 1}</td>
        <td class="t-name">${t}<span class="sub">${BL.escapeHtml(it.recommended_action || "")}</span></td>
        <td class="num">${BL.escapeHtml(it.score)}</td>
        <td>${BL.escapeHtml(it.topical_relevance || "—")}</td>
        <td class="cell-why">${BL.escapeHtml(BL.cleanMd(it.justification))}</td>
        <td>${qualityCell(it)}</td>
        <td class="cell-approve">${approveControl(it, existing)}</td>
      </tr>`;
    }).join("");
    $("proposals").innerHTML = `<table>
      <thead><tr><th>#</th><th>Target</th><th>Score</th><th>Relevance</th><th>Why it fits</th><th>Quality</th><th>Action</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    wireApproveButtons($("proposals"));
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
      })
      .catch((e) => {
        btn.disabled = false;
        btn.textContent = "Approve";
        btn.classList.add("approve-err");
        btn.title = "Could not approve (is the live server running?): " + e.message;
      });
  }

  function renderApproved(data) {
    const items = (data.approvals || []).slice();
    $("approvedMeta").textContent = items.length ? items.length + " queued" : "nothing approved yet";
    if (!items.length) {
      $("approved").innerHTML = `<div class="empty">No approvals yet — hit Approve on a proposal in Output</div>`;
      return;
    }
    const rows = items.map((a, i) => {
      const name = a.url ? `<a href="${BL.escapeHtml(a.url)}" target="_blank" rel="noopener">${BL.escapeHtml(a.name)}</a>` : BL.escapeHtml(a.name);
      const when = a.updated_at || a.approved_at;
      return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
        <td class="t-name">${name}</td>
        <td><span class="badge ${approvalBadgeClass(a.status)}">${BL.escapeHtml(approvalStatusLabel(a.status))}</span></td>
        <td class="cell-date">${BL.fmtDateTime(when)}</td>
        <td class="cell-notes">${BL.escapeHtml(a.detail || "")}</td>
      </tr>`;
    }).join("");
    $("approved").innerHTML = `<table>
      <thead><tr><th>Target</th><th>Status</th><th>When</th><th>Detail</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    maybePollApprovals(data);
  }

  function renderRuns(data) {
    const runs = data.runs || [];
    if (!runs.length) {
      $("runs").innerHTML = `<div class="empty">No runs recorded yet</div>`;
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
    if (!s.length) { $("sessions").innerHTML = `<div class="empty">No Steel sessions recorded yet</div>`; return; }
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

  function renderSubmissions(data, sort) {
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
      return `<th class="sortable" data-key="${c.key}" ${active ? `aria-sort="${sort.dir}"` : ""}>${c.label}<span class="arrow">${arrow}</span></th>`;
    }).join("");

    const rows = subs.map((x, i) => {
      const name = x.url ? `<a href="${BL.escapeHtml(x.url)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>` : BL.escapeHtml(x.name);
      const drDisplay = x.dr != null ? x.dr : BL.escapeHtml(x.dr_label || "—");
      const w = x.dr != null ? Math.round((x.dr / maxDr) * 100) : 0;
      return `<tr class="reveal" style="animation-delay:${i * 0.015}s">
        <td class="t-name">${name}</td>
        <td class="cell-date">${BL.escapeHtml(x.date || "—")}</td>
        <td><div class="dr-cell"><span class="dr">${drDisplay}</span><span class="dr-track"><span class="dr-bar" style="--w:${w}%"></span></span></div></td>
        <td><span class="badge ${BL.statusClass(x.status)}">${BL.escapeHtml(BL.statusLabel(x.status))}</span></td>
        <td class="cell-notes">${BL.escapeHtml(BL.cleanMd(x.notes))}</td>
      </tr>`;
    }).join("");

    $("submissions").innerHTML = `<table>
      <thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;

    $("submissions").querySelectorAll("th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        const dir = sort.key === key && sort.dir === "desc" ? "asc" : "desc";
        renderSubmissions(DATA, { key, dir });
      });
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
