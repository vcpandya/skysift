"""Inject dossier_data.json into dossier_template.html -> dossier.html."""
from pathlib import Path
H = Path(__file__).parent
data = (H / "dossier_data.json").read_text(encoding="utf-8").replace("</", "<" + chr(92) + "/")
html = (H / "dossier_template.html").read_text(encoding="utf-8").replace("/*__DATA__*/", data, 1)
(H / "dossier.html").write_text(html, encoding="utf-8")
print("dossier.html", round(len(html.encode()) / 1e6, 2), "MB")
