"use strict";

const STAGES = [
  ["checkout_initiated", "Checkout initiated"],
  ["method_selected", "Method selected"],
  ["details_entered", "Details entered"],
  ["authorization_requested", "Authorization requested"],
  ["payment_completed", "Payment completed"],
];
const COLORS = { success: "#1db7a6", failed: "#e45756", abandoned: "#f4a340", pending: "#8392ab" };
const FILTERS = [
  ["payment_method", "Payment method"],
  ["country", "Country"],
  ["currency", "Currency"],
  ["device_type", "Device"],
  ["browser", "Browser"],
  ["processor", "Processor"],
];
const SEGMENTS = [
  ["payment_method", "Payment method"],
  ["country", "Country"],
  ["currency", "Currency"],
  ["device_type", "Device"],
  ["browser", "Browser"],
  ["processor", "Processor"],
];

let allSessions = [];
let currentSessions = [];
let sourceDescription = "Included deterministic demonstration data";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[character]);
const percentage = (part, total) => (total ? (100 * part) / total : 0);
const formatPercent = (value) => `${Number(value).toFixed(1)}%`;
const displayName = (value) => String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 3200);
}

function groupBy(rows, key) {
  return rows.reduce((groups, row) => {
    const value = row[key] || "Unknown";
    (groups[value] ||= []).push(row);
    return groups;
  }, {});
}

function normalizeSession(session) {
  const normalized = { ...session };
  ["completed", "used_3ds", "has_duplicate", "has_out_of_order", "has_unknown_event", ...STAGES.map(([stage]) => `reached_${stage}`)]
    .forEach((field) => { normalized[field] = normalized[field] === true || normalized[field] === 1 || normalized[field] === "true"; });
  ["amount", "session_seconds", "three_ds_seconds", "max_gap_seconds", "event_count"]
    .forEach((field) => { normalized[field] = Number(normalized[field] || 0); });
  normalized.date = String(normalized.date || normalized.started_at || "").slice(0, 10);
  return normalized;
}

function populateControls() {
  const filterContainer = $("#filters");
  filterContainer.replaceChildren();
  FILTERS.forEach(([field, label]) => {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const select = document.createElement("select");
    select.id = `filter-${field}`;
    select.innerHTML = '<option value="">All</option>';
    [...new Set(allSessions.map((session) => session[field]).filter(Boolean))].sort().forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = displayName(value);
      select.append(option);
    });
    select.addEventListener("change", renderAll);
    wrapper.append(select);
    filterContainer.append(wrapper);
  });

  const dates = allSessions.map((session) => session.date).filter(Boolean).sort();
  if (dates.length) {
    $("#date-from").min = dates[0];
    $("#date-from").max = dates.at(-1);
    $("#date-from").value = dates[0];
    $("#date-to").min = dates[0];
    $("#date-to").max = dates.at(-1);
    $("#date-to").value = dates.at(-1);
  }

  const dimension = $("#segment-dimension");
  dimension.innerHTML = "";
  SEGMENTS.forEach(([value, label]) => dimension.add(new Option(label, value)));

  const outcomeFilter = $("#outcome-filter");
  outcomeFilter.innerHTML = '<option value="">All outcomes</option>';
  [...new Set(allSessions.map((session) => session.outcome))].sort().forEach((value) => {
    outcomeFilter.add(new Option(displayName(value), value));
  });
}

function filteredSessions() {
  const start = $("#date-from").value;
  const end = $("#date-to").value;
  return allSessions.filter((session) => {
    if (start && session.date < start) return false;
    if (end && session.date > end) return false;
    return FILTERS.every(([field]) => {
      const selected = $(`#filter-${field}`)?.value;
      return !selected || session[field] === selected;
    });
  });
}

function funnelMetrics(sessions) {
  let previous = sessions.length;
  return STAGES.map(([stage, label], index) => {
    const reached = sessions.filter((session) => session[`reached_${stage}`]).length;
    const denominator = index === 0 ? sessions.length : previous;
    const result = { stage, label, reached, drop: Math.max(0, denominator - reached), dropRate: percentage(Math.max(0, denominator - reached), denominator) };
    previous = reached;
    return result;
  });
}

function renderMetrics(sessions) {
  const completed = sessions.filter((session) => session.completed).length;
  const failed = sessions.filter((session) => session.outcome === "failed").length;
  const pending = sessions.filter((session) => session.outcome === "pending").length;
  const values = [
    ["Checkout sessions", sessions.length.toLocaleString(), "Current cohort"],
    ["Completion rate", formatPercent(percentage(completed, sessions.length)), `${completed.toLocaleString()} paid`],
    ["Non-completion", formatPercent(percentage(sessions.length - completed, sessions.length)), `${(sessions.length - completed).toLocaleString()} sessions`],
    ["Failed authorizations", failed.toLocaleString(), "Declined payments"],
    ["Pending", pending.toLocaleString(), "Awaiting confirmation"],
  ];
  $("#metrics").innerHTML = values.map(([label, value, note]) => `<article class="metric"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  $("#cohort-pill").textContent = `${sessions.length.toLocaleString()} sessions in current view`;
}

function renderFunnel(sessions) {
  const funnel = funnelMetrics(sessions);
  $("#funnel").innerHTML = funnel.map((item) => `
    <div class="funnel-row">
      <span class="funnel-label">${item.label}</span>
      <div class="funnel-track"><div class="funnel-fill" style="width:${percentage(item.reached, sessions.length)}%"></div></div>
      <span class="funnel-value">${item.reached.toLocaleString()} · ${formatPercent(percentage(item.reached, sessions.length))}</span>
    </div>`).join("");
  const largest = funnel.slice(1).sort((left, right) => right.drop - left.drop)[0];
  $("#largest-drop").textContent = largest
    ? `Largest transition loss: ${largest.drop.toLocaleString()} sessions before ${largest.label.toLowerCase()} (${formatPercent(largest.dropRate)}).`
    : "";
}

function renderOutcomes(sessions) {
  const counts = Object.fromEntries(Object.keys(COLORS).map((outcome) => [outcome, sessions.filter((session) => session.outcome === outcome).length]));
  let running = 0;
  const gradient = Object.entries(counts).map(([outcome, count]) => {
    const start = running;
    running += percentage(count, sessions.length);
    return `${COLORS[outcome]} ${start}% ${running}%`;
  }).join(", ");
  $("#outcomes").innerHTML = `
    <div class="outcome-wrap">
      <div class="donut" style="background:conic-gradient(${gradient})"><div class="donut-center"><strong>${formatPercent(percentage(counts.success, sessions.length))}</strong><span>completed</span></div></div>
      <div class="legend">${Object.entries(counts).map(([outcome, count]) => `<span><i style="background:${COLORS[outcome]}"></i>${displayName(outcome)} · ${count}</span>`).join("")}</div>
    </div>`;
}

function renderTrend(sessions) {
  const days = Object.entries(groupBy(sessions, "date")).sort(([left], [right]) => left.localeCompare(right)).map(([date, rows]) => ({ date, rate: percentage(rows.filter((row) => row.completed).length, rows.length) }));
  if (!days.length) { $("#trend").innerHTML = "<p>No sessions in this cohort.</p>"; return; }
  const width = 900; const height = 210; const padding = 20;
  const x = (index) => days.length === 1 ? width / 2 : padding + (index * (width - padding * 2)) / (days.length - 1);
  const y = (rate) => height - padding - (rate / 100) * (height - padding * 2);
  const points = days.map((day, index) => `${x(index)},${y(day.rate)}`).join(" ");
  const area = `${padding},${height - padding} ${points} ${width - padding},${height - padding}`;
  $("#trend").innerHTML = `
    <svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Daily completion-rate trend">
      <defs><linearGradient id="area-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#3766e8" stop-opacity=".24"/><stop offset="100%" stop-color="#3766e8" stop-opacity=".02"/></linearGradient></defs>
      ${[25, 50, 75].map((rate) => `<line class="gridline" x1="${padding}" y1="${y(rate)}" x2="${width - padding}" y2="${y(rate)}"/>`).join("")}
      <polygon class="area" points="${area}"/><polyline class="line" points="${points}"/>
      ${days.map((day, index) => `<circle class="point"><title>${day.date}: ${formatPercent(day.rate)}</title></circle>`.replace('<circle class="point"', `<circle class="point" cx="${x(index)}" cy="${y(day.rate)}" r="4"`)).join("")}
    </svg><div class="trend-labels"><span>${days[0].date}</span><span>Completion rate · 0–100%</span><span>${days.at(-1).date}</span></div>`;
}

function cohortSummary(sessions, dimension) {
  return Object.entries(groupBy(sessions, dimension)).map(([name, rows]) => {
    const completed = rows.filter((row) => row.completed).length;
    return { name, sessions: rows.length, completed, failed: rows.filter((row) => row.outcome === "failed").length, rate: percentage(completed, rows.length) };
  }).sort((left, right) => left.rate - right.rate);
}

function renderSegments(sessions) {
  const dimension = $("#segment-dimension").value || "payment_method";
  const summary = cohortSummary(sessions, dimension);
  $("#segment-chart").innerHTML = summary.map((row) => `<div class="bar-row"><span class="bar-label">${escapeHtml(displayName(row.name))}</span><div class="bar-track"><div class="bar-fill" style="width:${row.rate}%"></div></div><span class="bar-value">${formatPercent(row.rate)}</span></div>`).join("") || "<p>No cohort data.</p>";
  $("#segment-table").innerHTML = table(
    ["Cohort", "Sessions", "Completed", "Failed", "Completion"],
    summary.map((row) => [displayName(row.name), row.sessions, row.completed, row.failed, formatPercent(row.rate)]),
  );
}

function worstCohort(sessions, dimension, minimum = 20) {
  const summary = cohortSummary(sessions, dimension).filter((row) => row.sessions >= minimum).map((row) => ({ ...row, incomplete: row.sessions - row.completed, incompleteRate: 100 - row.rate }));
  return summary.sort((left, right) => right.incompleteRate - left.incompleteRate)[0];
}

function calculateInsights(sessions) {
  if (!sessions.length) return [];
  const baseline = percentage(sessions.filter((session) => !session.completed).length, sessions.length);
  const candidates = [];
  const failed = sessions.filter((session) => session.outcome === "failed" && session.decline_code);
  const declines = Object.entries(groupBy(failed, "decline_code")).map(([code, rows]) => [code, rows.length]).sort((left, right) => right[1] - left[1]);
  if (declines.length) {
    const [code, count] = declines[0];
    candidates.push({ score: count, category: "Decline code", title: `${displayName(code)} is the leading authorization decline`, evidence: `${count} of ${failed.length} failed authorizations (${formatPercent(percentage(count, failed.length))}) returned ${code}.`, action: code === "fraud_suspected" ? "Review fraud-rule precision and provide a safe verification fallback." : "Add recovery routing and offer an alternate payment method." });
  }
  const threeDs = sessions.filter((session) => session.used_3ds);
  const threeDsAbandoned = threeDs.filter((session) => session.outcome === "abandoned" && session.drop_off_before === "payment_completed");
  const nonThreeDsCards = sessions.filter((session) => session.payment_method?.startsWith("card_") && !session.used_3ds);
  if (threeDs.length && threeDsAbandoned.length) {
    const rate = percentage(threeDsAbandoned.length, threeDs.length);
    const comparison = percentage(nonThreeDsCards.filter((session) => session.outcome === "abandoned").length, nonThreeDsCards.length);
    candidates.push({ score: threeDsAbandoned.length * rate / 100, category: "3DS", title: "3DS introduces a concentrated abandonment point", evidence: `${threeDsAbandoned.length} of ${threeDs.length} 3DS sessions (${formatPercent(rate)}) were abandoned vs ${formatPercent(comparison)} without 3DS.`, action: "Reduce redirect latency, retain checkout context, and instrument issuer-return failures." });
  }
  [
    ["processor", "Processor", "Review routing health, timeouts, and failover for this processor."],
    ["payment_method", "Payment method", "Optimize this method-specific flow and offer a fallback method."],
    ["country", "Geography", "Inspect local payment availability, currency handling, and regional routing."],
  ].forEach(([dimension, category, action]) => {
    const worst = worstCohort(sessions, dimension);
    if (worst && worst.incompleteRate > baseline) candidates.push({ score: worst.incomplete * (worst.incompleteRate - baseline) / 100, category, title: `${displayName(worst.name)} has elevated non-completion`, evidence: `${worst.incomplete} of ${worst.sessions} sessions did not complete (${formatPercent(worst.incompleteRate)} vs ${formatPercent(baseline)} overall).`, action });
  });
  const deviceRows = sessions.map((session) => ({ ...session, device_browser: `${session.device_type} / ${session.browser}` }));
  const worstDevice = worstCohort(deviceRows, "device_browser", 30);
  if (worstDevice && worstDevice.incompleteRate > baseline) candidates.push({ score: worstDevice.incomplete * (worstDevice.incompleteRate - baseline) / 100, category: "Device / browser", title: `${worstDevice.name} has elevated non-completion`, evidence: `${worstDevice.incomplete} of ${worstDevice.sessions} sessions did not complete (${formatPercent(worstDevice.incompleteRate)} vs ${formatPercent(baseline)} overall).`, action: "Reproduce and simplify the checkout form on this client combination." });
  return candidates.sort((left, right) => right.score - left.score).slice(0, 5);
}

function renderInsights(sessions) {
  const insights = calculateInsights(sessions);
  $("#insights").innerHTML = insights.map((insight, index) => `<article class="insight"><span class="insight-rank">Priority ${index + 1} · ${escapeHtml(insight.category)}</span><h3>${escapeHtml(insight.title)}</h3><p>${escapeHtml(insight.evidence)}</p><p class="action"><strong>Action:</strong> ${escapeHtml(insight.action)}</p></article>`).join("") || '<article class="card"><p>This cohort is too small to rank root causes reliably.</p></article>';
}

function anomalyRows(sessions) {
  return [
    ["Slow 3DS", sessions.filter((session) => session.three_ds_seconds > 8), "3DS completion took more than eight seconds."],
    ["Long event gap", sessions.filter((session) => session.max_gap_seconds > 15 * 60), "A checkout step took more than 15 minutes."],
    ["Duplicate events", sessions.filter((session) => session.has_duplicate), "Sessions contain repeated logical events."],
    ["Out-of-order events", sessions.filter((session) => session.has_out_of_order), "Event timestamps regress relative to source sequence."],
  ].filter(([, rows]) => rows.length).map(([type, rows, evidence]) => ({ type, count: rows.length, rate: percentage(rows.length, sessions.length), severity: type.includes("Slow") || type.includes("Long") ? "High" : "Medium", evidence }));
}

function renderAnomalies(sessions) {
  const anomalies = anomalyRows(sessions);
  const qualitySessions = new Set(sessions.filter((session) => session.has_duplicate || session.has_out_of_order).map((session) => session.session_id)).size;
  const values = [
    ["Anomaly groups", anomalies.length],
    ["High severity", anomalies.filter((row) => row.severity === "High").length],
    ["Quality flags", qualitySessions],
    ["Long gaps", sessions.filter((session) => session.max_gap_seconds > 900).length],
  ];
  $("#anomaly-metrics").innerHTML = values.map(([label, value]) => `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`).join("");
  $("#anomaly-table").innerHTML = table(["Type", "Severity", "Affected", "Rate", "Evidence"], anomalies.map((row) => [row.type, row.severity, row.count, formatPercent(row.rate), row.evidence]));
}

function table(headers, rows) {
  return `<div class="table-scroll"><table><thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function sessionRows(sessions) {
  const outcome = $("#outcome-filter").value;
  const search = $("#session-search").value.trim().toLowerCase();
  return sessions.filter((session) => (!outcome || session.outcome === outcome) && (!search || session.session_id.toLowerCase().includes(search)));
}

function renderSessionTable(sessions) {
  const rows = sessionRows(sessions);
  const visible = rows.slice(0, 100);
  $("#session-table").innerHTML = `${table(
    ["Session", "Date", "Method", "Country", "Processor", "Outcome", "Drop-off before", "Decline"],
    visible.map((session) => [session.session_id, session.date, displayName(session.payment_method), session.country, displayName(session.processor), displayName(session.outcome), displayName(session.drop_off_before || "—"), displayName(session.decline_code || "—")]),
  )}<p class="sidebar-note">Showing ${visible.length.toLocaleString()} of ${rows.length.toLocaleString()} matching sessions.</p>`;
}

function renderAll() {
  currentSessions = filteredSessions();
  renderMetrics(currentSessions);
  renderFunnel(currentSessions);
  renderOutcomes(currentSessions);
  renderTrend(currentSessions);
  renderSegments(currentSessions);
  renderInsights(currentSessions);
  renderAnomalies(currentSessions);
  renderSessionTable(currentSessions);
  $("#footer-source").textContent = `${sourceDescription} · ${allSessions.length.toLocaleString()} sessions · filters update every view`;
}

function resetFilters() {
  FILTERS.forEach(([field]) => { const control = $(`#filter-${field}`); if (control) control.value = ""; });
  const dates = allSessions.map((session) => session.date).filter(Boolean).sort();
  if (dates.length) { $("#date-from").value = dates[0]; $("#date-to").value = dates.at(-1); }
  $("#outcome-filter").value = "";
  $("#session-search").value = "";
  renderAll();
}

function downloadSessions() {
  const rows = sessionRows(currentSessions);
  const fields = ["session_id", "date", "payment_method", "country", "currency", "processor", "device_type", "browser", "outcome", "drop_off_before", "decline_code", "session_seconds"];
  const csv = [fields.join(","), ...rows.map((session) => fields.map((field) => `"${String(session[field] ?? "").replaceAll('"', '""')}"`).join(","))].join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  link.download = "payment_sessions_filtered.csv";
  link.click();
  URL.revokeObjectURL(link.href);
}

function parseCsv(text) {
  const rows = []; let row = []; let field = ""; let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
      else if (character === '"') quoted = false;
      else field += character;
    } else if (character === '"') quoted = true;
    else if (character === ",") { row.push(field); field = ""; }
    else if (character === "\n") { row.push(field.replace(/\r$/, "")); rows.push(row); row = []; field = ""; }
    else field += character;
  }
  if (field || row.length) { row.push(field.replace(/\r$/, "")); rows.push(row); }
  const headers = rows.shift()?.map((header) => header.trim()) || [];
  return rows.filter((values) => values.some(Boolean)).map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""])));
}

function reconstructEvents(events) {
  const required = ["session_id", "event_sequence", "timestamp_utc", "event_type", "payment_method", "currency", "country", "processor", "status", "decline_code", "device_type", "browser", "amount", "duration_ms"];
  if (!events.length || required.some((field) => !(field in events[0]))) throw new Error("CSV is missing one or more required event fields.");
  const grouped = groupBy(events, "session_id");
  return Object.entries(grouped).map(([sessionId, raw]) => {
    const source = raw.map((event, index) => ({ ...event, _source: index, event_sequence: Number(event.event_sequence), timestamp: new Date(event.timestamp_utc) }));
    if (source.some((event) => Number.isNaN(event.timestamp.getTime()))) throw new Error(`Invalid timestamp in ${sessionId}.`);
    const ordered = [...source].sort((left, right) => left.event_sequence - right.event_sequence || left._source - right._source);
    const signatures = ordered.map((event) => [event.session_id, event.event_sequence, event.timestamp_utc, event.event_type, event.status, event.decline_code].join("|"));
    const hasDuplicate = new Set(signatures).size !== signatures.length;
    const deduplicated = ordered.filter((_, index) => signatures.indexOf(signatures[index]) === index);
    const hasOutOfOrder = ordered.some((event, index) => index > 0 && event.timestamp < ordered[index - 1].timestamp);
    const types = new Set(deduplicated.map((event) => event.event_type));
    let contiguous = true; const reached = {};
    STAGES.forEach(([stage]) => { reached[stage] = contiguous && types.has(stage); contiguous = reached[stage]; });
    const authorization = deduplicated.filter((event) => event.event_type === "authorization_response");
    const completed = reached.payment_completed && deduplicated.some((event) => event.event_type === "payment_completed" && event.status === "success");
    const outcome = completed ? "success" : authorization.some((event) => event.status === "failed") ? "failed" : authorization.some((event) => event.status === "pending") ? "pending" : "abandoned";
    const first = deduplicated[0]; const timestamps = deduplicated.map((event) => event.timestamp.getTime());
    const gaps = deduplicated.slice(1).map((event, index) => Math.max(0, (event.timestamp - deduplicated[index].timestamp) / 1000));
    const redirect = deduplicated.find((event) => event.event_type === "3ds_redirect");
    const threeDsEnd = deduplicated.find((event) => event.event_type === "3ds_completed");
    return normalizeSession({
      session_id: sessionId, started_at: new Date(Math.min(...timestamps)).toISOString(), date: new Date(Math.min(...timestamps)).toISOString().slice(0, 10),
      payment_method: first.payment_method, currency: first.currency, country: first.country, processor: first.processor, device_type: first.device_type, browser: first.browser,
      amount: Number(first.amount), outcome, completed, drop_off_before: STAGES.find(([stage]) => !reached[stage])?.[0] || "",
      decline_code: deduplicated.find((event) => event.decline_code)?.decline_code || "", used_3ds: Boolean(redirect),
      three_ds_seconds: redirect && threeDsEnd ? Math.max(0, (threeDsEnd.timestamp - redirect.timestamp) / 1000) : 0,
      max_gap_seconds: gaps.length ? Math.max(...gaps) : 0, has_duplicate: hasDuplicate, has_out_of_order: hasOutOfOrder,
      has_unknown_event: deduplicated.some((event) => !new Set([...STAGES.map(([stage]) => stage), "3ds_redirect", "3ds_completed", "authorization_response"]).has(event.event_type)), event_count: deduplicated.length,
      ...Object.fromEntries(STAGES.map(([stage]) => [`reached_${stage}`, reached[stage]])),
    });
  });
}

async function handleUpload(file) {
  try {
    const events = parseCsv(await file.text());
    allSessions = reconstructEvents(events);
    sourceDescription = `Uploaded ${file.name}`;
    $("#source-label").textContent = `${file.name} · ${allSessions.length.toLocaleString()} sessions`;
    populateControls(); renderAll(); toast("Event log loaded successfully.");
  } catch (error) { toast(error.message || "The event log could not be loaded."); }
}

function bindEvents() {
  $$(".tab").forEach((button) => button.addEventListener("click", () => {
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
    $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === button.dataset.tab));
  }));
  ["#date-from", "#date-to"].forEach((selector) => $(selector).addEventListener("change", renderAll));
  $("#segment-dimension").addEventListener("change", () => renderSegments(currentSessions));
  $("#outcome-filter").addEventListener("change", () => renderSessionTable(currentSessions));
  $("#session-search").addEventListener("input", () => renderSessionTable(currentSessions));
  $("#reset-filters").addEventListener("click", resetFilters);
  $("#download-sessions").addEventListener("click", downloadSessions);
  $("#event-upload").addEventListener("change", (event) => { if (event.target.files[0]) handleUpload(event.target.files[0]); });
}

async function initialize() {
  bindEvents();
  try {
    const response = await fetch("/sessions.json");
    if (!response.ok) throw new Error(`Dataset request failed (${response.status}).`);
    allSessions = (await response.json()).map(normalizeSession);
    populateControls(); renderAll();
  } catch (error) {
    $("#cohort-pill").textContent = "Dataset unavailable";
    toast(error.message || "Unable to load the demonstration data.");
  }
}

initialize();

