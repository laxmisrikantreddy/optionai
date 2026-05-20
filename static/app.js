const fmt = (n, d = 2) => (n == null || Number.isNaN(Number(n))) ? "-" : Number(n).toFixed(d);
const fmtTime = (iso) => {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleTimeString(); } catch { return "-"; }
};
const signedPct = (n) => {
  if (n == null || Number.isNaN(Number(n))) return "-";
  const v = Number(n);
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
};

function momPill(val, threshold) {
  if (val == null) return `<span class="pill flat">warming up</span>`;
  const v = Number(val);
  if (v >= threshold) return `<span class="pill up">&#9650; ${signedPct(v)}</span>`;
  if (v <= -threshold) return `<span class="pill down">&#9660; ${signedPct(v)}</span>`;
  return `<span class="pill flat">&asymp; ${signedPct(v)}</span>`;
}

function orbPill(brk) {
  if (!brk) return `<span class="pill flat">forming</span>`;
  if (brk === "HIGH") return `<span class="pill up">&#9650; broke HIGH</span>`;
  if (brk === "LOW") return `<span class="pill down">&#9660; broke LOW</span>`;
  return `<span class="pill flat">INSIDE range</span>`;
}

async function refreshHealth() {
  const pill = document.getElementById("statusPill");
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    pill.textContent = h.running ? "running" : "idle";
    pill.className = "pill " + (h.running ? "ok" : "err");
    document.getElementById("lastPoll").textContent =
      h.last_poll_at ? `poll #${h.poll_count} · ${fmtTime(h.last_poll_at)}` : "no poll yet";
    const err = document.getElementById("errPill");
    if (h.last_error) { err.style.display = ""; err.textContent = h.last_error; }
    else { err.style.display = "none"; }

    const threshold = h.rules?.spot_momentum_min_pct ?? 0.1;
    document.getElementById("cfgInfo").textContent =
      `${h.underlyings.join(", ")} · every ${h.poll_interval_seconds}s · ORB ${h.rules.orb_minutes}min`;

    // Price-action cards per underlying.
    const strip = document.getElementById("paStrip");
    strip.innerHTML = h.underlyings.map((u) => {
      const s = (h.snapshots || {})[u] || {};
      return `
        <div class="pa-card">
          <h3>${u}</h3>
          <div class="pa-row">
            <span><span class="pa-label">Spot</span><span class="pa-val">${fmt(s.spot)}</span></span>
            <span><span class="pa-label">VWAP</span><span class="pa-val ${s.above_vwap === true ? 'pos' : s.above_vwap === false ? 'neg' : ''}">${fmt(s.vwap)}</span></span>
          </div>
          <div class="pa-row" style="margin-top:6px">
            <span><span class="pa-label">5m</span>${momPill(s.momentum_5m_pct, threshold)}</span>
            <span><span class="pa-label">15m</span>${momPill(s.momentum_15m_pct, threshold)}</span>
          </div>
          <div class="pa-row" style="margin-top:6px">
            <span><span class="pa-label">ORB</span>${orbPill(s.orb_break)}</span>
            <span><span class="pa-label">High</span><span class="pa-val">${fmt(s.orb_high)}</span></span>
            <span><span class="pa-label">Low</span><span class="pa-val">${fmt(s.orb_low)}</span></span>
          </div>
        </div>`;
    }).join("");
  } catch {
    pill.textContent = "offline";
    pill.className = "pill err";
  }
}

async function refreshSignals() {
  try {
    const r = await fetch("/api/signals?limit=200");
    const rows = await r.json();
    const tbody = document.getElementById("rows");
    const empty = document.getElementById("empty");
    if (!rows.length) { tbody.innerHTML = ""; empty.style.display = ""; return; }
    empty.style.display = "none";
    tbody.innerHTML = rows.map((s) => `
      <tr>
        <td>${fmtTime(s.timestamp)}</td>
        <td>${s.underlying}</td>
        <td class="${s.side === "CE" ? "ce" : "pe"}">${s.side}</td>
        <td class="num">${fmt(s.strike, 0)}</td>
        <td>${s.expiry}</td>
        <td class="num">${fmt(s.ltp)}</td>
        <td class="num">${fmt(s.sl)}</td>
        <td class="num">${fmt(s.target)}</td>
        <td class="num">${fmt(s.spot)}</td>
        <td class="num">${fmt(s.vwap)}</td>
        <td class="num ${Number(s.spot_momentum_5m_pct) >= 0 ? 'pos' : 'neg'}">${signedPct(s.spot_momentum_5m_pct)}</td>
        <td class="num ${Number(s.spot_momentum_15m_pct) >= 0 ? 'pos' : 'neg'}">${signedPct(s.spot_momentum_15m_pct)}</td>
        <td class="num">${fmt(s.oi_change_pct, 1)}</td>
        <td><span class="rule">${s.rule}</span></td>
        <td><span class="filters">${s.filters_passed || '-'}</span></td>
      </tr>
    `).join("");
  } catch { /* ignore */ }
}

setInterval(refreshHealth, 3000);
setInterval(refreshSignals, 3000);
refreshHealth();
refreshSignals();
