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
    let res;
    try {
      res = await fetch("/prefs", { method: "POST", body, headers: { "X-SkySift-Token": token }, redirect: "manual" });
    } catch (err) { toast(`Could not save the preference: ${err}`, "fail"); return; }
    // a saved preference answers 303, which redirect "manual" reports as an opaque redirect
    if (!(res.type === "opaqueredirect" || res.ok)) {
      toast(`The preference was not saved (${res.status}). Reload the page and try again.`, "fail");
      return;
    }
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
    'a[href], button, summary, input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => !el.disabled && !el.closest("[hidden]") && el.offsetParent !== null);
  const openDialog = () => {
    // the dialog is named by the loaded partial's heading (#dialog-title in the popup, #sweep-title in the drawer)
    const heading = dialog.querySelector("[data-dialog-focus][id]");
    dialog.setAttribute("aria-labelledby", heading ? heading.id : "dialog-title");
    if (!dialog.open) dialog.showModal();
    // a long partial marks its heading [data-dialog-focus] (tabindex -1) so focus starts at the top, not on a far link
    // a trigger with data-dialog-at (the Dossiers tile's probability pills) opens the partial at that block instead
    const at = opener && opener.dataset.dialogAt;
    const block = at ? dialog.querySelector(`#${CSS.escape(at)}`) : null;
    const first = block || dialog.querySelector("[data-dialog-focus]")
      || focusables().find((el) => !el.matches("[data-dialog-close]")) || focusables()[0];
    if (first) first.focus();
    if (block) block.scrollIntoView({ block: "start" });
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
    const slot = document.getElementById("dialog-slot");
    slot.replaceChildren();
    const failed = () => { opener = null; toast("Could not load the details. Try again.", "fail"); };
    // htmx resolves the promise on a 404 or 500 too, without swapping: open only when something arrived
    window.htmx.ajax("GET", trigger.dataset.dialogSrc, { target: "#dialog-slot", swap: "innerHTML" })
      .then(() => (slot.childElementCount ? openDialog() : failed()), failed);
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

  // ---- Copy: a [data-copy] button puts its text on the clipboard. Without the clipboard API (an old browser, a page
  // that is not a secure context) the text goes into a hidden textarea, is selected there and copied the old way.
  document.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-copy]");
    if (!button) return;
    const text = button.dataset.copy;
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied", "pass");
    } catch {
      const area = document.createElement("textarea");
      area.className = "copy-fallback"; area.value = text; area.readOnly = true; area.setAttribute("aria-hidden", "true");
      document.body.appendChild(area);
      area.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch { copied = false; }
      area.remove();
      button.focus();
      toast(copied ? "Copied" : "Could not copy. The browser refused the clipboard.", copied ? "pass" : "fail");
    }
  });

  // ---- The expert queue. A row opens its signal: at 1280 px and above into #detail beside the table (the row's
  // hx-get, fired by the "split" event) with the URL remembering it, narrower on the signal page. j and k move the
  // selection, Enter opens it.
  const queue = document.getElementById("queue");
  const detail = document.getElementById("detail");
  const wide = window.matchMedia("(min-width: 1280px)");
  const queueRows = () => (queue ? [...queue.querySelectorAll("tr.row-link[data-href]")] : []);
  const selected = () => queueRows().find((r) => r.classList.contains("is-selected"));
  const select = (row) => {
    queueRows().forEach((r) => {
      r.classList.toggle("is-selected", r === row);
      if (r === row) r.setAttribute("aria-current", "true"); else r.removeAttribute("aria-current");
    });
    row.scrollIntoView({ block: "nearest" });
  };
  const openRow = (row) => {
    if (!(detail && wide.matches)) { window.location = row.dataset.href; return; }
    window.htmx.trigger(row, "split");      // the selection and the URL follow only a detail that arrived (below)
  };
  if (queue) {
    // A newer j, k or click makes hx-sync="#queue:replace" abort the detail load still in flight. The order of events
    // (htmx 2.0.3): the new load's sync step calls xhr.abort() on the old one, whose onabort fires htmx:afterRequest
    // (successful undefined) and then htmx:sendAbort, all on the same call stack, and only then does the new load
    // fire htmx:beforeRequest. Neither the xhr's identity nor its state tells the abort apart inside afterRequest
    // (readyState is 4 and status 0 there, as after a network error), so the failure toast waits one microtask, by
    // which time sendAbort has marked an aborted xhr. A 404, a 500 or a network error (htmx:sendError) still toasts.
    const aborted = new WeakSet();
    queue.addEventListener("htmx:sendAbort", (e) => { if (e.detail.xhr) aborted.add(e.detail.xhr); });
    queue.addEventListener("htmx:afterRequest", (e) => {
      const row = e.detail.elt && e.detail.elt.closest && e.detail.elt.closest("tr.row-link[data-id]");
      if (!row || e.detail.target !== detail) return;
      if (!e.detail.successful) {
        const xhr = e.detail.xhr;
        queueMicrotask(() => { if (!aborted.has(xhr)) toast("Could not load the details. Try again.", "fail"); });
        return;
      }
      select(row);
      const url = new URL(window.location.href);
      url.searchParams.set("signal", row.dataset.id);
      history.replaceState(null, "", url);
    });
  }
  const step = (by) => {
    const rows = queueRows();
    if (!rows.length) return;
    const at = rows.indexOf(selected());
    const row = rows[at < 0 ? (by > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, at + by))];
    select(row);
    // in the split view the detail follows the selection (spec 4.3), so a, r and n act on the row j and k reached
    if (detail && wide.matches) openRow(row);
  };
  // the signal a, r and n may decide in the queue: the one #detail shows, and only while it is the selected row
  const detailMatchesSelection = () => {
    const shown = detail && detail.querySelector("[data-signal]");
    const row = selected();
    return Boolean(shown && row && shown.dataset.signal === row.dataset.id);
  };
  document.addEventListener("click", (e) => {
    const row = e.target.closest("tr.row-link[data-href]");
    if (!row) return;
    // the target id is a real link for the keyboard, middle-click and a new tab; a plain mouse click on it at
    // split-view width (e.detail > 0: Enter on the focused link reports 0 and follows the link) loads the detail
    // beside the table like a click anywhere else on the row
    const plain = e.button === 0 && !(e.ctrlKey || e.metaKey || e.shiftKey || e.altKey);
    if (plain && e.detail > 0 && e.target.closest("a.row-name") && detail && wide.matches) {
      e.preventDefault(); openRow(row); return;
    }
    if (!e.target.closest("a, button, input")) openRow(row);
  });
  if (detail && wide.matches) {             // a reload keeps the open signal
    const id = new URLSearchParams(window.location.search).get("signal");
    const row = id && queueRows().find((r) => r.dataset.id === id);
    if (row) document.addEventListener("htmx:load", () => openRow(row), { once: true });
  }

  // ---- Keys: a approve, r reject, n needs data (the signal page and the split view's detail); j next, k previous
  // (the signal page's links, or the queue's selection, Enter opening it); ? lists them.
  const KEYS = { a: "btn-approve", r: "btn-reject", n: "btn-needs-data", j: "link-next", k: "link-prev" };
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || dialog.open || e.target.matches("input, textarea, select, [contenteditable]")) return;
    if (e.key === "?") { toast("Keys: j next, k previous, Enter opens, a approve, r reject, n needs data, Esc closes a popup."); return; }
    if (queue && (e.key === "j" || e.key === "k")) { e.preventDefault(); step(e.key === "j" ? 1 : -1); return; }
    if (queue && e.key === "Enter" && !e.target.closest("a, button") && selected()) { e.preventDefault(); openRow(selected()); return; }
    // in the queue a decision key waits until the detail of the selected row has arrived: never a stale signal
    if (queue && ["a", "r", "n"].includes(e.key) && !detailMatchesSelection()) return;
    const el = KEYS[e.key] && document.getElementById(KEYS[e.key]);
    if (el) el.click();
  });

  // ---- Live regions that stay quiet: a poll brings a hidden [data-live-for="id"] copy of the words, and the live region
  // with that id is rewritten only when they changed, so the same sentence is never announced twice.
  document.body.addEventListener("htmx:afterSettle", () => {
    document.querySelectorAll("[data-live-for]").forEach((src) => {
      const region = document.getElementById(src.dataset.liveFor);
      const words = src.textContent.trim();
      if (region && region.textContent.trim() !== words) region.textContent = words;
    });
  });

  // htmx does not swap 4xx responses by default; the start-sweep form answers 400/409 with the form and its error
  document.body.addEventListener("htmx:beforeSwap", (e) => {
    if (e.detail.xhr.status === 400 || e.detail.xhr.status === 409) { e.detail.shouldSwap = true; e.detail.isError = false; }
  });
  // a refused decision (r without a note) puts the cursor in the note, which the error now describes; a recorded one
  // moves focus to the decision's new state, so a keyboard user is not dropped to the top of the page by the swap
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (!e.detail.target || e.detail.target.id !== "decision") return;
    if (e.detail.xhr.status !== 400) {
      const state = e.detail.target.querySelector(".decision-state");
      if (state) state.focus();
      return;
    }
    const note = e.detail.target.querySelector("textarea[name=note]");
    if (note) note.focus();
  });
})();
