"use strict";

// ---------------------------------------------------------------- helpers
const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const nf = (d = 0) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: d, minimumFractionDigits: d });
const fmt = (v, d = 0) => (v == null || !isFinite(v) ? "–" : nf(d).format(v));
const hh = (h) => String(h).padStart(2, "0") + ":00";
const sum = (a) => a.reduce((s, v) => s + (v || 0), 0);
const mean = (a) => sum(a) / a.filter((v) => v != null).length;
const TL = (v) => fmt(v) + " TL";
const renders = [];
// forget a chart's previous redraw before drawing it again
const drop = (host) => { const i = renders.indexOf(host._draw); if (i >= 0) renders.splice(i, 1); };

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function niceTicks(lo, hi, n = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const step0 = (hi - lo) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return { ticks: out, lo: Math.min(lo, out[0]), hi: Math.max(hi, out[out.length - 1]) };
}

function tipBox(host) {
  let t = host.querySelector(".tip");
  if (!t) { t = document.createElement("div"); t.className = "tip"; host.appendChild(t); }
  return t;
}

function showTip(host, tip, x, y, title, rows) {
  tip.innerHTML = `<b>${title}</b>` + rows.map((r) =>
    `<div class="row"><span>${r.color ? `<i class="sw" style="background:var(${r.color})"></i>` : ""}${r.label}</span><span>${r.value}</span></div>`).join("");
  tip.style.display = "block";
  const w = host.clientWidth, tw = tip.offsetWidth;
  tip.style.left = Math.min(Math.max(0, x + 14), w - tw) + "px";
  tip.style.top = Math.max(0, y - 10) + "px";
}

// ---------------------------------------------------------------- line / band chart
// series: {kind: "line"|"band", x, y | lo+hi, color, dash, width, label, fmt}
function lineChart(host, o) {
  const draw = () => {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.height || 260;
    const m = { l: 52, r: 12, t: 10, b: 26 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.aria || "" }, host);
    const xs = o.series.flatMap((s) => s.x), ys = o.series.flatMap((s) => (s.kind === "band" ? [...s.lo, ...s.hi] : s.y)).filter((v) => v != null && isFinite(v));
    let x0 = o.xMin ?? Math.min(...xs), x1 = o.xMax ?? Math.max(...xs);
    let y0 = o.yMin ?? Math.min(...ys), y1 = o.yMax ?? Math.max(...ys);
    if (o.zero) y0 = Math.min(0, y0);
    const yt = niceTicks(y0, y1, o.yTicks || 4);
    y0 = yt.lo; y1 = yt.hi;
    const X = (v) => m.l + ((v - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
    const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
    for (const v of yt.ticks) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), style: `stroke:var(${v === 0 ? "--axis" : "--grid"})`, "stroke-width": 1 }, svg);
      el("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = (o.yfmt || fmt)(v);
    }
    const xticks = o.xTicks ? o.xTicks(x0, x1, W) : niceTicks(x0, x1, W < 520 ? 3 : 6).ticks.filter((v) => v >= x0 && v <= x1);
    for (const v of xticks) el("text", { x: X(v), y: H - 6, "text-anchor": "middle" }, svg).textContent = (o.xfmt || fmt)(v);
    const path = (x, y) => {
      let d = "", pen = false;
      x.forEach((xv, i) => {
        if (y[i] == null || !isFinite(y[i])) { pen = false; return; }
        d += (pen ? "L" : "M") + X(xv).toFixed(1) + "," + Y(y[i]).toFixed(1); pen = true;
      });
      return d;
    };
    for (const s of o.series) {
      if (s.kind === "band") {
        let d = "", seg = [];
        const flush = () => {
          if (seg.length > 1) d += "M" + seg.map((i) => X(s.x[i]) + "," + Y(s.hi[i])).join("L") + "L" + seg.slice().reverse().map((i) => X(s.x[i]) + "," + Y(s.lo[i])).join("L") + "Z";
          seg = [];
        };
        s.x.forEach((_, i) => (s.lo[i] == null || s.hi[i] == null ? flush() : seg.push(i)));
        flush();
        el("path", { d, style: `fill:var(${s.color || "--s1-band"})` }, svg);
      } else {
        el("path", { d: path(s.x, s.y), fill: "none", style: `stroke:var(${s.color})`, "stroke-width": s.width || 2, "stroke-dasharray": s.dash ? "4 4" : "none", "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      }
    }
    if (o.after) o.after(svg, X, Y, W, H, m);
    // hover: crosshair + tooltip at the nearest x of the first series
    const tip = tipBox(host);
    const cross = el("line", { y1: m.t, y2: H - m.b, style: "stroke:var(--axis)", "stroke-width": 1, visibility: "hidden" }, svg);
    const dots = o.series.filter((s) => s.kind !== "band").map((s) => el("circle", { r: 4, style: `fill:var(${s.color});stroke:var(--surface)`, "stroke-width": 2, visibility: "hidden" }, svg));
    const hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" }, svg);
    const nearest = (arr, v) => { let b = 0; for (let i = 1; i < arr.length; i++) if (Math.abs(arr[i] - v) < Math.abs(arr[b] - v)) b = i; return b; };
    const move = (ev) => {
      const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (W / r.width);
      const xv = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
      const base = o.series[0], i = nearest(base.x, xv), xi = base.x[i];
      cross.setAttribute("x1", X(xi)); cross.setAttribute("x2", X(xi)); cross.setAttribute("visibility", "visible");
      const rows = [];
      let di = 0;
      for (const s of o.series) {
        const j = s.x === base.x ? i : nearest(s.x, xi);
        if (s.kind === "band") { if (s.label) rows.push({ label: s.label, value: `${(s.fmt || fmt)(s.lo[j])} – ${(s.fmt || fmt)(s.hi[j])}`, color: "--s1-band" }); continue; }
        const dot = dots[di++], v = s.y[j];
        if (v == null || !isFinite(v)) { dot.setAttribute("visibility", "hidden"); continue; }
        dot.setAttribute("cx", X(s.x[j])); dot.setAttribute("cy", Y(v)); dot.setAttribute("visibility", "visible");
        if (s.label) rows.push({ label: s.label, value: (s.fmt || fmt)(v), color: s.color });
      }
      showTip(host, tip, (X(xi) * r.width) / W, ((ev.clientY - r.top)), (o.tipTitle || ((v) => (o.xfmt || fmt)(v)))(xi, i), rows);
    };
    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); dots.forEach((d) => d.setAttribute("visibility", "hidden")); });
  };
  draw();
  renders.push(draw);
  return draw;
}

// ---------------------------------------------------------------- signed vertical bars
function barChart(host, o) {
  const draw = () => {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.height || 200, m = { l: 44, r: 8, t: 10, b: 26 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.aria || "" }, host);
    const vals = o.values, n = vals.length;
    const yt = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
    const Y = (v) => m.t + (1 - (v - yt.lo) / (yt.hi - yt.lo)) * (H - m.t - m.b);
    const bw = (W - m.l - m.r) / n;
    for (const v of yt.ticks) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), style: `stroke:var(${v === 0 ? "--axis" : "--grid"})` }, svg);
      el("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = (o.yfmt || fmt)(v);
    }
    const tip = tipBox(host);
    vals.forEach((v, i) => {
      if (i % (o.labelEvery || 3) === 0) el("text", { x: m.l + bw * (i + 0.5), y: H - 6, "text-anchor": "middle" }, svg).textContent = o.labels[i];
      if (!v) return;
      const y = Math.min(Y(v), Y(0)), h = Math.max(1, Math.abs(Y(v) - Y(0)));
      const r = Math.min(4, bw / 4, h);
      const x = m.l + bw * i + 1, w = bw - 2;
      // rounded data end, square at the baseline
      const d = v > 0
        ? `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
        : `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`;
      const bar = el("path", { d, style: `fill:var(${o.color(v)})` }, svg);
      const hitR = el("rect", { x: m.l + bw * i, y: m.t, width: bw, height: H - m.t - m.b, fill: "transparent" }, svg);
      hitR.addEventListener("pointermove", (ev) => {
        const rr = svg.getBoundingClientRect();
        showTip(host, tip, ((m.l + bw * (i + 0.5)) * rr.width) / W, ev.clientY - rr.top, o.labels[i], o.tip(v, i));
        bar.style.opacity = 0.8;
      });
      hitR.addEventListener("pointerleave", () => { tip.style.display = "none"; bar.style.opacity = 1; });
    });
  };
  draw();
  renders.push(draw);
}

// ---------------------------------------------------------------- diverging stacked bars
// groups: [{label, color, values}]; positives stack up from zero, negatives down; a tick marks the net
function stackedBars(host, o) {
  const draw = () => {
    host.innerHTML = "";
    const W = host.clientWidth || 600, H = o.height || 280, m = { l: 52, r: 8, t: 10, b: 26 };
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.aria || "" }, host);
    const n = o.labels.length;
    const up = o.labels.map((_, i) => sum(o.groups.map((g) => Math.max(0, g.values[i]))));
    const dn = o.labels.map((_, i) => sum(o.groups.map((g) => Math.min(0, g.values[i]))));
    const yt = niceTicks(Math.min(0, ...dn), Math.max(0, ...up), 4);
    const Y = (v) => m.t + (1 - (v - yt.lo) / (yt.hi - yt.lo)) * (H - m.t - m.b);
    const bw = (W - m.l - m.r) / n;
    for (const v of yt.ticks) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), style: `stroke:var(${v === 0 ? "--axis" : "--grid"})` }, svg);
      el("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }, svg).textContent = (o.yfmt || fmt)(v);
    }
    const tip = tipBox(host);
    for (let i = 0; i < n; i++) {
      if (i % (W < 520 ? 6 : 3) === 0) el("text", { x: m.l + bw * (i + 0.5), y: H - 6, "text-anchor": "middle" }, svg).textContent = o.labels[i];
      let pos = 0, neg = 0;
      const x = m.l + bw * i + 1, w = Math.max(1, bw - 2);
      for (const g of o.groups) {
        const v = g.values[i];
        if (!v) continue;
        const a = v > 0 ? pos : neg, b = a + v;
        if (v > 0) pos = b; else neg = b;
        // 2px surface gap between stacked segments
        el("rect", { x, y: Math.min(Y(a), Y(b)), width: w, height: Math.max(0.5, Math.abs(Y(b) - Y(a))), style: `fill:var(${g.color});stroke:var(--surface)`, "stroke-width": 1 }, svg);
      }
      const net = sum(o.groups.map((g) => g.values[i]));
      el("line", { x1: x, x2: x + w, y1: Y(net), y2: Y(net), style: "stroke:var(--ink)", "stroke-width": 2 }, svg);
      const hit = el("rect", { x: m.l + bw * i, y: m.t, width: bw, height: H - m.t - m.b, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", (ev) => {
        const rr = svg.getBoundingClientRect();
        const rows = o.groups.map((g) => ({ label: g.label, value: (g.values[i] >= 0 ? "+" : "−") + fmt(Math.abs(g.values[i])), color: g.color, v: g.values[i] }))
          .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
        rows.push({ label: "Net fark", value: (net >= 0 ? "+" : "−") + TL(Math.abs(net)) });
        showTip(host, tip, ((m.l + bw * (i + 0.5)) * rr.width) / W, ev.clientY - rr.top, o.labels[i], rows);
      });
      hit.addEventListener("pointerleave", () => (tip.style.display = "none"));
    }
  };
  draw();
  renders.push(draw);
}

// ---------------------------------------------------------------- horizontal waterfall
function waterfall(host, start, steps, end) {
  const draw = () => {
    host.innerHTML = "";
    const rows = [{ label: "Normal bir gün", from: 0, to: start.value, kind: "total" }];
    let run = start.value;
    for (const s of steps) { rows.push({ label: s.label, from: run, to: run + s.value, kind: s.value >= 0 ? "up" : "down", v: s.value }); run += s.value; }
    rows.push({ label: "Yarın (tahmin)", from: 0, to: end.value, kind: "total" });
    const W = host.clientWidth || 600, rh = 34, labW = Math.min(170, W * 0.36), m = { l: labW, r: 70, t: 6, b: 24 };
    const H = m.t + m.b + rows.length * rh;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": "Fiyat farkının sürücülere göre ayrışımı" }, host);
    const lo = Math.min(...rows.map((r) => Math.min(r.from, r.to))), hi = Math.max(...rows.map((r) => Math.max(r.from, r.to)));
    // bars that show totals must start at zero, so the axis does too
    const xt = niceTicks(Math.min(0, lo), hi, 4);
    const X = (v) => m.l + ((v - xt.lo) / (xt.hi - xt.lo)) * (W - m.l - m.r);
    for (const v of xt.ticks) {
      el("line", { x1: X(v), x2: X(v), y1: m.t, y2: H - m.b, style: "stroke:var(--grid)" }, svg);
      el("text", { x: X(v), y: H - 6, "text-anchor": "middle" }, svg).textContent = fmt(v);
    }
    const tip = tipBox(host);
    rows.forEach((r, i) => {
      const y = m.t + i * rh + 7, h = rh - 14;
      const t = el("text", { x: 0, y: y + h / 2 + 4, style: "fill:var(--ink-2);font-size:13px" }, svg);
      t.textContent = r.label;
      const a = r.kind === "total" ? 0 : r.from;
      const x = X(Math.min(a, r.to)), w = Math.max(2, Math.abs(X(r.to) - X(a)));
      const color = r.kind === "total" ? "--ink-2" : r.kind === "up" ? "--up" : "--down";
      el("rect", { x, y, width: w, height: h, rx: 4, style: `fill:var(${color})`, opacity: r.kind === "total" ? 0.55 : 1 }, svg);
      const val = el("text", { x: x + w + 6, y: y + h / 2 + 4, style: "fill:var(--ink);font-size:12px" }, svg);
      val.textContent = r.kind === "total" ? TL(r.to) : (r.v >= 0 ? "+" : "−") + fmt(Math.abs(r.v));
      if (i < rows.length - 1) {
        const nx = X(r.to);
        el("line", { x1: nx, x2: nx, y1: y + h, y2: y + rh, style: "stroke:var(--axis)", "stroke-dasharray": "2 2" }, svg);
      }
      const hit = el("rect", { x: 0, y: m.t + i * rh, width: W, height: rh, fill: "transparent" }, svg);
      hit.addEventListener("pointermove", (ev) => {
        const rr = svg.getBoundingClientRect();
        const body = r.kind === "total"
          ? [{ label: "Günlük ortalama", value: TL(r.to) }]
          : [{ label: r.v >= 0 ? "Fiyatı artırıyor" : "Fiyatı düşürüyor", value: (r.v >= 0 ? "+" : "−") + TL(Math.abs(r.v)) }];
        showTip(host, tip, ev.clientX - rr.left, ev.clientY - rr.top, r.label, body);
      });
      hit.addEventListener("pointerleave", () => (tip.style.display = "none"));
    });
  };
  draw();
  renders.push(draw);
}

// ---------------------------------------------------------------- market clearing (mirror of market.py)
function interp(x, xp, fp) {
  if (x <= xp[0]) return fp[0];
  if (x >= xp[xp.length - 1]) return fp[fp.length - 1];
  let i = 1; while (xp[i] < x) i++;
  return fp[i - 1] + ((x - xp[i - 1]) / (xp[i] - xp[i - 1])) * (fp[i] - fp[i - 1]);
}
function clear(sup, dem, ds = 0, dd = 0) {
  const grid = [...new Set([...sup.price, ...dem.price])].sort((a, b) => a - b);
  let prev = null;
  for (let k = 0; k < grid.length; k++) {
    const s = interp(grid[k], sup.price, sup.mw) + ds, d = interp(grid[k], dem.price, dem.mw) + dd, gap = d - s;
    if (gap <= 0) {
      if (!prev || prev.gap === gap) return { price: grid[k], mw: s };
      const t = prev.gap / (prev.gap - gap);
      return { price: prev.p + t * (grid[k] - prev.p), mw: prev.s + t * (s - prev.s) };
    }
    prev = { p: grid[k], s, gap };
  }
  return { price: grid[grid.length - 1], mw: prev.s, capped: true };
}

// ---------------------------------------------------------------- page
const HOURS = [...Array(24).keys()];
const hourTicks = (a, b, W) => (W < 520 ? [0, 6, 12, 18] : [0, 3, 6, 9, 12, 15, 18, 21]);
const dayName = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("tr-TR", { weekday: "long", day: "numeric", month: "long" });
const NAMES = { "same hour today": "Bugünün aynı saati", "same hour last week": "Geçen haftanın aynı saati", "TEİAŞ load plan": "TEİAŞ yük planı", "EPİAŞ wind forecast": "EPİAŞ rüzgâr tahmini", "same hour 2 days ago": "2 gün önceki aynı saat" };
const UNITS = { price: "TL/MWh", consumption: "MWh", wind: "MWh", solar: "MWh", carbon: "g/kWh" };
// consecutive hours as ranges: [10, 11, 12, 13] -> "10:00–14:00"
const runs = (hs) => {
  const out = [];
  hs.slice().sort((a, b) => a - b).forEach((h) => {
    const last = out[out.length - 1];
    if (last && h === last[1] + 1) last[1] = h; else out.push([h, h]);
  });
  return out.map(([a, b]) => (a === b ? hh(a) : `${hh(a)}–${hh(b + 1)}`)).join(", ");
};
const TNAME = { price: "Fiyat", consumption: "Tüketim", wind: "Rüzgâr", solar: "Güneş", carbon: "Karbon" };

async function main() {
  const get = (p) => fetch(p, { cache: "no-cache" }).then((r) => r.json());
  const [F, Hs, B, S] = await Promise.all([get("data/forecast.json"), get("data/history.json"), get("data/battery.json"), get("data/scoreboard.json")]);
  const P = F.price, E = F.explain;
  const avg = mean(P.q50), today = F.benchmarks.price["same hour today"];
  const peakH = P.q50.indexOf(Math.max(...P.q50)), lowH = P.q50.indexOf(Math.min(...P.q50));

  $("#title").textContent = `${dayName(F.day)}: elektrik ne kadar?`;
  const diff = avg - E.normal;
  $("#lede").textContent = `Yarın ortalama ${TL(avg)}/MWh bekleniyor; son dört haftanın normal bir gününden ${TL(Math.abs(diff))} ${diff >= 0 ? "pahalı" : "ucuz"}. En pahalı saat ${hh(peakH)}, en ucuz saat ${hh(lowH)}.` + (F.inputs.holiday ? ` Takvim: ${F.inputs.holiday}.` : "");
  $("#hero").innerHTML = `
    <div><div class="stat label">Günlük ortalama</div><div class="big">${fmt(avg)}<span class="unit">TL/MWh</span></div></div>
    <div class="stat"><div class="label">Bugüne göre</div><div class="value ${avg >= mean(today) ? "delta-up" : "delta-down"}">${avg >= mean(today) ? "▲" : "▼"} ${fmt(Math.abs((avg / mean(today) - 1) * 100), 1)} %</div></div>
    <div class="stat"><div class="label">Zirve (${hh(peakH)})</div><div class="value">${fmt(P.q50[peakH])}</div></div>
    <div class="stat"><div class="label">%80 aralık, günlük</div><div class="value">${fmt(mean(P.q10))} – ${fmt(mean(P.q90))}</div></div>`;

  // tomorrow at a glance
  const share = (a, b) => (sum(a) / sum(b)) * 100;
  const bh0 = F.best_hours;
  const kpi = (label, value, hint = "") => `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
  $("#kpis").innerHTML = [
    kpi("En ucuz saatler", runs(bh0.cheapest), "fiyata göre"),
    kpi("Rüzgârın payı", "%" + fmt(share(F.wind.q50, F.consumption.q50)), fmt(sum(F.wind.q50) / 1000, 1) + " GWh"),
    kpi("Güneşin payı (lisanslı)", "%" + fmt(share(F.solar.q50, F.consumption.q50), 1), fmt(sum(F.solar.q50) / 1000, 1) + " GWh"),
    kpi("Tüketim", fmt(sum(F.consumption.q50) / 1000) + " GWh", "zirve " + fmt(Math.max(...F.consumption.q50) / 1000, 1) + " GW"),
    kpi("Karbon yoğunluğu", fmt(mean(F.carbon.q50)) + " g/kWh", "en temiz " + hh(bh0.cleanest[0])),
    kpi("Batarya kazancı", TL(F.battery.expected_tl), "10 MWh, beklenen"),
  ].join("");

  lineChart($("#c-price"), {
    aria: "Yarının saatlik fiyat tahmini", zero: true, xTicks: hourTicks, xfmt: hh, tipTitle: (x) => hh(x),
    series: [
      { kind: "line", x: HOURS, y: P.q50, color: "--s1", label: "Puant", width: 2.5 },
      { kind: "band", x: HOURS, lo: P.q10, hi: P.q90, label: "%80 aralık" },
      { kind: "line", x: HOURS, y: today, color: "--muted", dash: true, label: "Bugün", width: 1.5 },
    ],
  });
  $("#t-price").innerHTML = `<thead><tr><th>Saat</th><th>%10</th><th>Tahmin</th><th>%90</th><th>Bugün</th></tr></thead><tbody>` +
    HOURS.map((h) => `<tr><td>${hh(h)}</td><td>${fmt(P.q10[h])}</td><td><b>${fmt(P.q50[h])}</b></td><td>${fmt(P.q90[h])}</td><td>${fmt(today[h])}</td></tr>`).join("") + "</tbody>";

  // segmented toggles: show one pane, redraw charts that were hidden (they had no width)
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.for;
    b.parentElement.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(`[data-pane^="${k}-"]`).forEach((p) => (p.hidden = p.dataset.pane !== `${k}-${b.dataset.view}`));
    if (k === "explain") $("#explain-title").textContent = b.dataset.view === "daily" ? "Günlük ortalama" : "Saat saat";
    renders.forEach((r) => r());
  }));

  // yesterday: the last fully observed day of the record
  const HP = Hs.price, byDay = {};
  HP.time.forEach((t, i) => { const d = t.slice(0, 10); (byDay[d] = byDay[d] || []).push(i); });
  const done = Object.keys(byDay).filter((d) => byDay[d].length === 24 && byDay[d].every((i) => HP.actual[i] != null));
  if (done.length > 1) {
    const d = done[done.length - 1], idx = byDay[d], prev = byDay[done[done.length - 2]];
    const q50 = idx.map((i) => HP.q50[i]), act = idx.map((i) => HP.actual[i]);
    const lo = idx.map((i) => HP.q10[i]), hi = idx.map((i) => HP.q90[i]);
    const naive = prev.map((i) => HP.actual[i]);
    const mae = mean(q50.map((v, k) => Math.abs(v - act[k]))), nmae = mean(naive.map((v, k) => Math.abs(v - act[k])));
    const inside = act.filter((v, k) => v >= lo[k] && v <= hi[k]).length;
    const live = idx.some((i) => HP.live[i]);
    $("#dun-sub").textContent = `${dayName(d)} için ${live ? "canlı tahmin" : "geriye dönük testteki tahmin"} ve gerçekleşen PTF. Her gün bu karşılaştırma karneye eklenir.`;
    $("#dun-hero").innerHTML = `<div class="stat"><div class="label">Puant ortalama hata</div><div class="value">${TL(mae)}</div></div>
      <div class="stat"><div class="label">"Dünün fiyatı" kıyası</div><div class="value">${TL(nmae)}</div></div>
      <div class="stat"><div class="label">%80 aralıkta kalan saat</div><div class="value">${inside} / 24</div></div>
      <div class="stat"><div class="label">Gerçekleşen ortalama</div><div class="value">${TL(mean(act))}</div></div>`;
    lineChart($("#c-dun"), {
      aria: "Dünün tahmini ve gerçekleşen fiyat", height: 220, zero: true, xTicks: hourTicks, xfmt: hh, tipTitle: (x) => hh(x),
      series: [
        { kind: "line", x: HOURS, y: q50, color: "--s1", label: "Puant", width: 2 },
        { kind: "band", x: HOURS, lo, hi, label: "%80 aralık" },
        { kind: "line", x: HOURS, y: act, color: "--s2", label: "Gerçekleşen", width: 2 },
      ],
    });
  } else {
    $("#dun").hidden = true;
  }

  // why
  const groups = E.groups.filter((g) => Math.abs(g.daily) >= 0.5);
  $("#explain-sub").textContent = `Normal bir gün (son 28 günün aynı saatleri) ${TL(E.normal)}/MWh olurdu. Yarının farkı sürücülere bölündü; ${groups[0].daily >= 0 ? "en çok artıran" : "en çok etkileyen"}: ${groups[0].label.toLowerCase()}. Kırmızı fiyatı artırır, mavi düşürür.`;
  waterfall($("#c-explain"), { value: E.normal }, groups.map((g) => ({ label: g.label, value: g.daily })), { value: E.forecast });
  // hourly: the five largest groups by hourly magnitude in fixed slot order, the rest as "Diğer"
  const ranked = E.groups.slice().sort((a, b) => sum(b.hourly.map(Math.abs)) - sum(a.hourly.map(Math.abs)));
  const top = ranked.slice(0, 5), rest = ranked.slice(5);
  const hg = top.map((g, k) => ({ label: g.label, color: "--c" + (k + 1), values: g.hourly }));
  if (rest.length) hg.push({ label: "Diğer", color: "--muted", values: HOURS.map((h) => sum(rest.map((g) => g.hourly[h]))) });
  $("#lg-explain-h").innerHTML = hg.map((g) => `<span><i style="background:var(${g.color});height:10px"></i>${g.label}</span>`).join("") + `<span><i style="background:var(--ink)"></i>Net fark</span>`;
  stackedBars($("#c-explain-h"), { aria: "Saatlik sürücü katkıları", labels: HOURS.map(hh), groups: hg, yfmt: (v) => fmt(v) });

  // scenarios
  const T = [-4, -2, 0, 2, 4], Wm = [0.5, 0.75, 1, 1.25, 1.5], G = [0.8, 1, 1.2];
  const base = F.scenarios.find((s) => s.temp === 0 && s.wind === 1 && s.gas === 1);
  const scenHost = $("#c-scen");
  const updScen = () => {
    const t = T[+$("#s-temp").value], w = Wm[+$("#s-wind").value], g = G[+$("#s-gas").value];
    $("#o-temp").textContent = (t > 0 ? "+" : t < 0 ? "−" : "") + Math.abs(t) + " °C";
    $("#o-wind").textContent = w === 1 ? "tahmin kadar" : (w > 1 ? "+" : "−") + Math.abs(Math.round((w - 1) * 100)) + " %";
    $("#o-gas").textContent = g === 1 ? "aynı" : (g > 1 ? "+" : "−") + Math.abs(Math.round((g - 1) * 100)) + " %";
    const s = F.scenarios.find((x) => x.temp === t && x.wind === w && x.gas === g);
    const d = s.mean - base.mean;
    // heatmap at the chosen gas price: rows warm → cold, columns calm → windy; diverging around zero
    const cells = F.scenarios.filter((x) => x.gas === g), span0 = Math.max(1, ...cells.map((x) => Math.abs(x.mean - base.mean)));
    let gh = `<div></div>` + Wm.map((w2) => `<div class="h">${w2 === 1 ? "tahmin" : (w2 > 1 ? "+" : "−") + Math.abs(Math.round((w2 - 1) * 100)) + "%"}</div>`).join("");
    for (const t2 of T.slice().reverse()) {
      gh += `<div class="r">${t2 > 0 ? "+" : t2 < 0 ? "−" : ""}${Math.abs(t2)} °C</div>`;
      for (const w2 of Wm) {
        const c = cells.find((x) => x.temp === t2 && x.wind === w2), dd = c.mean - base.mean;
        const pct = Math.round((Math.abs(dd) / span0) * 70);
        const bg = Math.abs(dd) < 0.5 ? "var(--chip)" : `color-mix(in oklab, var(${dd > 0 ? "--up" : "--down"}) ${pct}%, var(--chip))`;
        gh += `<button type="button" data-t="${T.indexOf(t2)}" data-w="${Wm.indexOf(w2)}" class="${t2 === t && w2 === w ? "sel" : ""}" style="background:${bg}" title="${t2} °C, rüzgâr ×${w2}: ${TL(c.mean)}">${dd >= 0 ? "+" : "−"}${fmt(Math.abs(dd))}</button>`;
      }
    }
    gh += `<div></div><div class="axis">Rüzgâr →</div>`;
    $("#scen-grid").innerHTML = gh;
    $("#scen-grid").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      $("#s-temp").value = b.dataset.t; $("#s-wind").value = b.dataset.w; updScen();
    }));
    $("#scen-hero").innerHTML = `<div><div class="stat label">Senaryoda günlük ortalama</div><div class="big" style="font-size:34px">${fmt(s.mean)}<span class="unit">TL/MWh</span></div></div>
      <div class="stat"><div class="label">Temel tahmine göre</div><div class="value ${d > 0.5 ? "delta-up" : d < -0.5 ? "delta-down" : ""}">${d >= 0 ? "+" : "−"}${fmt(Math.abs(d))} TL (${d >= 0 ? "+" : "−"}${fmt(Math.abs((d / base.mean) * 100), 1)} %)</div></div>`;
    drop(scenHost);
    scenHost._draw = lineChart(scenHost, {
      aria: "Senaryo fiyatı", zero: true, xTicks: hourTicks, xfmt: hh,
      series: [
        { kind: "line", x: HOURS, y: s.hourly, color: "--s1", label: "Senaryo", width: 2.5 },
        { kind: "line", x: HOURS, y: base.hourly, color: "--muted", dash: true, label: "Temel", width: 1.5 },
      ],
    });
  };
  ["#s-temp", "#s-wind", "#s-gas"].forEach((s) => $(s).addEventListener("input", updScen));
  updScen();

  // market
  const M = F.market, mktHost = $("#c-market");
  if (M) {
    const base0 = clear(M.supply, M.demand);
    $("#market-sub").textContent = `${dayName(M.date)} saat ${hh(M.hour)} ihalesinin gerçek toplam arz ve talep eğrileri (yarının tahmini zirve saati). İki eğrinin kesiştiği yer takas fiyatıdır: ${TL(base0.price)}/MWh. Eğrileri kaydırarak ${"“"}2.000 MW daha az rüzgâr${"”"} gibi soruları gerçek tekliflerle yanıtlayın.`;
    const updMkt = () => {
      const ds = +$("#s-sup").value, dd = +$("#s-dem").value;
      $("#o-sup").textContent = (ds > 0 ? "+" : ds < 0 ? "−" : "") + fmt(Math.abs(ds)) + " MW";
      $("#o-dem").textContent = (dd > 0 ? "+" : dd < 0 ? "−" : "") + fmt(Math.abs(dd)) + " MW";
      const c = clear(M.supply, M.demand, ds, dd), d = c.price - base0.price;
      $("#mkt-hero").innerHTML = `<div><div class="stat label">Takas fiyatı</div><div class="big" style="font-size:34px">${fmt(c.price)}<span class="unit">TL/MWh</span></div></div>
        <div class="stat"><div class="label">Değişim</div><div class="value ${d > 0.5 ? "delta-up" : d < -0.5 ? "delta-down" : ""}">${d >= 0 ? "+" : "−"}${fmt(Math.abs(d))} TL</div></div>
        <div class="stat"><div class="label">Takas miktarı</div><div class="value">${fmt(c.mw)} MWh</div></div>` + (c.capped ? `<div class="stat"><div class="label">Durum</div><div class="value">⚠ Azami fiyatta</div></div>` : "");
      const sx = M.supply.mw.map((v) => v + ds), dx = M.demand.mw.map((v) => v + dd);
      drop(mktHost);
      const lo = Math.min(...sx, ...dx), hi = Math.max(...sx, ...dx);
      mktHost._draw = lineChart(mktHost, {
        aria: "Arz ve talep eğrileri", height: 300, xMin: Math.max(lo, c.mw - 12000), xMax: Math.min(hi, c.mw + 12000), yMin: 0,
        xfmt: (v) => fmt(v / 1000) + " GW", tipTitle: (x) => fmt(x) + " MWh",
        series: [
          { kind: "line", x: sx, y: M.supply.price, color: "--s1", label: "Arz fiyatı" },
          { kind: "line", x: dx, y: M.demand.price, color: "--s2", label: "Talep fiyatı" },
        ],
        after: (svg, X, Y) => {
          el("circle", { cx: X(c.mw), cy: Y(c.price), r: 6, style: "fill:var(--ink);stroke:var(--surface)", "stroke-width": 2 }, svg);
          const t = el("text", { x: X(c.mw) + 10, y: Y(c.price) - 10, style: "fill:var(--ink);font-size:12px;font-weight:600" }, svg);
          t.textContent = TL(c.price);
        },
      });
    };
    ["#s-sup", "#s-dem"].forEach((s) => $(s).addEventListener("input", updMkt));
    updMkt();
  } else {
    $("#piyasa").style.display = "none";
  }

  // other series
  for (const t of ["consumption", "wind", "solar", "carbon"]) {
    const s = F[t], bench = Object.entries(F.benchmarks[t] || {})[0];
    $("#lg-" + t).innerHTML = `<span><i style="background:var(--s1)"></i>Puant</span><span><i class="band"></i>%80</span>` + (bench ? `<span><i class="dash"></i>${NAMES[bench[0]] || bench[0]}</span>` : "");
    lineChart($("#c-" + t), {
      aria: TNAME[t] + " tahmini", height: 200, zero: t !== "consumption" && t !== "carbon", xTicks: hourTicks, xfmt: hh,
      series: [
        { kind: "line", x: HOURS, y: s.q50, color: "--s1", label: "Puant " + UNITS[t] },
        { kind: "band", x: HOURS, lo: s.q10, hi: s.q90, label: "%80 aralık" },
        ...(bench ? [{ kind: "line", x: HOURS, y: bench[1], color: "--muted", dash: true, width: 1.5, label: NAMES[bench[0]] || bench[0] }] : []),
      ],
    });
  }

  // best hours
  const ramp = (v, lo, hi, a, b) => {
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1)));
    const c = (x, y) => Math.round(x + (y - x) * t);
    return `rgb(${c(a[0], b[0])},${c(a[1], b[1])},${c(a[2], b[2])})`;
  };
  const blue = [[205, 226, 251], [16, 66, 129]], orange = [[251, 224, 212], [138, 44, 10]];
  const C = F.carbon.q50, bh = F.best_hours;
  let html = `<div></div>` + HOURS.map((h) => `<div class="hh">${h}</div>`).join("");
  html += `<div class="lab">Fiyat</div>` + HOURS.map((h) => `<div class="cell ${bh.cheapest.includes(h) ? "mark" : ""}" title="${hh(h)} · ${TL(P.q50[h])}/MWh" style="background:${ramp(P.q50[h], Math.min(...P.q50), Math.max(...P.q50), ...blue)}"></div>`).join("");
  html += `<div class="lab">Karbon</div>` + HOURS.map((h) => `<div class="cell ${bh.cleanest.includes(h) ? "mark" : ""}" title="${hh(h)} · ${fmt(C[h])} g/kWh" style="background:${ramp(C[h], Math.min(...C), Math.max(...C), ...orange)}"></div>`).join("");
  $("#hours").innerHTML = html;
  const span = (a) => a.map(hh).join(", ");
  $("#hour-chips").innerHTML = `<span class="chip">En ucuz: <b>${span(bh.cheapest)}</b></span><span class="chip">En temiz: <b>${span(bh.cleanest)}</b></span><span class="chip">İkisi birden: <b>${span(bh.both)}</b></span><span class="chip">Ortalama karbon: <b>${fmt(mean(C))} g/kWh</b></span>`;

  // battery
  const bat = F.battery, tot = B.total;
  $("#bat-sub").textContent = `${fmt(bat.energy_mwh)} MWh / ${fmt(bat.power_mw)} MW, %${fmt(bat.round_trip * 100)} çevrim verimli bir batarya her sabah yarının planını Puant fiyat tahminiyle yapar, gerçekleşen fiyattan alıp satar. Geriye dönük testte kusursuz öngörünün kazancının %${fmt((tot.forecast / tot.perfect) * 100)}'ini yakaladı; sabit "öğlen al, akşam sat" kuralının ${fmt(tot.forecast / tot.fixed_rule, 1)} katı. Yarın beklenen kazanç: ${TL(bat.expected_tl)}.`;
  barChart($("#c-batplan"), {
    aria: "Yarının batarya planı", values: bat.move, labels: HOURS.map(hh), color: (v) => (v > 0 ? "--s1" : "--s2"), yfmt: (v) => fmt(v, v % 1 ? 1 : 0),
    tip: (v, i) => [{ label: v > 0 ? "Şarj" : "Deşarj", value: fmt(Math.abs(v), 1) + " MWh" }, { label: "Tahmini fiyat", value: TL(P.q50[i]) }],
  });
  const bx = B.day.map((d) => new Date(d + "T12:00:00").getTime());
  const monthTicks = (a, b) => { const out = []; const d = new Date(a); d.setDate(1); d.setMonth(d.getMonth() + 1); while (d.getTime() <= b) { out.push(d.getTime()); d.setMonth(d.getMonth() + 4); } return out; };
  const mlab = (v) => new Date(v).toLocaleDateString("tr-TR", { month: "short", year: "2-digit" });
  lineChart($("#c-batcum"), {
    aria: "Bataryanın birikimli kazancı", zero: true, xTicks: monthTicks, xfmt: mlab,
    yfmt: (v) => fmt(v / 1e6, v % 1e6 ? 1 : 0) + " mn", tipTitle: (x) => new Date(x).toLocaleDateString("tr-TR"),
    series: [
      { kind: "line", x: bx, y: B.forecast, color: "--s1", label: "Puant", fmt: TL },
      { kind: "line", x: bx, y: B.perfect, color: "--s2", label: "Kusursuz öngörü", fmt: TL },
      { kind: "line", x: bx, y: B.fixed_rule, color: "--s3", label: "Sabit kural", fmt: TL },
    ],
  });

  // scoreboard
  const order = ["price", "consumption", "wind", "solar", "carbon"];
  let tb = `<table><thead><tr><th>Seri</th><th>Puant hatası</th><th>Kıyas</th><th>Kıyas hatası</th><th>İyileşme</th><th>%80 aralık tuttu</th></tr></thead><tbody>`;
  for (const t of order) {
    const r = S[t]; if (!r) continue;
    const benches = Object.entries(r).filter(([k]) => k !== "puant");
    const [bn, bv] = benches.reduce((a, b) => (b[1].mae < a[1].mae ? b : a));
    const imp = (1 - r.puant.mae / bv.mae) * 100;
    tb += `<tr><td>${TNAME[t]}</td><td>${fmt(r.puant.mae)} ${UNITS[t]}</td><td>${NAMES[bn] || bn}</td><td>${fmt(bv.mae)}</td><td class="${imp > 0 ? "win" : ""}">${imp > 0 ? "▲ " : ""}${fmt(imp)} %</td><td>${fmt(r.puant.coverage_80 * 100)} %</td></tr>`;
  }
  $("#score").innerHTML = tb + `</tbody></table><p class="note" style="margin:10px 0 0">Ortalama mutlak hata, Temmuz 2024'ten bugüne her saat için. Kıyas: en iyi resmî ya da basit tahmin.</p>`;

  const histHost = $("#c-hist"), monHost = $("#c-monthly");
  const updHist = () => {
    const t = $("#hist-target").value, h = Hs[t];
    const x = h.time.map((s) => new Date(s).getTime());
    drop(histHost); drop(monHost);
    histHost._draw = lineChart(histHost, {
      aria: "Son 60 gün tahmin ve gerçekleşen", zero: t !== "consumption" && t !== "carbon",
      xTicks: (a, b) => { const out = []; for (let v = a + 864e5 * 7; v < b; v += 864e5 * 14) out.push(v); return out; },
      xfmt: (v) => new Date(v).toLocaleDateString("tr-TR", { day: "numeric", month: "short" }),
      tipTitle: (v, i) => new Date(v).toLocaleString("tr-TR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) + (h.live[i] ? " · canlı" : ""),
      series: [
        { kind: "line", x, y: h.q50, color: "--s1", label: "Puant", width: 1.5 },
        { kind: "band", x, lo: h.q10, hi: h.q90 },
        { kind: "line", x, y: h.actual, color: "--s2", label: "Gerçekleşen", width: 1.5 },
      ],
    });
    const mon = h.monthly, names = Object.keys(mon[0] || {}).filter((k) => k !== "month");
    const colors = ["--s1", "--s2", "--s3"];
    $("#lg-monthly").innerHTML = names.map((n, i) => `<span><i style="background:var(${colors[i]})"></i>${n === "puant" ? "Puant" : NAMES[n] || n}</span>`).join("");
    const mx = mon.map((_, i) => i);
    monHost._draw = lineChart(monHost, {
      aria: "Aylık hata", height: 220, zero: true,
      xTicks: (a, b) => mx.filter((i) => i % 3 === 0), xfmt: (i) => (mon[i] ? new Date(mon[i].month + "-15").toLocaleDateString("tr-TR", { month: "short", year: "2-digit" }) : ""),
      tipTitle: (i) => new Date(mon[i].month + "-15").toLocaleDateString("tr-TR", { month: "long", year: "numeric" }),
      series: names.map((n, i) => ({ kind: "line", x: mx, y: mon.map((r) => r[n]), color: colors[i], label: n === "puant" ? "Puant" : NAMES[n] || n, width: n === "puant" ? 2.5 : 1.5 })),
    });
  };
  $("#hist-target").addEventListener("change", updHist);
  updHist();

  // highlight the section in view in the sticky menu
  const links = [...document.querySelectorAll("nav a")];
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) links.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + e.target.id));
  }), { rootMargin: "-40% 0px -55% 0px" });
  links.forEach((a) => { const s = document.querySelector(a.getAttribute("href")); if (s) io.observe(s); });

  $("#made").textContent ="Tahmin " + new Date(F.made).toLocaleString("tr-TR", { dateStyle: "long", timeStyle: "short" }) + " tarihinde üretildi";
}

// theme toggle: OS setting by default; the choice is remembered per browser
(function () {
  const root = document.documentElement;
  try { const t = localStorage.getItem("puant-theme"); if (t) root.dataset.theme = t; } catch (e) { /* storage blocked */ }
  $("#theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("puant-theme", root.dataset.theme); } catch (e) { /* storage blocked */ }
  });
})();

let rz;
addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => renders.forEach((r) => r()), 150); });
main().catch((e) => { $("#lede").textContent = "Veri yüklenemedi: " + e.message; console.error(e); });
