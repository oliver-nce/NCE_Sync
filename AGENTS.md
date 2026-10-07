<!--
Agent instructions to find NCE Sync code through the code index instead of reading whole files.

code-index:begin
{
 "purpose": "Agent instructions to find NCE Sync code through the code index instead of reading whole files.",
 "read_when": "When an AI agent needs to locate functions, routes, or tests before editing this repo.",
 "gotchas": [
  "Ask the code index and do not read whole files; open a file only to edit it."
 ],
 "keywords": ["agents", "code index", "elements.json", "find", "show", "callers", "code-index"],
 "format": 1
}
code-index:end
-->

# Finding code here: ask the code index, do not read files

`code_index/elements.json` maps every function, class, method, route, test, Vue component, CSS section and doc
section in this project, each with a one-line label. To find code for a question, ask it, then read only what
it prints. Open a whole file only to edit it.

```bash
TOOL="/Users/oliver3/Documents/_NCE_projects/_Project support files/code-index/codeindex.py"
python3 "$TOOL" --root . find WORDS...        # best-matching elements, with labels and line numbers
python3 "$TOOL" --root . show ID_OR_NAME      # just that element's source, what it calls, its tests
python3 "$TOOL" --root . callers ID_OR_NAME   # who calls it
```

Covers Python, JavaScript, TypeScript, Vue, PHP (WordPress hooks show as routes, e.g. `ACTION save_post`),
CSS and Markdown. It does not replace reading the code you are about to change; it tells you which code that is.

**Keeping it current.** After code changes run `python3 "$TOOL" --root . --write` (free). New or changed
functions get a one-line label from Claude Haiku (paid, small): see the tool's README
(`_Project support files/code-index/README.md`). A git pre-commit hook does both on commit.
