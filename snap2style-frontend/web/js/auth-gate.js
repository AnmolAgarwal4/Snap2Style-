document.addEventListener("DOMContentLoaded", () => {
  const trigger = document.querySelector(".profile-trigger");
  const dropdown = document.querySelector(".profile-dropdown");
  if (trigger && dropdown) {
    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      dropdown.style.display = dropdown.style.display === "block" ? "none" : "block";
    });
    document.addEventListener("click", (e) => {
      if (!dropdown.contains(e.target) && !trigger.contains(e.target)) {
        dropdown.style.display = "none";
      }
    });
  }
});
(async () => {
  try {
    const res = await fetch("/env-check", { cache: "no-store", credentials: "include" });
    const env = await res.json();
    const btn = document.querySelector("[data-google-btn], #google-login");
    if (btn) {
      if (env.google_oauth) {
        btn.style.display = "block";
        btn.addEventListener("click", () => (window.location.href = "/auth/google/start"));
      } else {
        btn.style.display = "none";
      }
    }
  } catch (e) {}
})();
