// web/js/app.js
(() => {
  if (window.__S2S_SINGLE__) return;
  window.__S2S_SINGLE__ = true;

  // ------------ config / utils ------------
  const API =
    window.API_BASE ||
    (location.origin.includes(":8000") ? location.origin : "http://127.0.0.1:8000");

  const $ = (id) => document.getElementById(id);
  const on = (el, ev, fn) => el && el.addEventListener(ev, fn);

  async function jsonOrThrow(res) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.detail || ("HTTP " + res.status));
    return data;
  }

  function resolveUrl(u) {
    return /^https?:\/\//i.test(u) ? u : API + u;
  }

  // ------------ auth / account UI ------------
  let inflightCredits = null;

  async function fetchCreditsOnce() {
    if (inflightCredits) return inflightCredits;
    inflightCredits = (async () => {
      const res = await fetch(API + "/credits", { credentials: "include", cache: "no-store" });
      const data = await res.json().catch(() => null);
      if (data) updateUI(data);
      inflightCredits = null;
      return data;
    })();
    return inflightCredits;
  }
  window.s2sFetchCredits = fetchCreditsOnce; // optional global hook

  function updateUI(data) {
    const acctEmail   = $("acctEmail");
    const acctPlan    = $("acctPlan");
    const acctAvatar  = $("acctAvatar");   // big circle in panel
    const acctInitial = $("acctInitial");  // small circle in header
    const authedItems = $("authedItems");
    const guestItems  = $("guestItems");

    const isUser   = data?.kind === "user";
    const email    = isUser ? (data.email || "") : "";
    const verified = !!data?.verified;
    const initial  = (isUser ? email : "Guest").trim().charAt(0).toUpperCase() || "G";

    if (acctInitial) acctInitial.textContent = initial;
    if (acctAvatar)  acctAvatar.textContent  = initial;
    if (acctEmail)   acctEmail.textContent   = isUser ? email : "Guest";
    if (acctPlan)    acctPlan.textContent    = isUser ? (verified ? "Verified" : "Unverified")
                                                      : "Not signed in";

    if (authedItems) authedItems.style.display = isUser ? "block" : "none";
    if (guestItems)  guestItems.style.display  = isUser ? "none"  : "block";
  }

  function wireAccountMenu() {
    const acctBtn    = $("acctBtn");
    const overlay    = $("acctOverlay") || document.querySelector(".acct-backdrop");
    const logoutItem = $("logoutItem");

    const open = () => {
      if (!overlay) return;
      overlay.style.display = "block";
      overlay.setAttribute("aria-hidden", "false");
      overlay.classList.add("open");
    };
    const close = () => {
      if (!overlay) return;
      overlay.setAttribute("aria-hidden", "true");
      overlay.classList.remove("open");
      setTimeout(() => { overlay.style.display = "none"; }, 160);
    };

    if (acctBtn && overlay) {
      on(acctBtn, "click", (e) => {
        e.stopPropagation();
        const hidden = overlay.getAttribute("aria-hidden") !== "false";
        hidden ? open() : close();
      });
      on(document, "click", (e) => {
        if (overlay.getAttribute("aria-hidden") === "true") return;
        if (!overlay.contains(e.target) && e.target !== acctBtn) close();
      });
      on(document, "keydown", (e) => { if (e.key === "Escape") close(); });
    }

    on(logoutItem, "click", async () => {
      try { await fetch(API + "/auth/logout", { method: "POST", credentials: "include" }); } catch {}
      updateUI(null);
      overlay && close();
      // stay on page so guests can still use freebies
    });
  }

  // ------------ login page wiring (email/password) ------------
  function wireLoginForm() {
    const form = $("loginForm");
    if (!form) return;

    on(form, "submit", async (e) => {
      e.preventDefault();
      const email = $("loginEmail")?.value?.trim();
      const pass  = $("loginPassword")?.value || "";
      if (!email || !pass) return alert("Enter email and password");

      try {
        const res = await fetch(API + "/auth/login", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password: pass }),
        });
        await jsonOrThrow(res);
        await fetchCreditsOnce();

        // go to app
        location.href = (window.S2S_AFTER_LOGIN || "/web/snap.html");
      } catch (err) {
        alert(err.message);
      }
    });

    // Optional: “Continue with Google” button (server OAuth redirect flow)
    const gbtn = $("googleLoginBtn");
    on(gbtn, "click", () => { location.href = API + "/auth/google/start"; });
  }

  // ------------ register page wiring (optional) ------------
  function wireRegisterForm() {
    const form = $("registerForm");
    if (!form) return;

    on(form, "submit", async (e) => {
      e.preventDefault();
      const email = $("regEmail")?.value?.trim();
      const pass  = $("regPassword")?.value || "";
      if (!email || !pass) return alert("Enter email and password (min 6 chars)");

      try {
        const res = await fetch(API + "/auth/register", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password: pass }),
        });
        await jsonOrThrow(res);
        await fetchCreditsOnce();
        alert("Registered. Check your email for verification link/OTP.");
        location.href = "/web/snap.html";
      } catch (err) {
        alert(err.message);
      }
    });

    const gbtn = $("googleRegisterBtn");
    on(gbtn, "click", () => { location.href = API + "/auth/google/start"; });
  }

  // ------------ upload (snap.html) ------------
  async function s2sUpload(file, instructions = "", style = "") {
    const fd = new FormData();
    fd.append("style", style);
    fd.append("instructions", instructions);
    fd.append("file", file);

    const res = await fetch(API + "/style-image", {
      method: "POST",
      body: fd,
      credentials: "include",
      cache: "no-store",
    });
    return jsonOrThrow(res);
  }

  function wireUploadForm() {
    const form = $("uploadForm");
    if (!form) return;

    on(form, "submit", async (e) => {
      e.preventDefault();
      const file = $("file")?.files?.[0];
      const instructions = $("instructions")?.value || "";
      const style = $("style")?.value || "";
      if (!file) return alert("Pick an image first");

      try {
        const out = await s2sUpload(file, instructions, style);
        const raw = out.styledUrls?.[0];
        if (!raw) throw new Error("No image URL from server");
        const url = resolveUrl(raw) + (raw.includes("?") ? "&" : "?") + "t=" + Date.now();
        const img = $("resultImg");
        if (img) img.src = url;
      } catch (err) {
        alert(err.message);
      }
    });
  }

  // ------------ boot ------------
  async function boot() {
    wireAccountMenu();
    wireLoginForm();
    wireRegisterForm();
    wireUploadForm();

    // ensure guest gets a cookie and header data populates
    try { await fetchCreditsOnce(); } catch {}

    if ($("acctBtn") || $("acctOverlay") || $("acctEmail") || $("acctPlan")) {
      try { await fetchCreditsOnce(); } catch {}
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  // expose a tiny API if needed
  window.S2S = {
    fetchCredits: fetchCreditsOnce,
    upload: s2sUpload,
    API,
  };
})();
