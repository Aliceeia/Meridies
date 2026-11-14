"""Regenerate the notebook from the master percent-format Python script."""
from pathlib import Path
import re
import nbformat

root = Path(__file__).resolve().parent
source = (root / "matterport_quickstart.py").read_text(encoding="utf-8")
cells = []
for raw in re.split(r"(?m)^# %%", source)[1:]:
    markdown = raw.startswith(" [markdown]")
    body = raw.split("\n", 1)[1].strip()
    if markdown:
        body = "\n".join(line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line for line in body.splitlines())
        cells.append(nbformat.v4.new_markdown_cell(body))
    else:
        compile(body, "matterport_quickstart.py", "exec")
        cells.append(nbformat.v4.new_code_cell(body))
book = nbformat.v4.new_notebook(cells=cells, metadata={"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}})
nbformat.validate(book)
nbformat.write(book, root / "matterport_quickstart.ipynb")
print(f"Generated and validated {len(cells)} notebook cells.")
