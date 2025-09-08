// ----- Config -----
const API_BASE = "http://127.0.0.1:8000";

// ----- Elements -----
const dropzone   = document.getElementById("dropzone");
const fileInput  = document.getElementById("fileInput");
const preview    = document.getElementById("preview");
const styleSelect= document.getElementById("styleSelect");
const submitBtn  = document.getElementById("submitBtn");
const statusEl   = document.getElementById("status");
const loading    = document.getElementById("loading");

const imgBefore  = document.getElementById("imgBefore");
const imgAfter   = document.getElementById("imgAfter");
const afterWrap  = document.querySelector(".after-wrap");
const slider     = document.getElementById("slider");
const openFull   = document.getElementById("openFull");
const resetBtn   = document.getElementById("resetBtn");

// safety: ensure button can't submit the page
if (submitBtn) submitBtn.setAttribute("type", "button");

// ----- Parallax / Hero Animations -----
window.addEventListener("mousemove", (e) => {
  const { innerWidth:w, innerHeight:h } = window;
  const x = (e.clientX - w/2) / w;
  const y = (e.clientY - h/2) / h;
  const grad = document.querySelector(".bg-gradient");
  const glow = document.querySelector(".bg-glow");
  if (grad) grad.style.transform = `translate(${x*20}px, ${y*20}px)`;
  if (glow) glow.style.transform = `translate(${x*30}px, ${y*10}px)`;
});

// ----- Drag & Drop -----
const openPicker = () => fileInput?.click();
dropzone?.addEventListener("click", openPicker);
dropzone?.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("dragging"); });
dropzone?.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone?.addEventListener("drop", (e) => {
  e.preventDefault(); dropzone.classList.remove("dragging");
  if (!e.dataTransfer.files?.length) return;
  setPreviewFile(e.dataTransfer.files[0]);
});
fileInput?.addEventListener("change", () => {
  if (fileInput.files?.length) setPreviewFile(fileInput.files[0]);
});
function setPreviewFile(file){
  if (!file.type.startsWith("image/")) {
    if (statusEl) statusEl.textContent = "Please select an image.";
    return;
  }
  const reader = new FileReader();
  reader.onload = (ev) => {
    if (preview) { preview.src = ev.target.result; preview.style.display = "block"; }
    if (imgBefore) imgBefore.src = ev.target.result;
  };
  reader.readAsDataURL(file);
}

// ----- Compare Slider -----
slider?.addEventListener("input", () => {
  if (afterWrap) afterWrap.style.width = `${slider.value}%`;
});

// ----- Helpers -----
function waitForImage(imgEl) {
  return new Promise((resolve) => {
    if (!imgEl) return resolve();
    if (imgEl.complete && imgEl.naturalWidth > 0) return resolve();
    imgEl.onload = () => resolve();
    imgEl.onerror = () => resolve();
  });
}
function scrollToResult() {
  const el = document.getElementById("result");
  if (!el) return;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

// prevent jumping to top if link not ready
openFull?.addEventListener("click", (e) => {
  const href = openFull.getAttribute("href");
  if (!href || href === "#" || href.startsWith("#")) {
    e.preventDefault();
    if (imgAfter?.src) window.open(imgAfter.src, "_blank", "noopener,noreferrer");
  }
});

// ----- Submit via button -----
submitBtn?.addEventListener("click", async () => {
  if (!fileInput?.files?.length) { if (statusEl) statusEl.textContent = "Select an image first."; return; }
  if (statusEl) statusEl.textContent = "";
  loading?.classList.remove("hidden");

  try {
    const fd = new FormData();
    fd.append("style", styleSelect.value);
    fd.append("file", fileInput.files[0]);

    const res = await fetch(`${API_BASE}/style-image`, { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data?.error || "Failed to style image");

    const styledUrl = data.styledUrls?.[0];
    if (!styledUrl) throw new Error("No image returned");

    const absoluteUrl = styledUrl.startsWith("http") ? styledUrl : `${API_BASE}${styledUrl}`;

    if (imgAfter) imgAfter.src = absoluteUrl;
    if (openFull) {
      openFull.href = absoluteUrl;
      openFull.setAttribute("target", "_blank");
      openFull.setAttribute("rel", "noopener noreferrer");
    }

    if (afterWrap) afterWrap.style.width = "50%";
    if (slider) slider.value = 50;

    await waitForImage(imgAfter);
    scrollToResult();
  } catch (err) {
    console.error(err);
    if (statusEl) statusEl.textContent = err.message;
  } finally {
    loading?.classList.add("hidden");
  }
});

// ----- Reset -----
resetBtn?.addEventListener("click", () => {
  if (fileInput) fileInput.value = "";
  if (preview) { preview.removeAttribute("src"); preview.style.display = "none"; }
  imgBefore?.removeAttribute("src");
  imgAfter?.removeAttribute("src");
  if (afterWrap) afterWrap.style.width = "50%";
  if (slider) slider.value = 50;
  if (statusEl) statusEl.textContent = "";
});
