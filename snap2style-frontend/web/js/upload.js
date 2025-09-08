/* =========================================================================
   Snap2Style - Upload & Style (pin Before image so it never disappears)
   ========================================================================= */

const API_BASE = window.API_BASE || "http://127.0.0.1:8000";
const ENABLE_LOGS = true;
const PERSIST_LAST_RESULT = true;

const log = (...a) => ENABLE_LOGS && console.log("[upload]", ...a);
const el  = (id) => document.getElementById(id);
const on  = (n,e,fn,o) => n && n.addEventListener(e,fn,o);
const isImageFile = (f) => !!f && typeof f.type === "string" && f.type.startsWith("image/");
const readAsDataURL = (file) => new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res(r.result); r.onerror=rej; r.readAsDataURL(file); });
async function waitForLoad(img){ if(!img) return; if(img.complete && img.naturalWidth>0) return; await new Promise(r=>{ img.onload=r; img.onerror=r; }); }
function safeGetLS(k){ try { return localStorage.getItem(k); } catch { return null; } }
function safeSetLS(k,v){ try { localStorage.setItem(k,v); } catch {} }
const toAbs = (u) => /^https?:\/\//i.test(u) ? u : `${API_BASE}${u}`;
const bust  = (u) => u + (u.includes("?") ? "&" : "?") + "t=" + Date.now();

document.addEventListener("DOMContentLoaded", () => {
  log("DOM ready");

  // ---- Elements ----
  const dropzone     = el("dropzone");
  // 🔧 Key fix: support both #fileInput and #fileOverlay, then fallback.
  const fileInput    = el("fileInput")
                    || el("fileOverlay")
                    || Array.from(document.querySelectorAll('input[type="file"]'))
                         .find(i => !i.disabled && i.offsetParent !== null)
                    || null;

  const preview      = el("preview");
  const imgBefore    = el("imgBefore");
  const imgAfter     = el("imgAfter");
  const afterWrap    = document.querySelector(".after-wrap");
  const slider       = el("slider");
  const openFull     = el("openFull");
  const downloadBtn  = el("downloadBtn");

  const styleSelect  = el("styleSelect");
  const instructions = el("instructions");
  const submitBtn    = el("submitBtn");
  const resetBtn     = el("resetBtn");
  const statusEl     = el("status");
  const loading      = el("loading");

  if (submitBtn) submitBtn.setAttribute("type","button");

  // ---- Keep the 'before' image pinned ----
  let currentFile = null;
  let pinnedBeforeSrc = ""; // data URL of the chosen file

  function ensureBeforeVisible() {
    if (!imgBefore) return;
    if (!imgBefore.getAttribute("src")) {
      if (pinnedBeforeSrc) {
        imgBefore.src = pinnedBeforeSrc;
        log("Restored before image");
      }
    }
  }

  // ---- Parallax (nice-to-have) ----
  const grad = document.querySelector(".bg-gradient");
  const glow = document.querySelector(".bg-glow");
  let rafId = null, lastX = 0, lastY = 0;
  on(window, "mousemove", (e) => {
    lastX = e.clientX; lastY = e.clientY;
    if (rafId) return;
    rafId = requestAnimationFrame(() => {
      rafId = null;
      const { innerWidth:w, innerHeight:h } = window;
      const x = (lastX - w/2) / w, y = (lastY - h/2) / h;
      if (grad) grad.style.transform = `translate(${x*20}px, ${y*20}px)`;
      if (glow) glow.style.transform = `translate(${x*30}px, ${y*10}px)`;
    });
  });

  // ---- Picker open helpers ----
  let pickerOpen = false;
  const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
  function openPicker(e){
    if (e) e.stopPropagation();
    if (!fileInput || pickerOpen) return;
    pickerOpen = true;
    fileInput.value = ""; // allow re-choosing same file
    if (isSafari) setTimeout(()=>fileInput.click(), 0);
    else fileInput.click();
  }
  on(dropzone, "click", openPicker);
  on(document.querySelector(".drop-content"), "click", openPicker);
  on(preview, "click", openPicker);
  on(fileInput, "click", (e)=> e.stopPropagation());
  window.addEventListener("focus", ()=> setTimeout(()=>{ pickerOpen=false; }, 200));

  // ---- Drag & Drop ----
  on(dropzone, "dragover", (e) => { e.preventDefault(); dropzone?.classList.add("dragging"); });
  on(dropzone, "dragleave", () => dropzone?.classList.remove("dragging"));
  on(dropzone, "drop", async (e) => {
    e.preventDefault(); dropzone?.classList.remove("dragging"); pickerOpen=false;
    const f = e.dataTransfer.files?.[0];
    if (f) await setPreviewFile(f);
  });

  // ---- File -> Preview & Before ----
  async function setPreviewFile(file){
    if (!isImageFile(file)) { return setStatus("Please select an image."); }
    currentFile = file;
    try {
      const dataURL = await readAsDataURL(file);
      pinnedBeforeSrc = dataURL;               // <-- pin it
      if (preview) { preview.src = dataURL; preview.style.display = "block"; }
      if (imgBefore) imgBefore.src = dataURL;  // <-- set before
      clearStatus();
    } catch {
      setStatus("Could not preview image.");
    }
  }

  on(fileInput, "change", async () => {
    pickerOpen = false;
    const f = fileInput.files?.[0];
    if (f) await setPreviewFile(f);
  });

  // ---- Compare & Open full ----
  on(slider, "input", () => { if (afterWrap) afterWrap.style.width = `${slider.value}%`; });
  on(openFull, "click", (e) => {
    const href = openFull?.getAttribute("href");
    if (!href || href === "#" || href.startsWith("#")) {
      e.preventDefault();
      if (imgAfter?.src) window.open(imgAfter.src, "_blank", "noopener,noreferrer");
    }
  });

  // ---- Submit ----
  on(submitBtn, "click", async () => {
    const f = currentFile || fileInput?.files?.[0];
    if (!isImageFile(f)) { return setStatus("Select an image first."); }

    // Make sure before is visible before we start
    ensureBeforeVisible();

    clearStatus(); showLoading(true);
    try {
      const fd = new FormData();
      fd.append("style", (styleSelect?.value || ""));
      fd.append("instructions", (instructions?.value || ""));
      fd.append("file", f);

      const res  = await fetch(`${API_BASE}/style-image`, {
        method: "POST",
        body: fd,
        mode: "cors",
        cache: "no-store",
        credentials: "include",
      });

      const text = await res.text();
      let data; try { data = JSON.parse(text); } catch { data = null; }
      if (!res.ok) throw new Error((data && (data.error || data.detail || data.message)) || `HTTP ${res.status}`);

      const styledUrl = data?.styledUrls?.[0];
      if (!styledUrl) throw new Error("No image returned");

      const finalUrl   = toAbs(styledUrl);
      const displayUrl = bust(finalUrl);

      // Re-assert the before image in case any UI reset happened
      ensureBeforeVisible();

      if (imgAfter) { imgAfter.src = displayUrl; imgAfter.style.display = "block"; }
      if (openFull) { openFull.href = finalUrl; openFull.target = "_blank"; openFull.rel = "noopener noreferrer"; }

      const fname = (() => {
        try { return new URL(finalUrl).pathname.split("/").pop() || "image.png"; }
        catch { return (finalUrl.split("/").pop() || "image.png").split("?")[0]; }
      })();
      const dlUrl = `${API_BASE}/download/${fname}`;
      if (downloadBtn) {
        if (downloadBtn.tagName.toLowerCase() === "a") {
          downloadBtn.href = dlUrl;
          downloadBtn.download = fname;
          downloadBtn.classList.remove("hidden");
        } else {
          downloadBtn.classList.remove("hidden");
          downloadBtn.onclick = () => {
            const a = document.createElement("a");
            a.href = dlUrl; a.download = fname;
            document.body.appendChild(a); a.click(); a.remove();
          };
        }
      }

      if (afterWrap) afterWrap.style.width = "50%";
      if (slider) slider.value = 50;

      await waitForLoad(imgAfter);
      document.getElementById("result")?.scrollIntoView({ behavior: "smooth", block: "start" });

      if (PERSIST_LAST_RESULT) safeSetLS("s2s:lastImgUrl", finalUrl);
      clearStatus();
    } catch (e) {
      log("error", e);
      setStatus(e.message || "Failed to style image.");
    } finally {
      showLoading(false);
      // Restore before image once more after the overlay is gone
      ensureBeforeVisible();
    }
  });

  // ---- Reset ----
  on(resetBtn, "click", () => {
    currentFile = null;
    pinnedBeforeSrc = "";
    if (fileInput) fileInput.value = "";
    if (preview) { preview.removeAttribute("src"); preview.style.display = "none"; }
    if (imgBefore) imgBefore.removeAttribute("src");
    if (imgAfter) imgAfter.removeAttribute("src");
    if (afterWrap) afterWrap.style.width = "50%";
    if (slider) slider.value = 50;
    downloadBtn?.classList.add("hidden");
    clearStatus();
  });

  // ---- Restore last result (optional) ----
  const last = safeGetLS("s2s:lastImgUrl");
  if (PERSIST_LAST_RESULT && last && imgAfter) {
    imgAfter.src = bust(last);
    if (openFull) openFull.href = last;
    if (afterWrap) afterWrap.style.width = "50%";
    if (slider) slider.value = 50;
    log("Restored last result:", last);
  }

  // ---- helpers ----
  function setStatus(msg){ if (statusEl) statusEl.textContent = msg || ""; }
  function clearStatus(){ setStatus(""); }
  function showLoading(show){ if (!loading) return; loading.classList.toggle("hidden", !show); }
});

// Helpers
function safeSet(key, val) {
  const s = JSON.stringify(val);
  try { localStorage.setItem(key, s); return "local"; }
  catch (e1) {
    try { sessionStorage.setItem(key, s); return "session"; }
    catch (e2) { log("Persist failed:", e1, e2); return null; }
  }
}
function safeGet(key) {
  let s = localStorage.getItem(key);
  if (!s) s = sessionStorage.getItem(key);
  return s ? JSON.parse(s) : null;
}
function safeDel(key) {
  localStorage.removeItem(key); sessionStorage.removeItem(key);
}

async function fileToDataURL(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

async function setBeforeImageFromFile(file) {
  if (!isImageFile(file)) {
    alert("Please select an image.");
    return;
  }
  const dataURL = await fileToDataURL(file);
  await setBeforeImageFromDataURL(dataURL, { name: file.name, type: file.type });
}

async function setBeforeImageFromDataURL(dataURL, meta = {}) {
  const img = el("beforeImg");
  img.src = dataURL;

  await new Promise((r) => { if (img.complete && img.naturalWidth) r(); else img.onload = r; });

  const storageUsed = safeSet(PERSIST_KEY, dataURL);
  safeSet(PERSIST_META, {
    ...meta,
    w: img.naturalWidth,
    h: img.naturalHeight,
    storedIn: storageUsed
  });
  log("Pinned image:", meta.name || "(from restore)", "→", storageUsed);
}

function restorePinned() {
  const dataURL = safeGet(PERSIST_KEY);
  const meta = safeGet(PERSIST_META);
  if (dataURL) {
    setBeforeImageFromDataURL(dataURL, meta || {});
  } else {
    log("No pinned image to restore.");
  }
}

function clearPinned() {
  safeDel(PERSIST_KEY);
  safeDel(PERSIST_META);
  const img = el("beforeImg");
  img.removeAttribute("src");
  log("Cleared pinned image.");
}

// Wire up
document.addEventListener("DOMContentLoaded", () => {
  const fileInput = el("fileInput");
  const clearBtn = el("clearBtn");

  // Prevent any form auto-submit wiping state
  if (fileInput && fileInput.form) {
    fileInput.form.addEventListener("submit", (e) => e.preventDefault());
  }

  fileInput?.addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (file) await setBeforeImageFromFile(file);
  });

  clearBtn?.addEventListener("click", clearPinned);

  // Drag & drop (optional)
  const dropZone = document.body;
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); });
  dropZone.addEventListener("drop", async (e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (file) await setBeforeImageFromFile(file);
  });

  // Restore if we have something pinned
  restorePinned();
});
