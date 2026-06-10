/* ===========================================================================
   Shared data loading + formatting helpers for all dashboard variants.
   Exposes a small global `BL`. No dependencies, no build step.
   =========================================================================== */
(function () {
  const FILES = ["submissions", "proposals", "runs", "sessions", "approvals", "livecheck", "meta"];

  // base is a relative path to the data dir, e.g. "../data/" or "data/".
  async function loadAll(base) {
    const out = {};
    await Promise.all(
      FILES.map(async (name) => {
        try {
          const res = await fetch(`${base}${name}.json`, { cache: "no-store" });
          out[name] = res.ok ? await res.json() : null;
        } catch (e) {
          out[name] = null;
        }
      })
    );
    return {
      submissions: out.submissions || [],
      proposals: out.proposals || { date: null, items: [] },
      runs: out.runs || [],
      sessions: out.sessions || [],
      approvals: out.approvals || [],
      livecheck: out.livecheck || { generated_at: null, summary: { live: 0, watching: 0, attention: 0 }, live: [], watching: [], attention: [] },
      meta: out.meta || { counts: {}, latest_run_date: null, generated_at: null },
    };
  }

  // Map a raw status string to a badge class.
  function statusClass(status) {
    const s = (status || "").toLowerCase();
    if (s.includes("live")) return "live";
    if (s.includes("lost") || s.includes("no link") || s.includes("error")) return "blocked";
    if (s.includes("block") || s.includes("reject")) return "blocked";
    if (s.includes("submit") || s.includes("pending") || s.includes("applied") || s.includes("ready") || s.includes("progress"))
      return "pending";
    return "default";
  }

  // Short label for a badge (strip parenthetical detail, keep a tidy chip).
  function statusLabel(status) {
    if (!status) return "—";
    return status.replace(/\s*\(.*?\)\s*/g, " ").replace(/✓/g, "").trim() || status;
  }

  // Strip light markdown (bold, code, links) for plain-text display.
  function cleanMd(s) {
    if (!s) return "";
    return String(s)
      .replace(/\*\*(.*?)\*\*/g, "$1")
      .replace(/`([^`]*)`/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .trim();
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Pretty host from a URL (for compact link display).
  function host(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); }
    catch (e) { return url || ""; }
  }

  function fmtDateTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  // KPIs derived from the data set, shared by Console + Bento variants.
  function kpis(data) {
    const subs = data.submissions || [];
    const live = subs.filter((s) => statusClass(s.status) === "live").length;
    const drs = subs.map((s) => s.dr).filter((n) => typeof n === "number");
    const avgDr = drs.length ? Math.round(drs.reduce((a, b) => a + b, 0) / drs.length) : null;
    const latest = data.runs && data.runs.length ? data.runs[data.runs.length - 1] : null;
    return {
      submissions: subs.length,
      live,
      avgDr,
      shortlist: (data.proposals && data.proposals.items ? data.proposals.items.length : 0),
      sessions: (data.sessions || []).length,
      candidates: latest ? latest.candidates_found : null,
    };
  }

  // Inline SVG sparkline from a list of numbers. Returns an SVG string.
  function sparkline(values, opts) {
    opts = opts || {};
    const w = opts.width || 220, h = opts.height || 40, p = 4;
    if (!values || values.length === 0)
      return `<svg class="spark" width="${w}" height="${h}" aria-hidden="true"></svg>`;
    if (values.length === 1) values = [values[0], values[0]];
    const max = Math.max(...values, 1), min = Math.min(...values, 0);
    const span = max - min || 1;
    const stepX = (w - p * 2) / (values.length - 1);
    const pts = values.map((v, i) => {
      const x = p + i * stepX;
      const y = h - p - ((v - min) / span) * (h - p * 2);
      return [x, y];
    });
    const line = pts.map((pt, i) => `${i ? "L" : "M"}${pt[0].toFixed(1)} ${pt[1].toFixed(1)}`).join(" ");
    const area = `M${pts[0][0].toFixed(1)} ${h - p} ` +
      pts.map((pt) => `L${pt[0].toFixed(1)} ${pt[1].toFixed(1)}`).join(" ") +
      ` L${pts[pts.length - 1][0].toFixed(1)} ${h - p} Z`;
    const last = pts[pts.length - 1];
    return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <path class="area" d="${area}"/>
      <path class="line" d="${line}"/>
      <circle class="dot" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3"/>
    </svg>`;
  }

  window.BL = { loadAll, statusClass, statusLabel, cleanMd, escapeHtml, host, fmtDateTime, kpis, sparkline };
})();
