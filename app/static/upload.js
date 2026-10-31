/* Upload & review page. Vanilla JS, no framework, no model calls. */
(() => {
  "use strict";

  const CFG = window.PR_GUARDIAN_UPLOAD || { enabled: false, limits: {} };
  const $ = (id) => document.getElementById(id);
  const state = { files: [], review: null, busy: false };

  const NO_ISSUES =
    "No issues detected by the enabled checks. This does not prove the code is bug-free.";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function human(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KiB";
    return (bytes / 1024 / 1024).toFixed(2) + " MiB";
  }

  function setStatus(text) { $("upload-status").textContent = text; }

  function setError(text) {
    const node = $("upload-error");
    node.hidden = !text;
    node.textContent = text || "";
  }

  function renderList() {
    const list = $("upload-list");
    list.innerHTML = "";
    state.files.forEach((file, index) => {
      const li = document.createElement("li");
      li.className = "upload-item";
      li.innerHTML =
        '<span class="upload-name">' + esc(file.name) + "</span>" +
        '<span class="muted">' + human(file.size) + "</span>" +
        '<button class="btn small" type="button" data-remove="' + index + '">Remove</button>';
      list.appendChild(li);
    });
    const total = state.files.reduce((sum, file) => sum + file.size, 0);
    $("upload-count").textContent = state.files.length
      ? state.files.length + " file(s), " + human(total)
      : "No files selected";
    $("upload-run").disabled = state.files.length === 0 || state.busy;
    $("upload-clear").disabled = state.files.length === 0 || state.busy;
  }

  function addFiles(fileList) {
    setError("");
    for (const file of Array.from(fileList || [])) {
      const name = file.name || "unnamed.py";
      const lower = name.toLowerCase();
      if (!(lower.endsWith(".py") || lower.endsWith(".zip"))) {
        setError("Only .py files or a single .zip are accepted. Ignored: " + name);
        continue;
      }
      if (state.files.some((existing) => existing.name === name)) {
        setError("The same file name was selected twice: " + name);
        continue;
      }
      state.files.push(file);
    }
    renderList();
  }

  function clearAll() {
    state.files = [];
    state.review = null;
    setError("");
    setStatus("");
    $("upload-results-panel").hidden = true;
    $("upload-status-panel").hidden = true;
    $("upload-input").value = "";
    renderList();
  }

  function severityClass(severity) {
    if (severity === "high") return "sev-high";
    if (severity === "medium") return "sev-medium";
    return "sev-low";
  }

  function renderReport(report) {
    state.review = report;
    $("upload-status-panel").hidden = false;
    $("upload-results-panel").hidden = false;

    const findings = report.final_findings || [];
    const bySeverity = { high: 0, medium: 0, low: 0 };
    findings.forEach((finding) => { bySeverity[finding.severity] = (bySeverity[finding.severity] || 0) + 1; });
    const files = report.changed_files || [];

    const analysis = report.upload_analysis || {};
    const degraded =
      (analysis.ruff_status && analysis.ruff_status !== "ok" &&
       analysis.ruff_status !== "empty") ||
      analysis.ast_status === "partial" || analysis.ast_status === "failed";
    const stageText = [
      "AST " + (analysis.ast_status || "not_run"),
      "Ruff " + (analysis.ruff_status || "not_run"),
      "worker " + (analysis.worker_status || "not_run"),
      "AI " + (analysis.ai_status || "not_run"),
    ].join(" · ");
    setStatus(
      (degraded ? "Analysis incomplete: " : "Analysis complete: ") +
      files.length + " file(s), " + findings.length +
      " finding(s). Tests were not run: uploaded code is never executed. (" +
      stageText + ")"
    );
    const notice = document.getElementById("upload-partial-notice");
    if (notice) {
      notice.hidden = !degraded;
      notice.textContent = degraded
        ? "Some checks did not complete. The findings below are real, but coverage " +
          "is incomplete; this is not a clean result."
        : "";
    }
    $("upload-summary").textContent =
      "high " + (bySeverity.high || 0) + " · medium " + (bySeverity.medium || 0) +
      " · low " + (bySeverity.low || 0) +
      " · Ruff findings are lint evidence; UPxxx patterns are suspected heuristics.";

    const container = $("upload-files");
    container.innerHTML = "";

    if (!findings.length) {
      const empty = document.createElement("p");
      empty.className = "upload-empty";
      empty.textContent = NO_ISSUES;
      container.appendChild(empty);
    }

    files.forEach((file) => {
      const own = findings.filter((finding) => finding.file_path === file.file_path);
      const section = document.createElement("details");
      section.className = "upload-file";
      section.open = own.length > 0;
      const head = document.createElement("summary");
      head.innerHTML = "<strong>" + esc(file.file_path) + "</strong> · " +
        file.added_lines.length + " line(s) · " + own.length + " finding(s)";
      section.appendChild(head);

      if (!own.length) {
        const none = document.createElement("p");
        none.className = "muted";
        none.textContent = "No findings for this file from the enabled checks.";
        section.appendChild(none);
      }
      own.forEach((finding) => {
        const card = document.createElement("div");
        card.className = "finding";
        card.innerHTML =
          '<div class="finding-head">' +
          '<span class="badge ' + severityClass(finding.severity) + '">' + esc(finding.severity) + "</span>" +
          '<span class="badge">' + esc(finding.rule || finding.category) + "</span>" +
          '<span class="badge ' + (finding.source === "ruff" ? "src-ruff" : "src-llm") + '">' +
          esc(finding.source) + "</span>" +
          '<span class="muted">' + esc(finding.file_path) + ":" + finding.start_line +
          (finding.end_line !== finding.start_line ? "-" + finding.end_line : "") + "</span>" +
          '<span class="badge">' + esc(finding.verification_status) + "</span>" +
          "</div>" +
          '<p class="finding-summary">' + esc(finding.summary) + "</p>" +
          (finding.explanation ? '<p class="muted">' + esc(finding.explanation) + "</p>" : "") +
          (finding.suggested_fix ? '<p class="finding-fix"><strong>Fix:</strong> ' +
            esc(finding.suggested_fix) + "</p>" : "");
        section.appendChild(card);
      });
      container.appendChild(section);
    });

    const notes = report.notes || [];
    if (notes.length) {
      const noteBox = document.createElement("div");
      noteBox.className = "upload-notes";
      noteBox.innerHTML = "<h3>Limits of this analysis</h3><ul>" +
        notes.map((note) => "<li>" + esc(note) + "</li>").join("") + "</ul>";
      container.appendChild(noteBox);
    }
  }

  async function analyse() {
    if (!state.files.length || state.busy) return;
    state.busy = true;
    $("upload-run").disabled = true;
    $("upload-status-panel").hidden = false;
    setError("");
    const started = Date.now();
    const timer = setInterval(() => {
      setStatus("Analysing… " + ((Date.now() - started) / 1000).toFixed(1) + "s");
    }, 200);

    try {
      const form = new FormData();
      const zip = state.files.find((file) => file.name.toLowerCase().endsWith(".zip"));
      if (zip) {
        form.append("archive", zip, zip.name);
      } else {
        state.files.forEach((file) => form.append("files", file, file.name));
      }
      const response = await fetch("/api/uploads", { method: "POST", body: form });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.message || payload.detail || ("HTTP " + response.status));
      }
      renderReport(payload.report);
    } catch (error) {
      setStatus("");
      setError("Analysis failed: " + error.message);
    } finally {
      clearInterval(timer);
      state.busy = false;
      renderList();
    }
  }

  function download(format) {
    if (!state.review) return;
    const url = "/api/reviews/" + encodeURIComponent(state.review.review_id) +
      "/export?format=" + format;
    window.location.href = url;
  }

  function init() {
    const enabled = Boolean(CFG.enabled);
    $("upload-form").hidden = !enabled;
    $("upload-disabled").hidden = enabled;
    if (!enabled) {
      $("upload-status-panel").hidden = true;
      return;
    }
    const limits = CFG.limits || {};
    $("upload-limit-hint").textContent =
      "Limits: " + (limits.max_files || "?") + " files, " +
      Math.round((limits.max_file_bytes || 0) / 1024) + " KiB per file, " +
      Math.round((limits.max_total_bytes || 0) / 1024 / 1024) + " MiB total.";
    $("upload-pick").addEventListener("click", () => $("upload-input").click());
    $("upload-input").addEventListener("change", (event) => addFiles(event.target.files));
    $("upload-run").addEventListener("click", analyse);
    $("upload-clear").addEventListener("click", clearAll);
    $("upload-list").addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove]");
      if (!button) return;
      state.files.splice(Number(button.dataset.remove), 1);
      renderList();
    });
    $("upload-export-md").addEventListener("click", () => download("markdown"));
    $("upload-export-json").addEventListener("click", () => download("json"));
    renderList();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
