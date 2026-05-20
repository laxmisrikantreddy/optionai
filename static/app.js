const fmt = (n, d = 2) => (n == null || Number.isNaN(Number(n))) ? "-" : Number(n).toFixed(d);
const fmtTime = (iso) => {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleTimeString(); } catch { return "-"; }
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
      `${h.underlyings.join(", ")} · every ${h.poll_interval_seconds}s`;
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
