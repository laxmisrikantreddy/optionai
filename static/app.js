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
const momClass = (mom, threshold) => {
  if (mom == null) return "flat";
  if (mom >= threshold) return "up";
  if (mom <= -threshold) return "down";
  return "flat";
};
const momLabel = (mom, threshold) => {
  if (mom == null) return "warming up";
  const cls = momClass(mom, threshold);
  if (cls === "up") return "▲ up " + signedPct(mom);
  if (cls === "down") return "▼ down " + signedPct(mom);
  return "≈ flat " + signedPct(mom);
};

async function refreshHealth() {
  const pill = document.getElementById("statusPill");
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    pill.textContent = h.running ? "running" : "idle";
    pill.className = "pill " + (h.running ? "ok" : "err");
    document.getElementById("lastPoll").textContent =
      h.last_poll_at ? `last poll ${fmtTime(h.last_poll_at)} · #${h.poll_count}` : "no poll yet";
    const err = document.getElementById("errPill");
    if (h.last_error) {
      err.style.display = "";
      err.textContent = h.last_error;
    } else {
      err.style.display = "none";
    }
    document.getElementById("cfgInfo").textContent =
      `${h.underlyings.join(", ")} · every ${h.poll_interval_seconds}s · momentum ${h.spot_lookback_seconds}s / min ±${h.spot_momentum_min_pct}%`;

    // Per-underlying spot strip with momentum direction.
    const spots = document.getElementById("spots");
    const threshold = Number(h.spot_momentum_min_pct ?? 0.1);
    spots.innerHTML = h.underlyings.map((u) => {
      const sp = (h.spot || {})[u];
      const mom = (h.spot_momentum_pct || {})[u];
      const cls = momClass(mom, threshold);
      return `
        <span>
          <span class="spot-name">${u}</span>
          <span class="spot-val">${fmt(sp, 2)}</span>
          <span class="pill ${cls}" style="margin-left:6px">${momLabel(mom, threshold)}</span>
        </span>`;
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
    if (!rows.length) {
      tbody.innerHTML = "";
      empty.style.display = "";
      return;
    }
    empty.style.display = "none";
    tbody.innerHTML = rows.map((s) => `
      <tr>
        <td>${fmtTime(s.timestamp)}</td>
        <td>${s.underlying}</td>
        <td class="num">${fmt(s.spot, 2)}</td>
        <td class="num ${Number(s.spot_momentum_pct) >= 0 ? "pos" : "neg"}">${signedPct(s.spot_momentum_pct)}</td>
        <td class="${s.side === "CE" ? "ce" : "pe"}">${s.side}</td>
        <td class="num">${fmt(s.strike, 2)}</td>
        <td>${s.expiry}</td>
        <td class="num">${fmt(s.ltp)}</td>
        <td class="num">${fmt(s.sl)}</td>
        <td class="num">${fmt(s.target)}</td>
        <td class="num">${fmt(s.delta, 3)}</td>
        <td class="num">${fmt(s.iv)}</td>
        <td class="num">${fmt(s.oi_change_pct, 1)}</td>
        <td class="num">${fmt(s.price_change_pct, 1)}</td>
        <td><span class="rule">${s.rule}</span></td>
      </tr>
    `).join("");
  } catch {
    /* ignore transient fetch errors */
  }
}

setInterval(refreshHealth, 3000);
setInterval(refreshSignals, 3000);
refreshHealth();
refreshSignals();
