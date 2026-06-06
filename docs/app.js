/* Scoring Zone backlink dashboard — render logic + nav + sortable submissions table. */
(function () {
  const $ = (id) => document.getElementById(id);
  let DATA = null;
  const TITLES = {
    output: ["Output", "Latest proposals"],
    sessions: ["Sessions", "Steel automation runs"],
    submissions: ["Submissions", "Directory backlinks"],
  };

  BL.loadAll("data/").then((data) => {
    DATA = data;
    renderKpis(data);
    renderProposals(data);
    renderRuns(data);
    renderSessions(data);
    renderSubmissions(data, { key: "dr", dir: "desc" });
    renderChrome(data);
    wireNav();
  });

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
      ["output", "sessions", "submissions"].forEach((v) => {
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
    const rows = items.map((it, i) => {
      const t = it.url ? `<a href="${BL.escapeHtml(it.url)}" target="_blank" rel="noopener">${BL.escapeHtml(it.name)}</a>` : BL.escapeHtml(it.name);
      return `<tr class="reveal" style="animation-delay:${i * 0.025}s">
        <td class="num" style="color:var(--faint)">${i + 1}</td>
        <td class="t-name">${t}<span class="sub">${BL.escapeHtml(it.recommended_action || "")}</span></td>
        <td class="num">${BL.escapeHtml(it.score)}</td>
        <td>${BL.escapeHtml(it.topical_relevance || "—")}</td>
        <td class="cell-why">${BL.escapeHtml(BL.cleanMd(it.justification))}</td>
      </tr>`;
    }).join("");
    $("proposals").innerHTML = `<table>
      <thead><tr><th>#</th><th>Target</th><th>Score</th><th>Relevance</th><th>Why it fits</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
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
