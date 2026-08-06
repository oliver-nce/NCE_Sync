#!/usr/bin/env python3
"""Generate ``nce_sync/CODE_INDEX.json`` deterministically from the source tree.

Adapted from NCE_Events/scripts/build_code_index.py for the NCE Sync (Tables) app.

Run modes
---------
- ``python scripts/build_code_index.py --write`` (default): regenerate
  ``nce_sync/CODE_INDEX.json`` and write it to disk.
- ``python scripts/build_code_index.py --check``: regenerate in memory and exit
  1 (printing a unified diff) if it differs from what is on disk.

Human-curated fields (``purpose``, ``sections``, ``depends_on``, etc.) are merged
from ``nce_sync/CODE_INDEX.manual.json``.

Stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import difflib
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = REPO_ROOT / "nce_sync" / "CODE_INDEX.json"
MANUAL_PATH = REPO_ROOT / "nce_sync" / "CODE_INDEX.manual.json"

# Map a file path (POSIX, repo-root-relative) → top-level group key in the index.
# First match wins.
GROUP_RULES: list[tuple[re.Pattern, str]] = [
	(re.compile(r"^nce_sync/api\.py$"), "backend"),
	(re.compile(r"^nce_sync/hooks\.py$"), "backend"),
	(re.compile(r"^nce_sync/overrides\.py$"), "backend"),
	(re.compile(r"^nce_sync/__init__\.py$"), "backend"),
	(re.compile(r"^nce_sync/utils/.*\.py$"), "utils"),
	(re.compile(r"^nce_sync/tests/.*\.py$"), "tests"),
	(re.compile(r"^nce_sync/public/js/.*\.js$"), "frontend"),
]

# Folders excluded entirely from scanning.
EXCLUDE_DIR_NAMES = {
	"__pycache__",
	"node_modules",
	".git",
}

# DocType folder root.
DOCTYPE_ROOT = "nce_sync/nce_sync/doctype"

# Page folder root.
PAGE_ROOT = "nce_sync/nce_sync/page"

# JS regexes for top-level ``export`` and ``frappe.provide``.
JS_EXPORT_RE = re.compile(
	r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:const|let|var|function|class)\s+(\w+)",
	re.MULTILINE,
)
JS_EXPORT_NAMED_RE = re.compile(r"^\s*export\s*\{([^}]+)\}", re.MULTILINE)
FRAPPE_PROVIDE_RE = re.compile(r"""frappe\.provide\(\s*['"]([^'"]+)['"]\s*\)""")

# ---------------------------------------------------------------------------
# Per-file extractors
# ---------------------------------------------------------------------------


def extract_python(path: Path) -> dict:
	"""Return ``{exports, whitelist_endpoints}`` for a Python module."""
	try:
		tree = ast.parse(path.read_text(encoding="utf-8"))
	except SyntaxError as exc:
		raise SystemExit(f"SyntaxError parsing {path}: {exc}")

	exports: list[str] = []
	whitelist_endpoints: list[str] = []
	for node in tree.body:
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			if not node.name.startswith("_"):
				exports.append(node.name)
			for deco in getattr(node, "decorator_list", []):
				if _is_frappe_whitelist(deco):
					whitelist_endpoints.append(node.name)
					break
	out: dict = {}
	if exports:
		out["exports"] = sorted(exports)
	if whitelist_endpoints:
		out["whitelist_endpoints"] = sorted(whitelist_endpoints)
	return out


def _is_frappe_whitelist(deco: ast.AST) -> bool:
	"""True if the decorator AST node is ``@frappe.whitelist`` or ``@frappe.whitelist(...)``."""
	if isinstance(deco, ast.Call):
		deco = deco.func
	if isinstance(deco, ast.Attribute) and deco.attr == "whitelist":
		if isinstance(deco.value, ast.Name) and deco.value.id == "frappe":
			return True
	return False


def extract_js(path: Path) -> dict:
	"""Return ``{exports, provides}`` for a ``.js`` file."""
	text = path.read_text(encoding="utf-8")
	return extract_js_string(text)


def extract_js_string(text: str) -> dict:
	exports: set[str] = set()
	for m in JS_EXPORT_RE.finditer(text):
		exports.add(m.group(1))
	for m in JS_EXPORT_NAMED_RE.finditer(text):
		for raw in m.group(1).split(","):
			name = raw.strip().split(" as ")[-1].strip()
			if name and re.match(r"^\w+$", name):
				exports.add(name)
	provides = sorted({m.group(1) for m in FRAPPE_PROVIDE_RE.finditer(text)})
	out: dict = {}
	if exports:
		out["exports"] = sorted(exports)
	if provides:
		out["provides"] = provides
	return out


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def group_for(rel_path: str) -> str | None:
	"""Return the top-level group key for ``rel_path`` or None to skip."""
	for pat, group in GROUP_RULES:
		if pat.match(rel_path):
			return group
	return None


def discover_files() -> list[str]:
	"""Walk the repo and return sorted POSIX paths of in-scope files."""
	found: list[str] = []
	for path in REPO_ROOT.rglob("*"):
		if not path.is_file():
			continue
		if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
			continue
		rel = path.relative_to(REPO_ROOT).as_posix()
		if group_for(rel) is not None:
			found.append(rel)
	return sorted(found)


def discover_doctype_folders() -> list[str]:
	"""Return sorted POSIX paths of doctype folders (with trailing slash)."""
	return _discover_subfolders(DOCTYPE_ROOT)


def discover_page_folders() -> list[str]:
	"""Return sorted POSIX paths of page folders (with trailing slash)."""
	return _discover_subfolders(PAGE_ROOT)


def _discover_subfolders(root_rel: str) -> list[str]:
	root = REPO_ROOT / root_rel
	if not root.is_dir():
		return []
	folders: list[str] = []
	for child in root.iterdir():
		if not child.is_dir():
			continue
		if child.name in EXCLUDE_DIR_NAMES:
			continue
		rel = child.relative_to(REPO_ROOT).as_posix() + "/"
		folders.append(rel)
	return sorted(folders)


# ---------------------------------------------------------------------------
# Build the index
# ---------------------------------------------------------------------------


def build_index(manual: dict) -> dict:
	"""Construct the index dict in canonical order."""
	today = _dt.date.today().isoformat()

	out: dict = {}

	meta_manual = manual.get("_meta", {})
	out["_meta"] = {
		"description": meta_manual.get(
			"description",
			"Machine-readable index of every module in the NCE Sync (Tables) Frappe app.",
		),
		"generated": today,
		"generator": "scripts/build_code_index.py",
		"usage": meta_manual.get(
			"usage",
			"Run `python scripts/build_code_index.py --write` to regenerate after code changes.",
		),
	}

	out["architecture"] = manual.get("architecture", {})

	overrides: dict = manual.get("module_overrides", {})
	used_overrides: set[str] = set()

	buckets: dict[str, dict] = {
		"backend": {},
		"utils": {},
		"tests": {},
		"frontend": {},
		"doctypes": {},
		"pages": {},
	}

	for folder in discover_doctype_folders():
		entry: dict = {}
		if folder in overrides:
			entry.update(overrides[folder])
			used_overrides.add(folder)
		else:
			entry["purpose"] = "TODO: describe in CODE_INDEX.manual.json"
		buckets["doctypes"][folder] = entry

	for folder in discover_page_folders():
		entry: dict = {}
		if folder in overrides:
			entry.update(overrides[folder])
			used_overrides.add(folder)
		else:
			entry["purpose"] = "TODO: describe in CODE_INDEX.manual.json"
		buckets["pages"][folder] = entry

	for rel in discover_files():
		group = group_for(rel)
		if group is None:
			continue
		entry: dict = {}
		path = REPO_ROOT / rel
		suffix = path.suffix
		if suffix == ".py":
			entry.update(extract_python(path))
		elif suffix == ".js":
			entry.update(extract_js(path))
		if rel in overrides:
			entry.update(overrides[rel])
			used_overrides.add(rel)
		else:
			entry.setdefault("purpose", "TODO: describe in CODE_INDEX.manual.json")
		buckets[group][rel] = entry

	stale = sorted(set(overrides) - used_overrides)
	if stale:
		raise SystemExit(
			"CODE_INDEX.manual.json references paths not on disk:\n  "
			+ "\n  ".join(stale)
			+ "\n\nRemove or update these entries, then re-run."
		)

	for key, bucket in buckets.items():
		if not bucket:
			continue
		out[key] = {k: bucket[k] for k in sorted(bucket)}

	return out


# ---------------------------------------------------------------------------
# Serialise + diff
# ---------------------------------------------------------------------------


def serialise(index: dict) -> str:
	return json.dumps(index, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
	parser.add_argument(
		"--check",
		action="store_true",
		help="Do not write; exit 1 if regenerating would change CODE_INDEX.json.",
	)
	parser.add_argument(
		"--write",
		action="store_true",
		help="Write the regenerated index to disk (default behaviour).",
	)
	args = parser.parse_args()

	if not MANUAL_PATH.exists():
		raise SystemExit(f"Missing {MANUAL_PATH}. Create it with at least {{\"_meta\": {{}}}}.")

	manual = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
	index = build_index(manual)
	text = serialise(index)

	if args.check:
		current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
		if current == text:
			return 0
		diff = difflib.unified_diff(
			current.splitlines(keepends=True),
			text.splitlines(keepends=True),
			fromfile="CODE_INDEX.json (on disk)",
			tofile="CODE_INDEX.json (regenerated)",
		)
		sys.stdout.writelines(diff)
		print("\nCODE_INDEX.json is out of date. Run `python scripts/build_code_index.py --write` and commit.")
		return 1

	INDEX_PATH.write_text(text, encoding="utf-8")
	print(f"wrote {INDEX_PATH}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
