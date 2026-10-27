/* PR Guardian demo UI.
 * All review data comes from the FastAPI backend, which runs the real pipeline.
 * Nothing on this page is precomputed or hard-coded.
 */
(function () {
  "use strict";

  var CONFIG = window.PR_GUARDIAN_CONFIG || {};
  var state = {
    caseId: null,
    reviewId: null,
    report: null,
    approvals: {},
    running: false
  };

  var $ = function (id) { return document.getElementById(id); };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function show(el, visible) {
    if (!el) { return; }
    el.classList.toggle("hidden", !visible);
  }

  function setError(message) {
    var banner = $("error-banner");
    if (!message) { show(banner, false); banner.textContent = ""; return; }
    banner.textContent = message;
    show(banner, true);
  }

  function setStatus(text, busy) {
    var el = $("run-status");
    el.textContent = text || "";
    el.classList.toggle("busy", !!busy);
  }

  async function api(url, options) {
    var res = await fetch(url, options);
    var text = await res.text();
    var data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!res.ok) {
      var detail = data && (data.detail || data.message);
      throw new Error(detail || ("HTTP " + res.status));
    }
    return data;
  }

  /* ---- case selection -------------------------------------------------- */

  function selectCase(caseId) {
    state.caseId = caseId;
    state.reviewId = null;
    state.report = null;
    state.approvals = {};
    Array.prototype.forEach.call(document.querySelectorAll(".case-card"), function (card) {
      card.classList.toggle("selected", card.getAttribute("data-case-id") === caseId);
    });
    $("run-btn").disabled = false;
    show($("results-panel"), false);
    setError("");
    loadDiff(caseId);
  }

  async function loadDiff(caseId) {
    var body = $("diff-body");
    body.innerHTML = '<p class="muted">Loading diff…</p>';
    try {
      var data = await api("/api/cases/" + encodeURIComponent(caseId) + "/diff");
      renderDiff(data.files);
      $("diff-hint").innerHTML = "Git diff from <code>main</code> to this branch. " +
        "Line numbers are the new-file line numbers.";
    } catch (err) {
      body.innerHTML = "";
      setError("Could not load diff: " + err.message);
    }
  }

  function renderDiff(files) {
    var body = $("diff-body");
    body.innerHTML = "";
    if (!files || !files.length) {
      body.innerHTML = '<p class="muted">This change touches no files.</p>';
      return;
    }
    files.forEach(function (file) {
      var wrap = document.createElement("div");
      wrap.className = "diff-file";
      var html = '<div class="diff-file-head"><span>' + esc(file.file_path) +
        '</span><span class="muted">' + esc(file.change_type) + '</span></div>';
      file.hunks.forEach(function (hunk) {
        html += '<div class="diff-hunk"><div class="hunk-header">' + esc(hunk.header) + '</div><div class="diff-lines">';
        hunk.lines.forEach(function (line) {
          html += '<div class="diff-line ' + esc(line.kind) + '">' +
            '<span class="ln">' + (line.old_no == null ? "" : line.old_no) + '</span>' +
            '<span class="ln">' + (line.new_no == null ? "" : line.new_no) + '</span>' +
            '<span class="code">' + esc(line.text) + '</span></div>';
        });
        html += "</div></div>";
      });
      wrap.innerHTML = html;
      body.appendChild(wrap);
    });
  }

  /* ---- running a review ------------------------------------------------ */

  async function runReview() {
    if (!state.caseId || state.running) { return; }
    state.running = true;
    var btn = $("run-btn");
    btn.disabled = true;
    setError("");
    show($("llm-banner"), false);
    setStatus("Running Ruff, pytest and the review…", true);

    try {
      var payload = await api("/api/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          case_id: state.caseId,
          run_tests: $("run-tests").checked
        })
      });
      state.reviewId = payload.review_id;
      state.report = payload.report;
      state.approvals = payload.approvals || {};
      renderResults(payload);
      show($("results-panel"), true);
      setStatus("Review complete.", false);
      $("results-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      setStatus("", false);
      setError("Review failed: " + err.message);
    } finally {
      state.running = false;
      btn.disabled = false;
    }
  }

  /* ---- rendering results ----------------------------------------------- */

  function llmStatusText(status) {
    return {
      ok: "Completed",
      enabled: "Completed",
      failed: "Failed",
      not_configured: "Not evaluated",
      disabled_by_public_policy: "Not evaluated (public policy)",
      disabled_by_request: "Not evaluated (disabled)"
    }[status] || status;
  }

  function renderResults(payload) {
    var report = payload.report;
    var summary = report.changed_files.length + " file(s) changed · review " + report.review_id;
    $("result-summary").textContent = summary;

    renderRuff(report);
    renderTests(report);
    renderLLM(report);
    renderFindings(report.final_findings, state.approvals);
    updateApprovalSummary();
    renderLlmBanner(report);
  }

  function renderLlmBanner(report) {
    var banner = $("llm-banner");
    if (report.llm_status === "ok") {
      banner.textContent = "The LLM step ran with model “" + report.model_name +
        "”. Its findings are marked suspected until a human accepts them.";
      show(banner, true);
      return;
    }
    if (report.llm_status === "failed") {
      banner.textContent = "The LLM call failed (" + (report.llm_note || "unknown error") +
        "). Static and test evidence above are still real results.";
      show(banner, true);
      return;
    }
    banner.textContent = "LLM review did not run. " + (report.llm_note ||
      "No API key is configured on this server.") +
      " The Ruff and pytest results shown here are real, not simulated.";
    show(banner, true);
  }

  function renderRuff(report) {
    var items = (report.static_findings || []).filter(function (f) { return f.source === "ruff"; });
    var body = $("ruff-body");
    if (!items.length) {
      body.innerHTML = '<p class="muted">No issues reported by Ruff.</p>' +
        '<p class="muted">Ruff finds lint and style problems; it does not detect behavioural bugs.</p>';
      return;
    }
    var html = '<p class="muted">' + items.length + " finding(s):</p><ul>";
    items.forEach(function (f) {
      html += "<li><code>" + esc(f.file_path) + ":" + f.start_line + "</code> — " + esc(f.summary) + "</li>";
    });
    body.innerHTML = html + "</ul>";
  }

  function renderTests(report) {
    var ts = report.test_summary || {};
    var body = $("tests-body");
    if (!ts.ran) {
      body.innerHTML = '<p class="muted">pytest did not run: ' + esc(ts.skipped_reason || "not requested") + "</p>";
      return;
    }
    var cls = (ts.failed || ts.errors) ? "st-suspected" : "st-confirmed";
    var html = "<p><strong>" + ts.passed + "</strong> passed · <strong>" +
      ts.failed + "</strong> failed · <strong>" + ts.errors + "</strong> errors</p>";
    html += '<p class="muted">' + Number(ts.duration_seconds || 0).toFixed(2) + "s" +
      (ts.timed_out ? " · timed out" : "") + "</p>";
    if (ts.failed_tests && ts.failed_tests.length) {
      html += '<p class="muted">Failing tests:</p><ul>';
      ts.failed_tests.forEach(function (name) {
        html += "<li><code>" + esc(name) + "</code> <span class='badge st-confirmed'>confirmed</span></li>";
      });
      html += "</ul>";
    }
    if (!(ts.failed || ts.errors)) {
      html += '<p class="muted"><span class="badge ' + cls + '">tests passing</span> ' +
        "No test failure evidence.</p>";
    }
    body.innerHTML = html;
  }

  function renderLLM(report) {
    var body = $("llm-body");
    var status = report.llm_status;
    if (status !== "ok" && status !== "enabled") {
      body.innerHTML = '<p><span class="badge st-noteval">not evaluated</span> ' +
        esc(llmStatusText(status)) + "</p><p class=\"muted\">" +
        esc(report.llm_note || "No model call was made for this review.") + "</p>";
      return;
    }
    var items = report.llm_findings || [];
    if (!items.length) {
      body.innerHTML = '<p><span class="badge st-confirmed">completed</span> ' +
        "The model reported no issues.</p>" +
        '<p class="muted">Reporting nothing is a valid outcome for a clean change.</p>';
      return;
    }
    var html = '<p class="muted">' + items.length +
      ' hypothesis(es) from <code>' + esc(report.model_name) + "</code>:</p><ul>";
    items.forEach(function (f) {
      html += "<li><code>" + esc(f.file_path) + ":" + f.start_line + "</code> — " +
        esc(f.summary) + ' <span class="badge st-suspected">suspected</span></li>';
    });
    body.innerHTML = html + "</ul>";
  }

  function renderFindings(findings, approvals) {
    var body = $("findings-body");
    body.innerHTML = "";
    if (!findings || !findings.length) {
      body.innerHTML = '<p class="muted">No reportable findings for this change.</p>';
      return;
    }
    findings.forEach(function (f) {
      var decision = approvals[f.id] || "pending";
      var card = document.createElement("div");
      card.className = "finding " + esc(f.verification_status);
      card.setAttribute("data-finding-id", f.id);

      var head = '<div class="finding-head">' +
        '<span class="finding-title">' + esc(f.summary) + "</span>" +
        '<span class="badge src-' + esc(f.source) + '">' + esc(f.source) + "</span>" +
        '<span class="badge st-' + esc(f.verification_status) + '">' + esc(f.verification_status) + "</span>" +
        '<span class="finding-loc">' + esc(f.file_path) + ":" + f.start_line + "</span></div>";

      var dl = "<dl>" +
        "<dt>Severity</dt><dd>" + esc(f.severity) + "</dd>" +
        "<dt>Category</dt><dd>" + esc(f.category) + "</dd>" +
        "<dt>Evidence</dt><dd>" + esc(f.evidence || "—") + "</dd>" +
        "<dt>Explanation</dt><dd>" + esc(f.explanation || "—") + "</dd>" +
        "<dt>Suggested fix</dt><dd>" + esc(f.suggested_fix || "—") + "</dd></dl>";

      var row = '<div class="decision-row">' +
        '<button class="btn small accept" type="button" data-decision="accepted">Accept</button>' +
        '<button class="btn small reject" type="button" data-decision="rejected">Reject</button>' +
        '<span class="state"></span></div>';

      card.innerHTML = head + dl + row;
      body.appendChild(card);
      applyDecisionStyles(card, decision);
    });
  }

  function applyDecisionStyles(card, decision) {
    var acceptBtn = card.querySelector("button[data-decision='accepted']");
    var rejectBtn = card.querySelector("button[data-decision='rejected']");
    var stateEl = card.querySelector(".state");
    if (acceptBtn) { acceptBtn.classList.toggle("active", decision === "accepted"); }
    if (rejectBtn) { rejectBtn.classList.toggle("active", decision === "rejected"); }
    if (stateEl) {
      stateEl.textContent = decision === "pending"
        ? "Awaiting human decision"
        : (decision === "accepted" ? "Accepted by reviewer" : "Rejected by reviewer");
    }
  }

  async function setDecision(findingId, decision) {
    if (!state.reviewId) { return; }
    var already = state.approvals[findingId] || "pending";
    var next = already === decision ? "pending" : decision;
    try {
      await api("/api/reviews/" + encodeURIComponent(state.reviewId) +
        "/findings/" + encodeURIComponent(findingId) + "/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: next })
      });
      state.approvals[findingId] = next;
      var card = document.querySelector('.finding[data-finding-id="' + CSS.escape(findingId) + '"]');
      if (card) { applyDecisionStyles(card, next); }
      updateApprovalSummary();
    } catch (err) {
      setError("Could not save decision: " + err.message);
    }
  }

  function updateApprovalSummary() {
    var values = Object.keys(state.approvals).map(function (k) { return state.approvals[k]; });
    var accepted = values.filter(function (v) { return v === "accepted"; }).length;
    var rejected = values.filter(function (v) { return v === "rejected"; }).length;
    var total = state.report && state.report.final_findings ? state.report.final_findings.length : 0;
    $("approval-summary").textContent = total + " finding(s) · " + accepted +
      " accepted · " + rejected + " rejected · " + (total - accepted - rejected) + " pending";
  }

  /* ---- export ---------------------------------------------------------- */

  async function exportReview(format) {
    if (!state.reviewId) { return; }
    var approvedOnly = $("approved-only").checked;
    var url = "/api/reviews/" + encodeURIComponent(state.reviewId) + "/export?format=" +
      encodeURIComponent(format) + (approvedOnly ? "&approved_only=true" : "");
    try {
      var res = await fetch(url);
      if (!res.ok) {
        var text = await res.text();
        throw new Error("HTTP " + res.status + " " + text.slice(0, 120));
      }
      var blob = await res.blob();
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "review-" + state.reviewId + (format === "json" ? ".json" : ".md");
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 2000);
    } catch (err) {
      setError("Export failed: " + err.message);
    }
  }

  /* ---- wiring ---------------------------------------------------------- */

  document.addEventListener("click", function (event) {
    var card = event.target.closest(".case-card");
    if (card) { selectCase(card.getAttribute("data-case-id")); return; }

    var decisionBtn = event.target.closest("button[data-decision]");
    if (decisionBtn) {
      var finding = decisionBtn.closest(".finding");
      if (finding) {
        setDecision(finding.getAttribute("data-finding-id"), decisionBtn.getAttribute("data-decision"));
      }
    }
  });

  $("run-btn").addEventListener("click", runReview);
  $("export-md").addEventListener("click", function () { exportReview("markdown"); });
  $("export-json").addEventListener("click", function () { exportReview("json"); });

  if (CONFIG.demo_repo_error) {
    setError("Demo repository unavailable: " + CONFIG.demo_repo_error);
  }
  if (CONFIG.llm_status === "not_configured") {
    var banner = $("llm-banner");
    banner.textContent = "No model API key is configured on this server, so the AI step will be " +
      "marked Not Evaluated. Ruff and pytest evidence are real.";
    show(banner, true);
  }
})();
