# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import now_datetime

VALID_SERVICES = [
	"WordPress", "WooCommerce", "Google Sheets", "Google Maps",
	"Authorize.net", "Stripe", "SendGrid", "Twilio", "Anthropic", "Klaviyo",
]
VALID_AUTH_TYPES = ["API Key", "Secret", "Basic Auth", "Bearer Token", "OAuth2", "None"]
VALID_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _safe_get_password(doc, fieldname):
	"""Read a Password field without raising if unset."""
	try:
		if not getattr(doc, fieldname, None):
			return None
		return doc.get_password(fieldname) or None
	except Exception:
		return None


class APIConnector(Document):
	def validate(self):
		if self.auth_type == "Secret" and not self.api_secret:
			frappe.throw("Secret is required for Secret auth type.")


@frappe.whitelist()
def get_credential(connector_name, fieldname):
	"""Return the decrypted value of a Password field for copying to clipboard."""
	allowed = {"api_key", "api_secret", "password", "bearer_token", "oauth_refresh_token"}
	if fieldname not in allowed:
		frappe.throw("Invalid credential field")

	doc = frappe.get_doc("API Connector", connector_name)
	doc.check_permission("read")

	value = _safe_get_password(doc, fieldname)
	return value or ""


@frappe.whitelist()
def test_connection(connector_name):
	"""Test an API Connector by sending a request to its base_url."""
	doc = frappe.get_doc("API Connector", connector_name)

	try:
		headers = {}
		auth = None

		if doc.auth_type == "API Key":
			api_key = _safe_get_password(doc, "api_key")
			if api_key:
				headers["X-API-Key"] = api_key

		elif doc.auth_type == "Secret":
			secret = _safe_get_password(doc, "api_secret")
			if not secret:
				frappe.throw("Secret is required.")

		elif doc.auth_type == "Basic Auth":
			username = doc.username or ""
			password = _safe_get_password(doc, "password") or ""
			auth = (username, password)

		elif doc.auth_type == "Bearer Token":
			token = _safe_get_password(doc, "bearer_token")
			if token:
				headers["Authorization"] = f"Bearer {token}"

		if doc.custom_headers:
			try:
				extra = json.loads(doc.custom_headers)
				headers.update(extra)
			except json.JSONDecodeError:
				pass

		timeout = doc.timeout_seconds or 30
		resp = requests.get(doc.base_url, headers=headers, auth=auth, timeout=timeout)

		success = resp.status_code < 400
		doc.db_set("last_tested", now_datetime())
		doc.db_set("last_test_result", "Success" if success else "Failed")
		doc.db_set("last_error", "" if success else f"HTTP {resp.status_code}: {resp.text[:500]}")

		return {"success": success, "status_code": resp.status_code}

	except Exception as e:
		doc.db_set("last_tested", now_datetime())
		doc.db_set("last_test_result", "Failed")
		doc.db_set("last_error", str(e)[:500])
		return {"success": False, "error": str(e)}


# ── Anthropic AI helpers ────────────────────────────────────────────────────

_CHAT_SYSTEM = (
	"You are an API documentation expert helping a user set up an API connector "
	"in their business application.\n\n"
	"Rules:\n"
	"1. Understand what API service the user needs.\n"
	"2. If the name is broad (e.g. AWS, Google, Microsoft), list the main "
	"service areas with a one-line description each and ask which one they need.\n"
	"3. Once a specific service is identified, briefly list the key endpoints "
	"available and confirm which ones the user wants.\n"
	"4. Keep responses concise — bullet points, short paragraphs.\n"
	"5. When you have enough information, tell the user you're ready and "
	'suggest they click **Generate Connector**.\n\n'
	"Do NOT produce JSON or code blocks. Have a natural conversation."
)

_GENERATE_SYSTEM = (
	"You are an API documentation expert. Based on the conversation that "
	"follows, generate a connector definition.\n\n"
	"Return ONLY a valid JSON object (no markdown fences, no commentary) "
	"matching this schema:\n\n"
	"{\n"
	'  "connector_name": "<service name>",\n'
	'  "service": "<one of: ' + ", ".join(VALID_SERVICES) + ', or Custom>",\n'
	'  "base_url": "<root API URL>",\n'
	'  "auth_type": "<one of: ' + ", ".join(VALID_AUTH_TYPES) + '>",\n'
	'  "timeout_seconds": 30,\n'
	'  "max_retries": 3,\n'
	'  "rate_limit_rpm": 0,\n'
	'  "notes": "<HTML credential-setup instructions>",\n'
	'  "implementation_guide": "<HTML overview of the service functionality, '
	'common patterns, and how to make use of it>",\n'
	'  "endpoints": [\n'
	"    {\n"
	'      "endpoint_name": "<Human-readable name>",\n'
	'      "endpoint_key": "<snake_case identifier>",\n'
	'      "http_method": "<GET|POST|PUT|PATCH|DELETE>",\n'
	'      "content_type": "application/json",\n'
	'      "path": "<path appended to base_url>",\n'
	'      "description": "<what it does>",\n'
	'      "documentation_url": "<docs URL>",\n'
	'      "sample_submission": {},\n'
	'      "sample_response": {},\n'
	'      "implementation_guide": "<usage notes, links to docs, example code>"\n'
	"    }\n"
	"  ]\n"
	"}\n\n"
	"Include only the endpoints the user asked for (or the most useful 5-10 "
	"if they did not specify). sample_submission and sample_response must be "
	"JSON objects or null. implementation_guide fields should include links "
	"to the relevant service documentation pages."
)


def _get_anthropic_key():
	"""Return the decrypted Anthropic API key or throw."""
	if not frappe.db.exists("API Connector", "Anthropic"):
		frappe.throw("No 'Anthropic' connector found. Create one with a valid API key first.")
	doc = frappe.get_doc("API Connector", "Anthropic")
	key = _safe_get_password(doc, "api_key")
	if not key:
		frappe.throw("The Anthropic connector has no API Key configured.")
	return key


def _call_anthropic(system, messages, max_tokens=1024, timeout=60):
	"""Send a request to the Anthropic Messages API and return the text."""
	key = _get_anthropic_key()
	resp = requests.post(
		"https://api.anthropic.com/v1/messages",
		headers={
			"x-api-key": key,
			"anthropic-version": "2023-06-01",
			"content-type": "application/json",
		},
		json={
			"model": "claude-sonnet-4-20250514",
			"max_tokens": max_tokens,
			"system": system,
			"messages": messages,
		},
		timeout=timeout,
	)
	if resp.status_code != 200:
		frappe.throw(f"Anthropic API error (HTTP {resp.status_code}): {resp.text[:500]}")
	return resp.json().get("content", [{}])[0].get("text", "")


def _parse_connector_json(text):
	"""Strip markdown fences and parse the JSON connector payload."""
	text = text.strip()
	if text.startswith("```"):
		text = "\n".join(text.split("\n")[1:])
		if text.rstrip().endswith("```"):
			text = text.rstrip()[:-3]
		text = text.strip()

	try:
		data = json.loads(text)
	except json.JSONDecodeError as e:
		frappe.throw(f"Failed to parse AI response as JSON: {e}\n\nRaw: {text[:1000]}")

	for ep in data.get("endpoints", []):
		for field in ("sample_submission", "sample_response"):
			val = ep.get(field)
			if val is not None and not isinstance(val, str):
				ep[field] = json.dumps(val, indent=2)
			elif val is None:
				ep[field] = ""
	return data


@frappe.whitelist()
def ai_discover_chat(messages):
	"""Handle one conversational turn for the AI Discover flow."""
	if isinstance(messages, str):
		messages = json.loads(messages)
	reply = _call_anthropic(_CHAT_SYSTEM, messages, max_tokens=1024, timeout=60)
	return {"reply": reply}


@frappe.whitelist()
def ai_discover_generate(messages):
	"""Generate the final connector JSON from conversation context."""
	if isinstance(messages, str):
		messages = json.loads(messages)
	gen_messages = messages + [
		{"role": "user", "content": "Generate the connector definition now."},
	]
	text = _call_anthropic(_GENERATE_SYSTEM, gen_messages, max_tokens=4096, timeout=90)
	return _parse_connector_json(text)


_GUIDE_SYSTEM = (
	"You are an API documentation expert. Generate implementation guides "
	"for the API connector described below.\n\n"
	"Return ONLY a valid JSON object (no markdown fences, no commentary) with:\n\n"
	"{\n"
	'  "connector_guide": "<HTML string: service overview, main capabilities, '
	"common integration patterns, authentication notes, rate-limiting / "
	'best practices, links to official docs>",\n'
	'  "endpoint_guides": {\n'
	'    "<endpoint_key>": "<text: what the endpoint does in detail, '
	"required/optional parameters, link to specific docs page, "
	'common usage examples, error handling tips>"\n'
	"  }\n"
	"}\n\n"
	"connector_guide must be HTML (use <h4>, <p>, <ul>, <ol>, <code>, <a>). "
	"endpoint_guides values are plain text with URLs."
)


@frappe.whitelist()
def ai_generate_guide(connector_name):
	"""Generate implementation guides for a connector and its endpoints."""
	doc = frappe.get_doc("API Connector", connector_name)

	endpoints_info = []
	for ep in doc.endpoints:
		endpoints_info.append({
			"name": ep.endpoint_name,
			"key": ep.endpoint_key,
			"method": ep.http_method,
			"path": ep.path,
			"description": ep.description or "",
			"documentation_url": ep.documentation_url or "",
		})

	prompt = (
		f"Service: {doc.connector_name}\n"
		f"Type: {doc.service}\n"
		f"Base URL: {doc.base_url}\n"
		f"Auth Type: {doc.auth_type}\n\n"
		f"Endpoints:\n{json.dumps(endpoints_info, indent=2)}\n\n"
		f"Generate the implementation guides now."
	)

	text = _call_anthropic(
		_GUIDE_SYSTEM,
		[{"role": "user", "content": prompt}],
		max_tokens=4096,
		timeout=90,
	)

	text = text.strip()
	if text.startswith("```"):
		text = "\n".join(text.split("\n")[1:])
		if text.rstrip().endswith("```"):
			text = text.rstrip()[:-3]
		text = text.strip()

	try:
		data = json.loads(text)
	except json.JSONDecodeError as e:
		frappe.throw(f"Failed to parse AI response: {e}\n\nRaw: {text[:1000]}")

	doc.implementation_guide = data.get("connector_guide", "")

	endpoint_guides = data.get("endpoint_guides", {})
	filled = 0
	for ep in doc.endpoints:
		guide = endpoint_guides.get(ep.endpoint_key, "")
		if guide:
			ep.implementation_guide = guide
			filled += 1

	doc.save()

	return {
		"connector_guide": doc.implementation_guide,
		"endpoint_count": filled,
	}


@frappe.whitelist()
def create_connector_from_ai(connector_data):
	"""Create an API Connector document from AI-discovered data."""
	if isinstance(connector_data, str):
		connector_data = json.loads(connector_data)

	name = connector_data.get("connector_name", "").strip()
	if not name:
		frappe.throw("Connector name is required.")
	if frappe.db.exists("API Connector", name):
		frappe.throw(f"A connector named '{name}' already exists.")

	doc = frappe.new_doc("API Connector")
	doc.connector_name = name
	doc.service = connector_data.get("service", "Custom")
	doc.base_url = connector_data.get("base_url", "")
	doc.auth_type = connector_data.get("auth_type", "API Key")
	doc.timeout_seconds = connector_data.get("timeout_seconds", 30)
	doc.max_retries = connector_data.get("max_retries", 3)
	doc.rate_limit_rpm = connector_data.get("rate_limit_rpm", 0)
	doc.notes = connector_data.get("notes", "")
	doc.implementation_guide = connector_data.get("implementation_guide", "")
	doc.status = "Inactive"

	for ep in connector_data.get("endpoints", []):
		doc.append("endpoints", {
			"endpoint_name": ep.get("endpoint_name", ""),
			"endpoint_key": ep.get("endpoint_key", ""),
			"http_method": ep.get("http_method", "GET"),
			"content_type": ep.get("content_type", "application/json"),
			"path": ep.get("path", ""),
			"description": ep.get("description", ""),
			"documentation_url": ep.get("documentation_url", ""),
			"sample_submission": ep.get("sample_submission", ""),
			"sample_response": ep.get("sample_response", ""),
			"implementation_guide": ep.get("implementation_guide", ""),
		})

	doc.insert()
	return {"name": doc.name, "endpoint_count": len(doc.endpoints)}
