/**
 * AI Doc Translator — app.js
 * Vanilla JS, no frameworks. Page-specific init functions.
 */

"use strict";

// ============================================================
// User identity — stored in localStorage as a UUID
// ============================================================

const USER_ID_KEY = "adt_user_id";

function initUserId() {
  if (!localStorage.getItem(USER_ID_KEY)) {
    localStorage.setItem(USER_ID_KEY, crypto.randomUUID());
  }
}

function getUserId() {
  return localStorage.getItem(USER_ID_KEY) || "";
}

// ============================================================
// Shared helpers
// ============================================================

function showEl(el) {
  el.classList.remove("hidden");
}

function hideEl(el) {
  el.classList.add("hidden");
}

function setError(el, msg) {
  el.textContent = msg;
  showEl(el);
}

function clearError(el) {
  el.textContent = "";
  hideEl(el);
}

function formatDate(isoStr) {
  if (!isoStr) return "—";
  return new Intl.DateTimeFormat("ru", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(isoStr));
}

function statusLabel(status) {
  const labels = {
    pending: "В очереди",
    running: "Перевод",
    done: "Готово",
    error: "Ошибка",
    cancelled: "Отменено",
  };
  return labels[status] || status;
}

async function apiFetch(path, options = {}) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch (_) { /* ignore */ }
    throw new Error(detail);
  }
  // 204 No Content
  if (resp.status === 204) return null;
  return resp.json();
}

// ============================================================
// Upload page
// ============================================================

function initUploadPage() {
  const dropzone = document.getElementById("dropzone");
  if (!dropzone) return;

  const debugBanner = document.getElementById("debug-banner");
  const fileInput     = document.getElementById("file-input");
  const fileChosen    = document.getElementById("file-chosen");
  const fileNameEl    = document.getElementById("file-name");
  const fileClearBtn  = document.getElementById("file-clear");
  const targetLang    = document.getElementById("target-lang");
  const uploadBtn     = document.getElementById("upload-btn");
  const uploadError   = document.getElementById("upload-error");
  const uploadSection = document.getElementById("upload-section");
  const progressSection = document.getElementById("progress-section");

  let selectedFile = null;
  let pollTimer = null;

  // ---- File selection ----

  function setFile(file) {
    if (!file) return;
    const ext = file.name.split(".").pop().toLowerCase();
    if (!["pdf", "txt", "html"].includes(ext)) {
      setError(uploadError, "Поддерживаются только PDF, TXT и HTML файлы.");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setError(uploadError, "Файл превышает максимально допустимый размер 50 МБ.");
      return;
    }
    clearError(uploadError);
    selectedFile = file;
    fileNameEl.textContent = file.name;
    showEl(fileChosen);
    uploadBtn.disabled = false;
  }

  function clearFile() {
    selectedFile = null;
    fileInput.value = "";
    hideEl(fileChosen);
    uploadBtn.disabled = true;
    clearError(uploadError);
  }

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) setFile(fileInput.files[0]);
  });
  fileClearBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    clearFile();
  });

  // Drag-and-drop
  let dragCounter = 0;
  dropzone.addEventListener("dragover", (e) => e.preventDefault());
  dropzone.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    dropzone.classList.add("drag-over");
  });
  dropzone.addEventListener("dragleave", () => {
    dragCounter--;
    if (dragCounter === 0) dropzone.classList.remove("drag-over");
  });
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dragCounter = 0;
    dropzone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });

  // ---- Upload ----

  uploadBtn.addEventListener("click", async () => {
    if (!selectedFile) return;
    clearError(uploadError);
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Загрузка…";

    const form = new FormData();
    form.append("file", selectedFile);
    form.append("target_lang", targetLang.value);
    form.append("user_id", getUserId());

    try {
      const data = await apiFetch("/api/translations/upload", {
        method: "POST",
        body: form,
      });
      showProgressCard(data.job_id);
    } catch (err) {
      setError(uploadError, err.message);
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Перевести";
    }
  });

  // ---- Progress card ----

  const statusBadge    = document.getElementById("job-status-badge");
  const progressPhase  = document.getElementById("progress-phase");
  const progressBar    = document.getElementById("progress-bar");
  const progressBarWrap= document.getElementById("progress-bar-wrap");
  const progressLabel  = document.getElementById("progress-label");
  const downloadBtn    = document.getElementById("download-btn");
  const cancelBtn      = document.getElementById("cancel-btn");
  const newTranslBtn   = document.getElementById("new-translation-btn");
  const progressError  = document.getElementById("progress-error");

  const TERMINAL_STATUSES = ["done", "error", "cancelled"];

  function phaseLabel(s) {
    if (s === "pending")  return "В очереди…";
    if (s === "running")  return "Идёт перевод…";
    if (s === "done")     return "Перевод завершён";
    if (s === "error")    return "Произошла ошибка";
    if (s === "cancelled")return "Перевод отменён";
    return "";
  }

  function renderStatus(job) {
    const s = job.status;

    statusBadge.textContent = statusLabel(s);
    statusBadge.className = "status-badge status-" + s;
    progressPhase.textContent = phaseLabel(s);

    const total = job.chunk_total || 0;
    const done  = job.chunk_done  || 0;
    const pct   = total > 0 ? Math.round((done / total) * 100) : (s === "done" ? 100 : 0);

    progressBar.style.width = pct + "%";
    progressBarWrap.setAttribute("aria-valuenow", pct);

    if (total > 0) {
      progressLabel.textContent = `Чанки: ${done} / ${total} (${pct}%)`;
    } else if (s === "running") {
      progressLabel.textContent = "Подготовка…";
    } else {
      progressLabel.textContent = "";
    }

    // Actions
    hideEl(downloadBtn);
    hideEl(cancelBtn);
    hideEl(newTranslBtn);

    if (s === "done") {
      downloadBtn.href = `/api/translations/${job.job_id}/download`;
      showEl(downloadBtn);
      showEl(newTranslBtn);
    } else if (s === "pending" || s === "running") {
      showEl(cancelBtn);
    } else {
      showEl(newTranslBtn);
    }

    if (s === "error" && job.error_msg) {
      setError(progressError, job.error_msg);
    } else {
      clearError(progressError);
    }
  }

  function showProgressCard(jobId) {
    hideEl(uploadSection);
    showEl(progressSection);

    cancelBtn.onclick = async () => {
      cancelBtn.disabled = true;
      try {
        await apiFetch(`/api/translations/${jobId}`, { method: "DELETE" });
      } catch (err) {
        setError(progressError, err.message);
        cancelBtn.disabled = false;
      }
    };

    newTranslBtn.onclick = () => {
      if (pollTimer) clearTimeout(pollTimer);
      hideEl(progressSection);
      clearFile();
      uploadBtn.textContent = "Перевести";
      showEl(uploadSection);
    };

    pollStatus(jobId);
  }

  async function pollStatus(jobId) {
    try {
      const job = await apiFetch(`/api/translations/${jobId}/status`);
      renderStatus(job);
      if (!TERMINAL_STATUSES.includes(job.status)) {
        pollTimer = setTimeout(() => pollStatus(jobId), 2000);
      }
    } catch (err) {
      setError(progressError, err.message);
      pollTimer = setTimeout(() => pollStatus(jobId), 4000);
    }
  }
}

// ============================================================
// History page
// ============================================================

function initHistoryPage() {
  const loadingEl   = document.getElementById("history-loading");
  if (!loadingEl) return;

  const emptyEl     = document.getElementById("history-empty");
  const errorEl     = document.getElementById("history-error");
  const tableEl     = document.getElementById("history-table");
  const bodyEl      = document.getElementById("history-body");
  const paginationEl= document.getElementById("history-pagination");
  const prevBtn     = document.getElementById("history-prev");
  const nextBtn     = document.getElementById("history-next");
  const pageInfoEl  = document.getElementById("history-page-info");

  const PAGE_SIZE = 20;
  let currentOffset = 0;
  let totalLoaded = 0;

  async function loadHistory(offset) {
    hideEl(emptyEl);
    hideEl(errorEl);
    hideEl(tableEl);
    clearError(errorEl);

    try {
      const items = await apiFetch(
        `/api/history?user_id=${getUserId()}&limit=${PAGE_SIZE}&offset=${offset}`
      );
      hideEl(loadingEl);
      totalLoaded = items.length;

      if (items.length === 0 && offset === 0) {
        showEl(emptyEl);
        return;
      }

      bodyEl.innerHTML = "";
      items.forEach((item) => {
        const tr = document.createElement("tr");
        const langStr = [item.source_lang, item.target_lang]
          .filter(Boolean)
          .join(" → ") || "—";

        tr.innerHTML = `
          <td title="${item.filename}">${truncate(item.filename, 40)}</td>
          <td>${langStr}</td>
          <td>${formatDate(item.created_at)}</td>
          <td><span class="status-badge status-${item.status || 'done'}">${statusLabel(item.status || 'done')}</span></td>
          <td class="col-actions">
            <div class="cell-actions">
              <a class="btn btn-ghost btn-sm" href="/api/translations/${item.job_id}/download" download>Скачать</a>
              <button class="btn btn-danger btn-sm" data-id="${item.id}" data-job="${item.job_id}" type="button">Удалить</button>
            </div>
          </td>
        `;
        bodyEl.appendChild(tr);
      });

      showEl(tableEl);

      // Pagination
      const hasPrev = offset > 0;
      const hasNext = items.length === PAGE_SIZE;

      if (hasPrev || hasNext) {
        const page = Math.floor(offset / PAGE_SIZE) + 1;
        pageInfoEl.textContent = `Страница ${page}`;
        prevBtn.disabled = !hasPrev;
        nextBtn.disabled = !hasNext;
        showEl(paginationEl);
      } else {
        hideEl(paginationEl);
      }
    } catch (err) {
      hideEl(loadingEl);
      setError(errorEl, err.message);
    }
  }

  bodyEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-id]");
    if (!btn) return;
    if (!confirm("Удалить запись из истории? Файлы будут удалены.")) return;
    btn.disabled = true;
    try {
      await apiFetch(`/api/history/${btn.dataset.id}`, { method: "DELETE" });
      await loadHistory(currentOffset);
    } catch (err) {
      setError(errorEl, err.message);
      btn.disabled = false;
    }
  });

  prevBtn.addEventListener("click", () => {
    currentOffset = Math.max(0, currentOffset - PAGE_SIZE);
    loadHistory(currentOffset);
  });

  nextBtn.addEventListener("click", () => {
    currentOffset += PAGE_SIZE;
    loadHistory(currentOffset);
  });

  loadHistory(0);
}

// ============================================================
// Glossary page
// ============================================================

function initGlossaryPage() {
  const loadingEl = document.getElementById("glossary-loading");
  if (!loadingEl) return;

  const errorEl   = document.getElementById("glossary-error");
  const tableEl   = document.getElementById("glossary-table");
  const bodyEl    = document.getElementById("glossary-body");
  const emptyEl   = document.getElementById("glossary-empty");
  const addForm   = document.getElementById("add-term-form");
  const addError  = document.getElementById("add-term-error");
  const srcInput  = document.getElementById("new-source");
  const tgtInput  = document.getElementById("new-target");

  async function loadGlossary() {
    hideEl(errorEl);
    hideEl(emptyEl);
    hideEl(tableEl);
    clearError(errorEl);

    try {
      const items = await apiFetch(`/api/glossary?user_id=${getUserId()}`);
      hideEl(loadingEl);

      if (items.length === 0) {
        showEl(emptyEl);
        return;
      }

      bodyEl.innerHTML = "";
      items.forEach((item) => {
        bodyEl.appendChild(buildGlossaryRow(item));
      });
      showEl(tableEl);
    } catch (err) {
      hideEl(loadingEl);
      setError(errorEl, err.message);
    }
  }

  function buildGlossaryRow(item) {
    const tr = document.createElement("tr");
    tr.dataset.id = item.id;

    const srcTd = document.createElement("td");
    srcTd.textContent = item.source_term;

    const tgtTd = document.createElement("td");
    tgtTd.textContent = item.target_term;

    const actionsTd = document.createElement("td");
    actionsTd.className = "col-actions";

    const editBtn = document.createElement("button");
    editBtn.className = "btn btn-ghost btn-sm";
    editBtn.type = "button";
    editBtn.textContent = "Изменить";

    const saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-primary btn-sm hidden";
    saveBtn.type = "button";
    saveBtn.textContent = "Сохранить";

    const delBtn = document.createElement("button");
    delBtn.className = "btn btn-danger btn-sm";
    delBtn.type = "button";
    delBtn.textContent = "Удалить";

    const wrap = document.createElement("div");
    wrap.className = "cell-actions";
    wrap.append(editBtn, saveBtn, delBtn);
    actionsTd.appendChild(wrap);

    tr.append(srcTd, tgtTd, actionsTd);

    editBtn.addEventListener("click", () => {
      srcTd.contentEditable = "true";
      tgtTd.contentEditable = "true";
      srcTd.focus();
      hideEl(editBtn);
      showEl(saveBtn);
    });

    saveBtn.addEventListener("click", async () => {
      const newSrc = srcTd.textContent.trim();
      const newTgt = tgtTd.textContent.trim();
      if (!newSrc || !newTgt) return;
      saveBtn.disabled = true;
      try {
        await apiFetch(`/api/glossary/${item.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_term: newSrc, target_term: newTgt }),
        });
        srcTd.contentEditable = "false";
        tgtTd.contentEditable = "false";
        showEl(editBtn);
        hideEl(saveBtn);
        item.source_term = newSrc;
        item.target_term = newTgt;
      } catch (err) {
        setError(errorEl, err.message);
        saveBtn.disabled = false;
      }
    });

    delBtn.addEventListener("click", async () => {
      if (!confirm("Удалить термин?")) return;
      delBtn.disabled = true;
      try {
        await apiFetch(`/api/glossary/${item.id}`, { method: "DELETE" });
        tr.remove();
        if (bodyEl.childElementCount === 0) {
          hideEl(tableEl);
          showEl(emptyEl);
        }
      } catch (err) {
        setError(errorEl, err.message);
        delBtn.disabled = false;
      }
    });

    return tr;
  }

  addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearError(addError);

    const src = srcInput.value.trim();
    const tgt = tgtInput.value.trim();
    if (!src || !tgt) {
      setError(addError, "Заполните оба поля.");
      return;
    }

    const submitBtn = addForm.querySelector("button[type=submit]");
    submitBtn.disabled = true;

    try {
      const item = await apiFetch("/api/glossary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: getUserId(),
          source_term: src,
          target_term: tgt,
        }),
      });
      srcInput.value = "";
      tgtInput.value = "";

      hideEl(emptyEl);
      if (!tableEl.classList.contains("hidden") === false) {
        showEl(tableEl);
      }
      showEl(tableEl);
      bodyEl.appendChild(buildGlossaryRow(item));
    } catch (err) {
      setError(addError, err.message);
    } finally {
      submitBtn.disabled = false;
    }
  });

  loadGlossary();
}

// ============================================================
// Utilities
// ============================================================

function truncate(str, max) {
  if (!str) return "—";
  return str.length > max ? str.slice(0, max - 1) + "…" : str;
}

// ============================================================
// Bootstrap
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
  initUserId();
  initUploadPage();
  initHistoryPage();
  initGlossaryPage();
});
