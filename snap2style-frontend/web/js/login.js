// web/js/login.js
(() => {
  const API = location.origin.includes(':8000')
    ? location.origin
    : 'http://127.0.0.1:8000';

  const form   = document.getElementById('loginForm');
  const email  = document.getElementById('loginEmail');
  const pass   = document.getElementById('loginPass');
  const msgEl  = document.getElementById('loginMsg');

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();               // stop form reload
    msgEl && (msgEl.textContent = 'Logging in…');

    try {
      const res = await fetch(API + '/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',       // IMPORTANT: store/send cookie
        body: JSON.stringify({
          email: (email.value || '').trim(),
          password: pass.value || ''
        })
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Login failed');

      // give the browser a tick to persist the cookie, then go
      setTimeout(() => {
        window.location.assign('snap.html');
      }, 50);
    } catch (err) {
      msgEl && (msgEl.textContent = err.message || String(err));
    }
  });
})();
// after res.ok:
