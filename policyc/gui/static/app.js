// policyc demo GUI. Plain JS, no libraries: talks to the local JSON API in policyc/gui/server.py.
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { llm: "fake", status: null };

// ---- helpers -------------------------------------------------------------------

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function badge(text, kind) { return `<span class="badge b-${kind}">${esc(text)}</span>`; }
function pct(v) { return v == null ? "n/a" : `${v.toFixed(1)}%`; }
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("show"), 6000);
}
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: "bad response from the server" }));
  if (!res.ok) { const e = new Error(data.error || `HTTP ${res.status}`); e.data = data; throw e; }
  return data;
}
async function busy(button, label, fn) {
  const old = button.innerHTML; button.disabled = true;
  button.innerHTML = `<span class="spin"></span> ${esc(label)}`;
  try { return await fn(); }
  catch (e) { toast(e.message); return null; }
  finally { button.disabled = false; button.innerHTML = old; }
}
function stageKind(stage) {
  return { accepted: "ok", not_smaller: "warn", verifier: "err", compile: "err", declarations: "err" }[stage] || "skip";
}
const STAGE_LABEL = { accepted: "accepted", not_smaller: "not smaller", verifier: "rejected by verifier",
  compile: "does not compile", declarations: "changed declarations" };

// ---- editor: textarea + line-number gutter with diagnostic markers --------------

function makeEditor(host) {
  host.innerHTML = `<pre class="gutter"></pre><textarea spellcheck="false" autocapitalize="off"></textarea>`;
  const gutter = host.querySelector(".gutter"), ta = host.querySelector("textarea");
  const ed = {
    marks: {},
    get value() { return ta.value; },
    set value(v) { ta.value = v; ed.marks = {}; ed.render(); },
    render() {
      const n = ta.value.split("\n").length;
      let out = "";
      for (let i = 1; i <= n; i++) {
        const m = ed.marks[i];
        out += (m ? `<span class="${m}">${i}</span>` : i) + "\n";
      }
      gutter.innerHTML = out;
      gutter.scrollTop = ta.scrollTop;
    },
    mark(diags) {
      ed.marks = {};
      for (const d of diags || []) {
        if (d.severity === "error") ed.marks[d.line] = "bad";
        else if (!ed.marks[d.line]) ed.marks[d.line] = "warn";
      }
      ed.render();
    },
    goto(line) {
      const lines = ta.value.split("\n");
      let start = 0;
      for (let i = 0; i < line - 1 && i < lines.length; i++) start += lines[i].length + 1;
      ta.focus(); ta.setSelectionRange(start, start + (lines[line - 1] || "").length);
      ta.scrollTop = Math.max(0, (line - 4) * 20.8);
    },
    textarea: ta,
  };
  ta.addEventListener("input", () => { ed.marks = {}; ed.render(); });
  ta.addEventListener("scroll", () => { gutter.scrollTop = ta.scrollTop; });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const s = ta.selectionStart;
      ta.setRangeText("    ", s, ta.selectionEnd, "end"); ed.render();
    }
  });
  return ed;
}

function diagList(diags, editor) {
  if (!diags || !diags.length) return "";
  const html = diags.map((d, i) => `
    <div class="diag" data-i="${i}">
      ${badge(d.code, d.severity === "error" ? "err" : "warn")}
      <span class="where">line ${d.line}:${d.col}</span>
      <span class="msg">${esc(d.message)}</span>
      ${d.expected ? `<span class="extra">expected: ${esc(d.expected.join(", "))}</span>` : ""}
      ${d.hint ? `<span class="extra">hint: ${esc(d.hint)}</span>` : ""}
    </div>`).join("");
  setTimeout(() => document.querySelectorAll(".diag[data-i]").forEach((el) => {
    el.onclick = () => editor && editor.goto(diags[+el.dataset.i].line);
  }));
  return html;
}

async function loadSource(name) { return (await api(`/api/source?name=${encodeURIComponent(name)}`)).source; }

function fillSelect(sel, names, preferred) {
  sel.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  if (preferred && names.includes(preferred)) sel.value = preferred;
}

// ---- line diff (LCS) -------------------------------------------------------------

function lineDiff(a, b) {
  const x = a.trimEnd().split("\n"), y = b.trimEnd().split("\n");
  const n = x.length, m = y.length, L = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push(["same", x[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(["del", x[i++]]);
    else out.push(["add", y[j++]]);
  }
  while (i < n) out.push(["del", x[i++]]);
  while (j < m) out.push(["add", y[j++]]);
  return `<div class="diff">${out.map(([k, t]) => `<div class="${k}">${esc(t)}</div>`).join("")}</div>`;
}

// ---- compile tab -------------------------------------------------------------------

const compileEd = makeEditor($("#compile-editor"));
let compileData = null, compilePhase = null;

function renderPhase(name) {
  compilePhase = name;
  const d = compileData;
  document.querySelectorAll(".step").forEach((s) => s.classList.toggle("sel", s.dataset.phase === name));
  const box = $("#compile-detail");
  const sem = (d.diagnostics || []).filter((x) => x.phase === "semantic");
  const views = {
    Lexer: () => `<div class="section-title">${d.tokens.length} tokens</div>
      <div class="scroll"><table class="grid"><tr><th>Line:Col</th><th>Token</th><th>Lexeme</th></tr>
      ${d.tokens.map((t) => `<tr><td class="mono">${t.line}:${t.col}</td><td>${esc(t.type)}</td><td class="mono">${esc(t.lexeme)}</td></tr>`).join("")}
      </table></div>`,
    Parser: () => d.ast ? `<div class="section-title">Abstract syntax tree</div><pre class="code">${esc(d.ast)}</pre>`
      : `<p class="placeholder">No AST: fix the errors below first.</p>`,
    Semantic: () => sem.length ? `<div class="section-title">Semantic analysis</div>${diagList(sem, compileEd)}`
      : `<p class="placeholder">No semantic issues: names declared, actions supported, no duplicates or conflicts.</p>`,
    IR: () => d.ir.length ? `<div class="section-title">${d.ir.length} IR rules (ACCESS expanded, one rule per subject, action, resource)</div>
      <div class="scroll"><table class="grid"><tr><th>Id</th><th>Effect</th><th>Subject</th><th>Action</th><th>Resource</th><th>Condition</th><th>Line</th></tr>
      ${d.ir.map((r) => `<tr><td class="mono">${r.id}</td><td>${badge(r.effect, r.effect === "DENY" ? "err" : "ok")}</td>
        <td>${esc(r.subject)}</td><td>${esc(r.action)}</td><td>${esc(r.resource)}</td><td class="mono">${esc(r.condition)}</td><td>${r.line}</td></tr>`).join("")}
      </table></div>` : `<p class="placeholder">No IR yet.</p>`,
    Optimizer: () => !d.optimization ? `<p class="placeholder">Turn on "Optimizer + verifier" and compile again.</p>`
      : `<div class="flow"><div class="stat"><div class="v">${d.optimization.rules_before}</div><div class="l">IR rules before</div></div>
         <div class="stat hl"><div class="v">${d.optimization.rules_after}</div><div class="l">IR rules after</div></div></div>
         ${d.optimization.removed.length ? `<table class="grid"><tr><th>Removed</th><th>Line</th><th>Why</th></tr>
         ${d.optimization.removed.map((r) => `<tr><td class="mono">${r.id}</td><td>${r.line}</td><td>${esc(r.reason)}</td></tr>`).join("")}</table>`
         : `<p class="placeholder">Nothing to remove: every rule can change some decision.</p>`}`,
    Verifier: () => !d.optimization ? `<p class="placeholder">Turn on "Optimizer + verifier" and compile again.</p>`
      : `<div class="verdict ${d.optimization.proven ? "ok" : "err"}">${d.optimization.proven ? "✔ PROVEN" : "✖ REJECTED"}
         <span class="small">${esc(d.optimization.summary)}</span></div>
         <p class="hint">The optimizer is not trusted: its output is kept only if the verifier proves every
         decision is unchanged (translation validation).</p>`,
    Codegen: () => d.target ? `<div class="section-title">Target policy (JSON)</div><pre class="code">${esc(JSON.stringify(d.target, null, 2))}</pre>`
      : `<p class="placeholder">No output: compilation stopped at an error.</p>`,
  };
  // A phase's own errors come first, so the audience sees them without scrolling.
  const own = { Lexer: "lexer", Parser: "parser" }[name];
  const ownDiags = own ? (d.diagnostics || []).filter((x) => x.phase === own) : [];
  box.innerHTML = (ownDiags.length ? `<div class="section-title">${name} errors</div>${diagList(ownDiags, compileEd)}` : "")
    + views[name]();
}

async function runCompile() {
  const data = await busy($("#compile-run"), "Compiling", () =>
    api("/api/compile", { source: compileEd.value, optimize: $("#compile-optimize").checked }));
  if (!data) return;
  compileData = data;
  compileEd.mark(data.diagnostics);
  $("#compile-steps").innerHTML = data.phases.map((p, i) =>
    `${i ? '<span class="arrow">→</span>' : ""}<button type="button" class="step ${p.status}" data-phase="${p.name}">
      <span class="dot"></span>${p.name}</button>`).join("");
  document.querySelectorAll(".step").forEach((s) => (s.onclick = () => renderPhase(s.dataset.phase)));
  const errors = data.diagnostics.filter((x) => x.severity === "error").length;
  const warnings = data.diagnostics.length - errors;
  $("#compile-summary").innerHTML = data.ok
    ? `${badge("compiles", "ok")} ${data.ir.length} IR rules · ${warnings} warning(s)`
    : `${badge(`${errors} error(s)`, "err")} ${warnings ? `${warnings} warning(s)` : ""} · compilation stopped before IR`;
  const firstBad = data.phases.find((p) => p.status === "error");
  renderPhase(firstBad ? firstBad.name : (data.optimization ? "Verifier" : "Codegen"));
  $("#compile-diags").innerHTML = data.diagnostics.length
    ? `<div class="section-title">All diagnostics (click to jump to the line)</div>${diagList(data.diagnostics, compileEd)}` : "";
}

// ---- generate tab ---------------------------------------------------------------------

function parseResources(text) {
  const out = {};
  for (const part of text.split(";")) {
    const [name, acts] = part.split("=");
    if (name && name.trim() && acts) out[name.trim()] = acts.split(",").map((a) => a.trim().toUpperCase()).filter(Boolean);
  }
  return out;
}
const splitList = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

function showTask(id) {
  const t = state.status.tasks.find((x) => x.id === id);
  const fields = ["#gen-english", "#gen-subjects", "#gen-resources", "#gen-attributes"];
  fields.forEach((f) => ($(f).readOnly = !!t));
  if (!t) { fields.forEach((f) => ($(f).value = "")); return; }
  $("#gen-english").value = t.english;
  $("#gen-subjects").value = t.vocabulary.subjects.join(", ");
  $("#gen-resources").value = Object.entries(t.vocabulary.resources).map(([r, a]) => `${r}=${a.join(",")}`).join("; ");
  $("#gen-attributes").value = t.vocabulary.attributes.join(", ");
}

async function runGenerate() {
  const task = $("#gen-task").value;
  const body = { llm: state.llm, attempts: +$("#gen-attempts").value || 3 };
  if (task) body.task = task;
  else {
    body.english = $("#gen-english").value;
    const subjects = splitList($("#gen-subjects").value), resources = parseResources($("#gen-resources").value);
    if (subjects.length || Object.keys(resources).length)
      body.vocabulary = { subjects, resources, attributes: splitList($("#gen-attributes").value) };
  }
  const label = state.llm === "fake" ? "Generating (fake model)" : `Asking ${state.status.model}`;
  const d = await busy($("#gen-run"), label, () => api("/api/generate", body));
  if (!d) return;
  const verified = d.verified === null ? "" : d.verified
    ? badge("✔ equivalent to reference", "ok") : badge("✖ not equivalent to reference", "err");
  const items = d.attempts.map((a, i) => {
    const cls = a.ok ? "good" : "bad";
    const fb = a.errors.length ? `<div class="feedback"><b>Fed back to the model:</b>
        ${a.errors.map((e) => `${badge(e.code, "err")} line ${e.line}: ${esc(e.message)}`).join("<br>")}</div>` : "";
    return `<div class="item ${cls}"><div class="item-head">Attempt ${i + 1}
        ${a.ok ? badge("compiles", "ok") : badge("does not compile", "err")}
        <span class="hint">${a.latency_s.toFixed(2)} s</span></div>
        <pre class="code">${esc(a.dsl || "(empty answer)")}</pre>${fb}</div>`;
  }).join("");
  $("#gen-result").className = "";
  $("#gen-result").innerHTML = `
    ${d.fake ? '<div class="fake-banner">FAKE MODEL: scripted answers, not real results</div>' : ""}
    <div class="badges">${d.ok ? badge("compiles", "ok") : badge("no compiling policy", "err")} ${verified}
      ${badge(`${d.attempts.length} attempt(s)`, "info")} ${badge(`${d.latency_s.toFixed(2)} s`, "info")}
      ${d.tokens != null ? badge(`${d.tokens} tokens`, "info") : ""} <span class="hint">${esc(d.model)}</span></div>
    <div class="section-title">Attempts</div><div class="timeline">${items}</div>
    ${d.ok ? `<div class="toolbar"><button type="button" class="ghost" id="gen-to-compile">Open in Compile</button>
      ${d.reference ? '<button type="button" class="ghost" id="gen-to-verify">Compare with reference in Verify</button>' : ""}</div>` : ""}
    ${d.reference ? `<details><summary>Reference policy (hidden from the model)</summary><pre class="code">${esc(d.reference)}</pre></details>` : ""}`;
  if (d.ok) {
    $("#gen-to-compile").onclick = () => { compileEd.value = d.dsl + "\n"; switchTab("compile"); runCompile(); };
    if (d.reference) $("#gen-to-verify").onclick = () => {
      verOrig.value = d.reference; verCand.value = d.dsl + "\n"; $("#ver-mode").value = "equivalent";
      switchTab("verify"); runVerify();
    };
  }
}

// ---- optimize tab ------------------------------------------------------------------------

const optEd = makeEditor($("#opt-editor"));

async function runOptimize() {
  const label = state.llm === "fake" ? "Optimizing (fake model)" : `Asking ${state.status.model}`;
  let d;
  try {
    d = await busy($("#opt-run"), label, async () => {
      try {
        return await api("/api/optimize", { source: optEd.value, llm: state.llm,
          samples: +$("#opt-samples").value || 1, attempts: +$("#opt-attempts").value || 3, mode: $("#opt-mode").value });
      } catch (e) { if (e.data && e.data.diagnostics) optEd.mark(e.data.diagnostics); throw e; }
    });
  } catch (e) { return; }
  if (!d) return;
  optEd.mark([]);
  const chosen = d.proposals.find((p) => p.chosen);
  const cards = d.proposals.map((p, i) => {
    const kind = stageKind(p.stage);
    const cex = p.counterexamples.length ? `<div class="cex"><b>Counterexample:</b> ${p.counterexamples.map((c) =>
      `${esc(c.subject)} ${esc(c.action)} ${esc(c.resource)} ${Object.keys(c.attributes).length ? `(${esc(Object.entries(c.attributes).map(([k, v]) => `${k}=${v}`).join(", "))})` : ""}:
       original ${badge(c.original, c.original === "ALLOW" ? "ok" : "err")} → proposal ${badge(c.candidate, c.candidate === "ALLOW" ? "ok" : "err")}`).join("<br>")}</div>` : "";
    const errs = p.errors.length ? `<div class="feedback">${p.errors.map((e) => `${badge(e.code, "err")} line ${e.line}: ${esc(e.message)}`).join("<br>")}</div>` : "";
    return `<div class="item ${kind === "ok" ? "good" : kind === "warn" ? "meh" : "bad"}">
      <div class="item-head">Attempt ${p.attempt} · proposal ${i + 1} ${badge(STAGE_LABEL[p.stage] || p.stage, kind)}
        ${p.rules != null ? `<span class="hint">${p.rules} IR rules</span>` : ""}
        ${p.stricter ? badge("stricter than original", "warn") : ""} ${p.drops_deny_guard ? badge("drops a DENY", "warn") : ""}
        ${p.chosen ? badge("chosen", "info") : ""}</div>
      ${p.note ? `<div class="feedback">${esc(p.note)}</div>` : ""}${errs}
      ${p.verifier ? `<div class="feedback mono">${esc(p.verifier)}</div>` : ""}${cex}
      <details><summary>Proposed policy</summary><pre class="code">${esc(p.dsl || "(empty)")}</pre></details></div>`;
  }).join("");
  $("#opt-result").className = "";
  $("#opt-result").innerHTML = `
    ${d.fake ? '<div class="fake-banner">FAKE MODEL: replays the compiler-only result, not real results</div>' : ""}
    <div class="flow">
      <div class="stat"><div class="v">${d.rules_before}</div><div class="l">original IR rules</div></div>
      <div class="stat"><div class="v">${d.baseline.rules_after}</div><div class="l">compiler-only optimizer</div></div>
      <div class="stat hl"><div class="v">${d.rules_after}</div><div class="l">LLM, verified (${d.statements_before} → ${d.statements_after} statements)</div></div>
    </div>
    <div class="badges">${d.accepted ? badge("rewrite accepted", "ok") : badge("nothing accepted: original kept", "warn")}
      ${badge(`mode: ${d.mode}`, "info")} ${badge(`${d.latency_s.toFixed(2)} s`, "info")}
      ${d.tokens != null ? badge(`${d.tokens} tokens`, "info") : ""} <span class="hint">${esc(d.model)}</span></div>
    ${chosen && chosen.stricter ? '<div class="verdict err">⚠ Stricter than the original <span class="small">it denies some requests the original allowed</span></div>' : ""}
    <div class="section-title">Proposals</div><div class="timeline">${cards}</div>
    ${d.accepted ? `<div class="section-title">Accepted rewrite vs original</div>${lineDiff(d.original_source, d.final_source)}
      <div class="toolbar" style="margin-top:10px"><button type="button" class="ghost" id="opt-to-verify">Re-check in Verify</button></div>` : ""}
    ${d.baseline.removed.length ? `<details><summary>What the compiler-only optimizer removed</summary><table class="grid">
      <tr><th>Rule</th><th>Line</th><th>Why</th></tr>${d.baseline.removed.map((r) =>
      `<tr><td class="mono">${r.id}</td><td>${r.line}</td><td>${esc(r.reason)}</td></tr>`).join("")}</table></details>` : ""}`;
  if (d.accepted) $("#opt-to-verify").onclick = () => {
    verOrig.value = d.original_source; verCand.value = d.final_source; $("#ver-mode").value = d.mode;
    switchTab("verify"); runVerify();
  };
}

// ---- verify tab ---------------------------------------------------------------------------

const verOrig = makeEditor($("#ver-original")), verCand = makeEditor($("#ver-candidate"));
const PRESETS = [
  ["Safe cleanup: optimizable vs candidate_good", "examples/optimizable.pol", "examples/candidate_good.pol"],
  ["Dropped a condition: optimizable vs candidate_bad", "examples/optimizable.pol", "examples/candidate_bad.pol"],
  ["Deleted a DENY guard: optimizable vs candidate_drops_deny", "examples/optimizable.pol", "examples/candidate_drops_deny.pol"],
  ["Stricter rewrite: optimizable vs candidate_stricter", "examples/optimizable.pol", "examples/candidate_stricter.pol"],
];

async function loadPreset(i) {
  const [, a, b] = PRESETS[i];
  verOrig.value = await loadSource(a); verCand.value = await loadSource(b);
  $("#ver-verdict").innerHTML = "";
}

async function runVerify() {
  const d = await busy($("#ver-run"), "Proving", () => api("/api/verify",
    { original: verOrig.value, candidate: verCand.value, mode: $("#ver-mode").value }));
  if (!d) return;
  verOrig.mark(d.original_errors); verCand.mark(d.candidate_errors);
  if (d.original_errors.length || d.candidate_errors.length) {
    $("#ver-verdict").innerHTML = `<div class="verdict err">✖ Does not compile <span class="small">fix the marked lines first</span></div>
      ${diagList(d.original_errors.concat(d.candidate_errors))}`;
    return;
  }
  const rows = d.counterexamples.map((c) => `<tr><td>${esc(c.subject)}</td><td>${esc(c.action)}</td><td>${esc(c.resource)}</td>
      <td class="mono">${esc(Object.entries(c.attributes).map(([k, v]) => `${k}=${v}`).join(", ") || "-")}</td>
      <td>${badge(c.original, c.original === "ALLOW" ? "ok" : "err")}</td><td>${badge(c.candidate, c.candidate === "ALLOW" ? "ok" : "err")}</td>
      <td>${c.direction === "weaker" ? badge("weaker: allows more", "err") : badge("stricter", "warn")}</td></tr>`).join("");
  $("#ver-verdict").innerHTML = `
    <div class="verdict ${d.proven ? "ok" : "err"}">${d.proven ? "✔ PROVEN" : "✖ REJECTED"}
      <span class="small">${esc(d.summary)}</span></div>
    ${rows ? `<div class="section-title">Requests where the candidate decides differently</div>
      <div class="scroll" style="margin-bottom:14px"><table class="grid"><tr><th>Subject</th><th>Action</th><th>Resource</th>
      <th>Attributes</th><th>Original</th><th>Candidate</th><th>Effect</th></tr>${rows}</table></div>` : ""}`;
}

// ---- results tab ----------------------------------------------------------------------------

async function loadResults() {
  const box = $("#results-body");
  let d;
  try { d = await api("/api/results"); } catch (e) { box.textContent = e.message; return; }
  if (!d.available) {
    box.className = "placeholder";
    box.innerHTML = `No evaluation results yet. Run <code>python -m policyc evaluate</code> (or add <code>--llm fake</code>), then reopen this tab.`;
    return;
  }
  box.className = "";
  const s = d.summary, g = s.generation || {}, o = (s.optimization || {}).totals || {}, sem = s.semantic || {};
  const all = (g.by_difficulty || {}).all || {};
  const diffRows = ["easy", "medium", "hard", "all"].map((k) => {
    const b = (g.by_difficulty || {})[k] || {};
    return `<tr><td>${k}</td><td>${b.tasks ?? ""}</td><td>${pct(b.first_try_compile)}</td><td>${pct(b.final_compile)}</td>
      <td>${pct(b.first_try_verified)}</td><td>${pct(b.final_verified)}</td><td>${b.mean_cycles ?? "n/a"}</td></tr>`;
  }).join("");
  const optRows = ((s.optimization || {}).policies || []).map((r) => `<tr><td>${esc(r.task)}</td>
      <td class="num">${r.baseline_before} → ${r.baseline_after}</td><td class="num">${r.llm_status === "ok" ? `${r.baseline_before} → ${r.llm_after}` : esc(r.llm_status)}</td>
      <td class="num">${r.llm_status === "ok" ? `${r.statements_before} → ${r.statements_after}` : ""}</td>
      <td>${r.accepted == null ? "" : r.accepted ? badge("yes", "ok") : badge("no", "warn")}</td></tr>`).join("");
  box.innerHTML = `
    ${d.fake ? '<div class="fake-banner">FAKE - not real results</div>' : ""}
    <div class="badges">${badge(d.run.llm || "model n/a", "info")} <span class="hint">${esc(d.run.date || "")} · prompts ${esc(d.run.prompt_version || "")}</span></div>
    <div class="flow">
      <div class="stat hl"><div class="v">${pct(all.final_verified)}</div><div class="l">generated policies proven correct</div></div>
      <div class="stat"><div class="v">${pct(all.first_try_compile)}</div><div class="l">compile on the first try</div></div>
      <div class="stat hl"><div class="v">${pct(o.llm_reduction_pct)}</div><div class="l">IR rules removed by LLM (compiler-only: ${pct(o.baseline_reduction_pct)})</div></div>
      <div class="stat"><div class="v">${pct(o.accepted_pct)}</div><div class="l">LLM proposals accepted (${o.accepted ?? 0}/${o.proposals ?? 0})</div></div>
      <div class="stat"><div class="v">${pct(sem.detection_rate)}</div><div class="l">seeded issues detected (${sem.detected ?? 0}/${sem.expected ?? 0})</div></div>
    </div>
    <div class="res-grid">
      <div class="card"><div class="section-title">Objective 4: English to DSL</div>
        <table class="grid"><tr><th></th><th>Tasks</th><th>Compile 1st</th><th>Compile final</th><th>Correct 1st</th><th>Correct final</th><th>Cycles</th></tr>${diffRows}</table></div>
      <div class="card"><div class="section-title">Objective 5: optimization</div>
        <table class="grid"><tr><th>Policy</th><th>Compiler-only</th><th>LLM</th><th>Statements</th><th>Accepted</th></tr>${optRows}</table></div>
    </div>
    <div class="charts">${d.charts.map((c) => `<img src="/results/${encodeURIComponent(c)}" alt="${esc(c.replace(".png", "").replace("_", " "))} chart">`).join("")}</div>`;
}

// ---- tabs, model toggle, theme, boot -----------------------------------------------------------

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "results") loadResults();
  try { localStorage.setItem("policyc-tab", name); } catch (e) { /* storage may be blocked */ }
}

function setLlm(mode) {
  state.llm = mode;
  $("#llm-gemini").classList.toggle("on", mode === "gemini");
  $("#llm-fake").classList.toggle("on", mode === "fake");
  $("#model-chip").textContent = mode === "gemini" ? state.status.model : "scripted answers (offline)";
}

function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("policyc-theme", t); } catch (e) { /* ignore */ }
}

async function boot() {
  let theme = "light";
  try { theme = localStorage.getItem("policyc-theme") || "light"; } catch (e) { /* ignore */ }
  applyTheme(theme);
  $("#theme-toggle").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));

  state.status = await api("/api/status");
  const gem = $("#llm-gemini");
  gem.disabled = !state.status.key_set;
  if (!state.status.key_set) gem.title = "Set GEMINI_API_KEY and restart to use Gemini";
  gem.onclick = () => setLlm("gemini");
  $("#llm-fake").onclick = () => setLlm("fake");
  setLlm(state.status.key_set ? state.status.llm : "fake");

  const sources = state.status.sources;
  // Demo examples first, then the benchmark files.
  const examples = sources.filter((s) => s.startsWith("examples/"))
    .concat(sources.filter((s) => s.startsWith("evaluation/") && !s.startsWith("evaluation/generation/")));
  fillSelect($("#compile-source"), examples, "examples/valid.pol");
  $("#compile-source").onchange = async () => { compileEd.value = await loadSource($("#compile-source").value); runCompile(); };
  compileEd.value = await loadSource($("#compile-source").value);
  $("#compile-run").onclick = runCompile;
  $("#compile-optimize").onchange = runCompile;
  compileEd.textarea.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runCompile(); });
  runCompile();

  $("#gen-task").innerHTML = `<option value="">Custom requirement…</option>` + state.status.tasks.map((t) =>
    `<option value="${t.id}">${t.id} (${t.difficulty}): ${esc(t.english.slice(0, 60))}${t.english.length > 60 ? "…" : ""}</option>`).join("");
  $("#gen-task").value = "g16"; showTask("g16");
  $("#gen-task").onchange = () => showTask($("#gen-task").value);
  $("#gen-run").onclick = runGenerate;

  const optChoices = sources.filter((s) => s.startsWith("evaluation/optimization/") || s === "examples/optimizable.pol");
  fillSelect($("#opt-source"), optChoices, "evaluation/optimization/o4_or_merge.pol");
  $("#opt-source").onchange = async () => { optEd.value = await loadSource($("#opt-source").value); $("#opt-result").innerHTML = ""; };
  optEd.value = await loadSource($("#opt-source").value);
  $("#opt-run").onclick = runOptimize;

  $("#ver-preset").innerHTML = PRESETS.map((p, i) => `<option value="${i}">${esc(p[0])}</option>`).join("");
  $("#ver-preset").onchange = () => loadPreset(+$("#ver-preset").value);
  $("#ver-preset").value = "1";
  await loadPreset(1);
  $("#ver-run").onclick = runVerify;

  let tab = "compile";
  try { tab = localStorage.getItem("policyc-tab") || "compile"; } catch (e) { /* ignore */ }
  switchTab(tab);
}

boot().catch((e) => toast(`Could not start: ${e.message}`));
