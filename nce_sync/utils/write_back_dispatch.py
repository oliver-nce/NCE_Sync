# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Write-back dispatcher for WP Tables.

Routes write-back operations to either the default SQL push or registered named handlers
based on the child table steps defined in WP Table Write Back Step.

Main function: run_write_back_for_doc(wp_table_name, doctype, docname, method)
"""

import json

import frappe


def load_wp_table_doc(wp_table_name):
	"""
	Load WP Tables document. Result is not cached (may change between enqueues).
	"""
	return frappe.get_doc("WP Tables", wp_table_name)


def get_effective_steps(wp_table_doc):
	"""
	Return list of enabled write-back steps for this table.

	If child table has no enabled steps AND write_back_mode == SQL Direct,
	return one implicit Default SQL Push step (run_on=Both) for backward compatibility.

	Steps are sorted by idx.
	"""
	steps = []
	for step in wp_table_doc.write_back_steps or []:
		if not step.enabled:
			continue
		steps.append(step)

	if not steps and wp_table_doc.write_back_mode == "SQL Direct":
		steps = [
			frappe._dict(
				{
					"step_name": "Default SQL Push",
					"enabled": 1,
					"action_kind": "Default SQL Push",
					"handler_name": None,
					"config_json": None,
					"run_on": "Both",
					"stop_on_error": 1,
					"idx": 1,
				}
			)
		]

	steps.sort(key=lambda x: x.idx or 0)
	return steps


def _filter_steps_by_write_back_mode(steps, write_back_mode):
	"""
	SQL Direct: only Default SQL Push steps.
	API Required: only Named Handler steps.
	"""
	if write_back_mode == "SQL Direct":
		return [s for s in steps if s.action_kind == "Default SQL Push"]
	if write_back_mode == "API Required":
		return [s for s in steps if s.action_kind == "Named Handler"]
	return steps


def _run_sql_direct_push(wp_table_name, doctype, docname):
	"""
	Call the existing SQL push implementation (lazy import avoids circular import with live_sync).
	"""
	from nce_sync.utils.live_sync import push_record_to_wp

	push_record_to_wp(wp_table_name, doctype, docname)


def _run_named_handler(frappe_doc, wp_table_doc, method, step):
	"""
	Run a named handler registered in the registry.

	Loads handler by name, validates it exists, parses config_json,
	and calls the handler.
	"""
	handler_name = step.handler_name
	if not handler_name:
		frappe.log_error(
			title=f"Write-back skip: handler_name missing for step on {frappe_doc.doctype}:{frappe_doc.name}",
			message=f"Step: {step.as_dict() if hasattr(step, 'as_dict') else step}",
		)
		return

	from nce_sync.utils.write_back_handlers import HANDLERS

	handler_fn = HANDLERS.get(handler_name)
	if handler_fn is None:
		frappe.log_error(
			title=f"Write-back handler not found: {handler_name}",
			message=f"Registered handlers: {list(HANDLERS.keys())}",
		)
		raise ValueError(f"Handler '{handler_name}' not registered")

	config = {}
	if step.config_json:
		if isinstance(step.config_json, str):
			config = json.loads(step.config_json)
		else:
			config = step.config_json

	handler_fn(frappe_doc, wp_table_doc, method, config)


def _get_method_from_doc_event(frappe_doc, method):
	"""
	Map Frappe doc event to Run On value.
	"""
	if method == "after_insert":
		return "Insert"
	if method in ("on_update", "validate"):
		return "Update"
	return None


def run_write_back_for_doc(wp_table_name, doctype, docname, method):
	"""
	Background job: run write-back steps for one Frappe document.

	Respects:
	- listen_for_changes flag (must be 1)
	- write_back_mode (Never / SQL Direct / API Required)
	- step.enabled
	- step.run_on (Insert / Update / Both) matched against event

	Steps run in idx order; stop_on_error skips subsequent steps on failure.
	"""
	try:
		frappe_doc = frappe.get_doc(doctype, docname)
	except frappe.DoesNotExistError:
		frappe.log_error(
			title="Write-back skip: document not found",
			message="{}:{}".format(doctype, docname),
		)
		return

	wp_table_doc = load_wp_table_doc(wp_table_name)

	if not wp_table_doc.listen_for_changes:
		return

	if wp_table_doc.write_back_mode == "Never":
		return

	steps = get_effective_steps(wp_table_doc)
	steps = _filter_steps_by_write_back_mode(steps, wp_table_doc.write_back_mode)
	if not steps:
		return

	run_on_event = _get_method_from_doc_event(frappe_doc, method)
	if run_on_event is None:
		run_on_event = "Update"

	for step in steps:
		if not step.enabled:
			continue

		step_run_on = step.run_on or "Both"
		if step_run_on != "Both" and step_run_on != run_on_event:
			continue

		try:
			if step.action_kind == "Default SQL Push":
				_run_sql_direct_push(wp_table_name, doctype, docname)
			elif step.action_kind == "Named Handler":
				_run_named_handler(frappe_doc, wp_table_doc, method, step)
		except Exception as e:
			log_message = "Step: {} (handler: {})\nError: {}".format(
				step.step_name or "unnamed", step.handler_name or "N/A", str(e)
			)
			frappe.log_error(
				title="WP Write-back step error: {} on {}:{}".format(wp_table_doc.name, doctype, docname),
				message=log_message,
			)
			if step.stop_on_error:
				break
