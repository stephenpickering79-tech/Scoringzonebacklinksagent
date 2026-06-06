/* Variant C — Bento render logic + expand-to-modal. */
(function () {
  const $ = (id) => document.getElementById(id);
  let DATA = null;

  BL.loadAll("../data/").then((data) => {
    DATA = data;
    renderBento(data);
    renderFoot(data);
    wireModal();
  });

  function renderBento(data) {
    const k = BL.kpis(data);
    const subs = (data.submissions || []).slice().sort(byDrDesc);
    const props = (data.proposals && data.proposals.items) || [];
    const sessions = data.sessions || [];
    const runs = data.runs || [];

    const tiles = [];

    // hero KPI (tall, spans 2 cols + 2 rows)
    tiles.push(`<div class="tile hero span-2 row-2">
      <div class="tlabel">Live backlinks</div>
      <div class="big">${k.live}</div>
      <div class="subgrid">
        <div><div class="v">${k.submissions}</div><div class="l">Submissions</div></div>
        <div><div class="v">${k.avgDr != null ? k.avgDr : "—"}</div><div class="l">Avg DR</div></div>
        <div><div class="v">${k.shortlist}</div><div class="l">Shortlist</div></div>
        <div><div class="v">${k.sessions}</div><div class="l">Sessions</div></div>
      </div>
    </div>`);

    // trend tile
    if (runs.length) {
      const last = runs[runs.length - 1];
      tiles.push(`<div class="tile trend span-2">
        <div><div class="tlabel">Shortlist trend</div>
          <div class="figure">${last.shortlist_size} / ${last.candidates_found}</div></div>
        ${BL.sparkline(runs.map((r) => r.shortlist_size || 0), { width: 360, height: 56 })}
      </div>`);
    } else {
      tiles.push(`<div class="tile trend span-2"><div class="tlabel">Shortlist trend</div><div class="empty" style="padding:10px 0">No runs yet</div></div>`);
    }

    // proposals list tile (clickable → modal)
    tiles.push(listTile({
      cls: "span-2 row-2", label: "Latest proposals", title: titleProposals(data),
      empty: "Awaiting first run", open: "proposals",
      rows: props.slice(0, 5).map((p) =>
        `<div class="minirow"><span class="mr-name">${BL.escapeHtml(p.name)}</span><span class="mr-score">${BL.escapeHtml(p.score)}</span></div>`
      ),
    }));

    // top submissions tile (clickable → modal)
    tiles.push(listTile({
      cls: "span-2 row-2", label: "Top submissions · DR", title: "By Domain Rating",
      empty: "No submissions", open: "submissions",
      rows: subs.slice(0, 5).map((s) =>
        `<div class="minirow"><span class="mr-name">${BL.escapeHtml(s.name)}</span><span class="mr-dr">${s.dr != null ? "DR " + s.dr : BL.escapeHtml(s.dr_label || "—")}</span></div>`
      ),
    }));

    // sessions tile (clickable → modal)
    tiles.push(listTile({
      cls: "span-2", label: "Recent sessions", title: "Steel automation",
      empty: "No sessions yet", open: "sessions",
      rows: sessions.slice(0, 2).map((s) =>
        `<div class="minirow"><span class="mr-name">${BL.escapeHtml(s.target ? BL.host(s.target) : s.mode || "session")}</span>
          <span class="badge ${BL.statusClass(s.outcome)}">${BL.escapeHtml(s.outcome)}</span></div>`
      ),
    }));

    $("bento").innerHTML = tiles.join("");

    // wire clickable tiles
    $("bento").querySelectorAll(".tile.click").forEach((t) => {
      t.addEventListener("click", () => openModal(t.dataset.open));
    });
  }

  function listTile(o) {
    const body = o.rows && o.rows.length
      ? `<div class="rows">${o.rows.join("")}</div><div class="more">view all</div>`
      : `<div class="empty" style="padding:14px 0">${o.empty}</div>`;
    const clickable = o.rows && o.rows.length ? "click" : "";
    return `<div class="tile list ${o.cls} ${clickable}" data-open="${o.open}">
      <div class="tlabel">${o.label}</div>
      <h3 style="margin-bottom:8px">${o.title}</h3>
      ${body}
    </div>`;
  }

  function titleProposals(data) {
    const d = data.proposals && data.proposals.date;
    return d ? "Run " + d : "Scored shortlist";
  }

  /* ---- modal ---- */
  function wireModal() {
    $("modalClose").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  }
  function closeModal() { $("modal").hidden = true; }

  function openModal(which) {
    let title = "", html = "";
    if (which === "proposals") { title = "Latest proposals"; html = proposalsTable(DATA); }
    else if (which === "submissions") { title = "Directory submissions"; html = submissionsTable(DATA, { key: "dr", dir: "desc" }); }
    else if (which === "sessions") { title = "Steel sessions"; html = sessionsTable(DATA); }
    $("modalTitle").textContent = title;
    $("modalBody").innerHTML = html;
    $("modal").hidden = false;
    if (which === "submissions") wireSubSort();
  }

  function proposalsTable(data) {
    const items = (data.proposals && data.proposals.items) || [];
    if (!items.length) return `<div class="empty">Awaiting the first propose run</div>`;
    const rows = items.map((it, i) => {
      const t = it.url ? `<a href="${BL.escapeHtml(it.url)}" target="_blank" rel="noopener">${BL.escapeHtml(it.name)}</a>` : BL.escapeHtml(it.name);
      return `<tr><td class="num" style="color:var(--faint)">${i + 1}</td><td class="t-name">${t}</td>
        <td class="num">${BL.escapeHtml(it.score)}</td><td>${BL.escapeHtml(it.topical_relevance || "—")}</td>
        <td class="cell-why">${BL.escapeHtml(BL.cleanMd(it.justification))}</td></tr>`;
    }).join("");
    return `<table><thead><tr><th>#</th><th>Target</th><th>Score</th><th>Relevance</th><th>Why it fits</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function sessionsTable(data) {
    const s = data.sessions || [];
    if (!s.length) return `<div class="empty">No Steel sessions recorded yet</div>`;
    const rows = s.map((x) => `<tr>
      <td class="cell-date">${BL.fmtDateTime(x.timestamp)}</td>
      <td>${x.target ? `<a href="${BL.escapeHtml(x.target)}" target="_blank" rel="noopener">${BL.escapeHtml(BL.host(x.target))}</a>` : "—"}</td>
      <td class="mono" style="font-size:12px;color:var(--muted)">${BL.escapeHtml(x.mode || "")}</td>
      <td><span class="badge ${BL.statusClass(x.outcome)}">${BL.escapeHtml(x.outcome)}</span></td>
      <td class="num">${x.screenshot_count || 0}</td></tr>`).join("");
    return `<table><thead><tr><th>When</th><th>Target</th><th>Mode</th><th>Outcome</th><th>Shots</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function submissionsTable(data, sort) {
    const cols = [["name", "Directory"], ["date", "Date"], ["dr", "DR"], ["status", "Status"], ["notes", "Notes"]];
    const subs = sortRows((data.submissions || []).slice(), sort);
    const head = cols.map(([key, label]) => {
      const active = key === sort.key, arrow = active ? (sort.dir === "asc" ? "↑" : "↓") : "↕";
      return `<th class="sortable" data-key="${key}" ${active ? `aria-sort="${sort.dir}"` : ""}>${label} <span class="arrow">${arrow}</span></th>`;
    }).join("");
    const rows = subs.map((x) => {
      const name = x.url ? `<a href="${BL.escapeHtml(x.url)}" target="_blank" rel="noopener">${BL.escapeHtml(x.name)}</a>` : BL.escapeHtml(x.name);
      return `<tr><td class="t-name">${name}</td><td class="cell-date">${BL.escapeHtml(x.date || "—")}</td>
        <td class="dr">${x.dr != null ? x.dr : BL.escapeHtml(x.dr_label || "—")}</td>
        <td><span class="badge ${BL.statusClass(x.status)}">${BL.escapeHtml(BL.statusLabel(x.status))}</span></td>
        <td class="cell-notes">${BL.escapeHtml(BL.cleanMd(x.notes))}</td></tr>`;
    }).join("");
    return `<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  function wireSubSort() {
    $("modalBody").querySelectorAll("th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        const cur = th.getAttribute("aria-sort");
        const dir = cur === "desc" ? "asc" : "desc";
        $("modalBody").innerHTML = submissionsTable(DATA, { key, dir });
        wireSubSort();
      });
    });
  }

  function sortRows(rows, sort) {
    const mul = sort.dir === "asc" ? 1 : -1;
    return rows.sort((a, b) => {
      let av = a[sort.key], bv = b[sort.key];
      if (sort.key === "dr") { av = av == null ? -1 : av; bv = bv == null ? -1 : bv; return (av - bv) * mul; }
      av = (av || "").toString().toLowerCase(); bv = (bv || "").toString().toLowerCase();
      return av < bv ? -mul : av > bv ? mul : 0;
    });
  }
  function byDrDesc(a, b) { return (b.dr == null ? -1 : b.dr) - (a.dr == null ? -1 : a.dr); }

  function renderFoot(data) {
    const when = data.meta && data.meta.generated_at ? BL.fmtDateTime(data.meta.generated_at) : "—";
    $("foot").textContent = `Generated ${when} · backlink_agent/dashboard.py`;
  }
})();
