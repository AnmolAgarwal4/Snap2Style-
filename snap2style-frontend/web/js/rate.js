const starsEl    = document.getElementById("stars");
const submitBtn  = document.getElementById("submitRating");
const statusRate = document.getElementById("rateStatus");
const feedbackEl = document.getElementById("feedback");

if (starsEl && submitBtn && statusRate) {
  let rating = 0;

  starsEl.querySelectorAll(".star").forEach(star => {
    star.addEventListener("mouseenter", () => highlight(+star.dataset.val));
    star.addEventListener("mouseleave", () => highlight(rating));
    star.addEventListener("click", () => { rating = +star.dataset.val; highlight(rating); });
  });

  function highlight(n){
    starsEl.querySelectorAll(".star").forEach(s => {
      s.classList.toggle("active", +s.dataset.val <= n);
    });
  }

  submitBtn.addEventListener("click", () => {
    if (!rating) { statusRate.textContent = "Pick a star rating first."; return; }
    const entries = JSON.parse(localStorage.getItem("s2s_ratings") || "[]");
    entries.push({ rating, feedback: feedbackEl?.value || "", ts: new Date().toISOString() });
    localStorage.setItem("s2s_ratings", JSON.stringify(entries));
    statusRate.textContent = "Thanks for your feedback! ✨";
    if (feedbackEl) feedbackEl.value = "";
    rating = 0;
    highlight(0);
  });
}
