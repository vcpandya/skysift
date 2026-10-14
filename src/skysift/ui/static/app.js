document.addEventListener("click", (e) => {
  const row = e.target.closest("tr.row-link[data-href]");
  if (row && !e.target.closest("a, button, input")) window.location = row.dataset.href;
});
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  const act = (id) => { const el = document.getElementById(id); if (el) el.click(); };
  if (e.key === "a") act("btn-approve");
  if (e.key === "r") act("btn-reject");
  if (e.key === "n") act("btn-needs-data");
  if (e.key === "j") act("link-next");
  if (e.key === "k") act("link-prev");
});
