# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

"""
Write-back handler registry for named handlers.

Handlers are registered with a unique name and must match the handler_name
field in WP Table Write Back Step child table rows.

Signature for all handlers:
    handler(frappe_doc, wp_table_doc, method, config) -> None

Where:
- frappe_doc: The Frappe document that triggered the write-back
- wp_table_doc: The WP Tables configuration document
- method: "after_insert" or "on_update" (the Frappe doc event)
- config: The parsed config_json dict from the step configuration
"""

import frappe

HANDLERS = {}


def register_handler(name):
	"""
	Decorator to register a write-back handler.

	Usage:
	    @register_handler("my_handler_name")
	    def my_handler(frappe_doc, wp_table_doc, method, config):
	        # Your logic here
	        pass
	"""

	def decorator(fn):
		HANDLERS[name] = fn
		return fn

	return decorator


def get_handler(name):
	"""
	Get a registered handler by name, or None if not found.
	"""
	return HANDLERS.get(name)


def get_all_handler_names():
	"""
	Return list of all registered handler names (for validation).
	"""
	return list(HANDLERS.keys())


# Eager-import built-in handlers so @register_handler side effects run at startup.
import nce_sync.utils.write_back_handlers.woo_placeholder  # noqa: E402, F401
