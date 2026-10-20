// SkySift dashboard behaviour, no dependency: preferences, the shared dialog, count-ups, keys, toasts, row links.
(() => {
  const root = document.documentElement;
  const token = document.body.dataset.token;
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- Mode and theme: the forms post to /prefs without JavaScript too; here the theme switches in place and the
  // mode reloads the page, because simple and expert render different markup.
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest("form[data-prefs]");
    if (!form || !e.submitter) return;
    e.preventDefault();
    const body = new URLSearchParams(new FormData(form, e.submitter));
    try {
      await fetch("/prefs", { method: "POST", body, headers: { "X-SkySift-Token": token }, redirect: "manual" });
    } catch (err) { toast(`Could not save the preference: ${err}`, "fail"); return; }
    if (e.submitter.name === "theme") {
      root.dataset.theme = e.submitter.value;
      form.querySelectorAll("button[name=theme]").forEach((b) => b.setAttribute("aria-pressed", String(b === e.submitter)));
      document.dispatchEvent(new CustomEvent("skysift:theme", { detail: { theme: e.submitter.value } }));
    } else {
      window.location.assign(body.get("next") || "/");
    }
  });

  // ---- The dialog: any [data-dialog-src] control loads that partial into #dialog-slot and opens #dialog. Focus moves
  // in, Tab stays inside, Escape, the close button or a click on the backdrop closes it, and focus returns.
  const dialog = document.getElementById("dialog");
  let opener = null;
  const focusables = () => [...dialog.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
  const openDialog = () => {
    if (!dialog.open) dialog.showModal();
    const first = focusables().find((el) => !el.matches("[data-dialog-close]")) || focusables()[0];
    if (first) first.focus();
  };
  const closeDialog = () => { if (dialog.open) dialog.close(); };
  dialog.addEventListener("close", () => {
    document.getElementById("dialog-slot").replaceChildren();
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  });
  dialog.addEventListener("click", (e) => {
    if (e.target.closest("[data-dialog-close]")) { closeDialog(); return; }
    // a click on the backdrop reports the dialog itself as its target, and so does one on the dialog's own padding
    const box = dialog.getBoundingClientRect();
    const outside = e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom;
    if (e.target === dialog && outside) closeDialog();
  });
  dialog.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const els = focusables();
    if (!els.length) return;
    const [first, last] = [els[0], els[els.length - 1]];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  document.addEventListener("click", (e) => {
    const trigger = e.target.closest("[data-dialog-src]");
    if (!trigger) return;
    e.preventDefault();
    e.stopPropagation();
    opener = trigger;
    window.htmx.ajax("GET", trigger.dataset.dialogSrc, { target: "#dialog-slot", swap: "innerHTML" })
      .then(openDialog, () => toast("Could not load the details. Try again.", "fail"));
  });

  // ---- Count-ups: [data-count] numbers rise from zero over 600 ms on first paint, then stay quiet.
  const countUp = (el) => {
    const target = Number(el.dataset.count);
    if (!Number.isFinite(target)) return;
    const digits = Number(el.dataset.decimals || 0);
    const fmt = (v) => v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 600);
      el.textContent = fmt(target * (1 - (1 - t) ** 3));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };
  if (!reducedMotion()) document.querySelectorAll("[data-count]").forEach(countUp);

  // ---- Toasts: a response header HX-Trigger: {"toast": {"message": "...", "tone": "pass"}} shows one.
  function toast(message, tone) {
    const region = document.getElementById("toasts");
    const el = document.createElement("div");
    el.className = `toast${tone ? ` toast-${tone}` : ""}`;
    el.textContent = message;                       // server text is untrusted: text, never markup
    region.appendChild(el);
    setTimeout(() => { el.classList.add("is-leaving"); setTimeout(() => el.remove(), 200); }, 5000);
  }
  document.body.addEventListener("toast", (e) => {
    const d = e.detail || {};
    if (d.message || d.value) toast(String(d.message || d.value), d.tone);
  });

  // ---- Keys on the signal page: a approve, r reject, n needs data, j next, k previous; ? lists them.
  const KEYS = { a: "btn-approve", r: "btn-reject", n: "btn-needs-data", j: "link-next", k: "link-prev" };
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || dialog.open || e.target.matches("input, textarea, select, [contenteditable]")) return;
    if (e.key === "?") { toast("Keys: j next, k previous, a approve, r reject, n needs data, Esc closes a popup."); return; }
    const el = KEYS[e.key] && document.getElementById(KEYS[e.key]);
    if (el) el.click();
  });

  // ---- Table rows that link to a signal.
  document.addEventListener("click", (e) => {
    const row = e.target.closest("tr.row-link[data-href]");
    if (row && !e.target.closest("a, button, input")) window.location = row.dataset.href;
  });

  // htmx does not swap 4xx responses by default; the start-sweep form answers 400/409 with the form and its error
  document.body.addEventListener("htmx:beforeSwap", (e) => {
    if (e.detail.xhr.status === 400 || e.detail.xhr.status === 409) { e.detail.shouldSwap = true; e.detail.isError = false; }
  });
})();
