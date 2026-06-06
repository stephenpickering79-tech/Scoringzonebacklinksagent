/* Variant A — Editorial render logic. */
(function () {
  const $ = (id) => document.getElementById(id);

  BL.loadAll("../data/").then((data) => {
    renderMeta(data);
    renderProposals(data);
    renderSessions(data);
    renderSubmissions(data);
    renderFoot(data);
  });

  function renderMeta(data) {
    const k = BL.kpis(data);
    const stats = [
      [k.submissions, "Submissions"],
      [k.live, "Live"],
      [k.avgDr ?? "—", "Avg DR"],
      [k.sessions, "Sessions"],
    ];
    $("metaRow").innerHTML = stats.map(
      (s) => `<div class="stat"><div class="n">${s[0]}</div><div class="l">${s[1]}</div></div>`
    ).join("");
  }

  function renderProposals(data) {
    const p = data.proposals || { items: [] };
    $("proposalWhen").textContent = p.date ? "run " + p.date : "no run yet";
    const items = p.items || [];

    // run-history trend
    const runs = data.runs || [];
    if (runs.length) {
      const series = runs.map((r) => r.shortlist_size || 0);
      const last = runs[runs.length - 1];
      $("trend").innerHTML =
        `<div><div class="label">Shortlist over time</div>
           <div class="figure">${last.shortlist_size} / ${last.candidates_found} candidates</div></div>
         ${BL.sparkline(series, { width: 260, height: 44 })}`;
    } else {
      $("trend").style.display = "none";
    }

    if (!items.length) {
      $("proposalLead").textContent = "No proposals yet. The daily agent writes a scored shortlist here after its first run.";
      $("proposals").innerHTML = `<div class="empty">Awaiting the first propose run</div>`;
      return;
    }
    $("proposalLead").textContent = `${items.length} scored target${items.length === 1 ? "" : "s"} that cleared the authority threshold.`;
    const rows = items.map((it, i) => {
      const target = it.url
        ? `<a href="${BL.escapeHtml(it.url)}" target="_blank" rel="noopener">${BL.escapeHtml(it.name)}</a>`
        : BL.escapeHtml(it.name);
      return `<tr class="reveal" style="animation-delay:${i * 0.03}s">
        <td class="idx">${i + 1}</td>
        <td class="target">${target}<span class="src">${BL.escapeHtml(it.recommended_action || "")}</span></td>
        <td class="score">${BL.escapeHtml(it.score)}</td>
        <td>${BL.escapeHtml(it.topical_relevance || "—")}</td>
        <td class="why">${BL.escapeHtml(BL.cleanMd(it.justification))}</td>
      </tr>`;
    }).join("");
    $("proposals").innerHTML = `<table class="etable">
      <thead><tr><th>#</th><th>Target</th><th>Score</th><th>Relevance</th><th>Why it fits</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function renderSessions(data) {
    const s = data.sessions || [];
    if (!s.length) {
      $("sessions").innerHTML = `<div class="empty">No Steel sessions recorded yet</div>`;
      return;
    }
    const rows = s.map((x, i) => `<tr class="reveal" style="animation-delay:${i * 0.03}s">
      <td class="date">${BL.fmtDateTime(x.timestamp)}</td>
      <td>${x.target ? `<a href="${BL.escapeHtml(x.target)}" target="_blank" rel="noopener">${BL.escapeHtml(BL.host(x.target))}</a>` : "—"}</td>
      <td><span class="badge ${BL.statusClass(x.outcome)}">${BL.escapeHtml(x.outcome)}</span></td>
      <td class="num">${x.screenshot_count || 0} shots</td>
      <td class="mono" style="color:var(--faint);font-size:12px">${BL.escapeHtml(x.session_id_short || "")}</td>
    </tr>`).join("");
    $("sessions").innerHTML = `<table class="etable">
      <thead><tr><th>When</th><th>Target</th><th>Outcome</th><th>Captures</th><th>Session</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function renderSubmissions(data) {
    const subs = (data.submissions || []).slice().sort(byDrDesc);
    if (!subs.length) {
      $("submissions").innerHTML = `<div class="empty">No submissions parsed</div>`;
      return;
    }
    const rows = subs.map((x, i) => {
      const name = x.url
        ? `<a href="${BL.escapeHtml(x.url)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>`
        : BL.escapeHtml(x.name);
      return `<tr class="reveal" style="animation-delay:${i * 0.02}s">
        <td class="name">${name}</td>
        <td class="date">${BL.escapeHtml(x.date || "—")}</td>
        <td class="drcell"><span class="dr">${x.dr != null ? x.dr : BL.escapeHtml(x.dr_label || "—")}</span></td>
        <td><span class="badge ${BL.statusClass(x.status)}">${BL.escapeHtml(BL.statusLabel(x.status))}</span></td>
        <td class="notes">${BL.escapeHtml(BL.cleanMd(x.notes))}</td>
      </tr>`;
    }).join("");
    $("submissions").innerHTML = `<table class="subs-list">
      <thead><tr><th>Directory</th><th>Date</th><th>DR</th><th>Status</th><th>Notes</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  function byDrDesc(a, b) {
    const av = a.dr == null ? -1 : a.dr, bv = b.dr == null ? -1 : b.dr;
    return bv - av;
  }

  function renderFoot(data) {
    const when = data.meta && data.meta.generated_at ? BL.fmtDateTime(data.meta.generated_at) : "—";
    $("foot").textContent = `Generated ${when} · backlink_agent/dashboard.py · data parsed from directory-submissions.md`;
  }
})();
