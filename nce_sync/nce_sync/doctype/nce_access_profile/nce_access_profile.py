# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

ROLE_PREFIX = "NCE "


class NCEAccessProfile(Document):
	def validate(self):
		self._ensure_role()

	def on_update(self):
		self.apply_table_access()

	def _ensure_role(self):
		"""Create (or link) the Role this profile manages. One Role per
		profile, named 'NCE {Profile Name}'. Nothing else in the app should
		hand-edit this Role's permissions directly in Role Permissions
		Manager -- use this profile's Table Access grid + Apply Access
		instead, since apply_table_access() treats this profile as the sole
		source of truth for the Role's permissions and will remove anything
		it doesn't recognise.
		"""
		role_name = f"{ROLE_PREFIX}{(self.profile_name or '').strip()}".strip()
		if not self.profile_name:
			return
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role_name,
					"desk_access": 1,
				}
			).insert(ignore_permissions=True)
		self.role = role_name

	def apply_table_access(self):
		"""Sync this profile's Table Access rows into real DocType
		permissions (Custom DocPerm, permlevel 0) for the linked Role, and
		remove permission on any DocType no longer listed. This is what
		actually enforces "no access to anything else" -- it's not just a
		suggestion, rows missing from the grid get their permission deleted.
		"""
		if not self.role:
			return

		wanted = {}
		for row in self.table_access:
			if not row.document_type:
				continue
			wanted[row.document_type] = {
				"read": 1 if (row.read or row.write) else 0,
				"write": 1 if row.write else 0,
			}

		existing = frappe.get_all(
			"Custom DocPerm",
			filters={"role": self.role, "permlevel": 0},
			fields=["name", "parent", "read", "write"],
		)
		existing_by_doctype = {e.parent: e for e in existing}

		# Remove permission on anything no longer in the list.
		for doctype_name, perm in existing_by_doctype.items():
			if doctype_name not in wanted:
				frappe.delete_doc(
					"Custom DocPerm", perm.name, ignore_permissions=True, force=True
				)

		# Add or update everything that should be there.
		for doctype_name, flags in wanted.items():
			perm = existing_by_doctype.get(doctype_name)
			if perm:
				if perm.read != flags["read"] or perm.write != flags["write"]:
					frappe.db.set_value(
						"Custom DocPerm",
						perm.name,
						{"read": flags["read"], "write": flags["write"]},
					)
			else:
				frappe.get_doc(
					{
						"doctype": "Custom DocPerm",
						"parent": doctype_name,
						"parenttype": "DocType",
						"parentfield": "permissions",
						"role": self.role,
						"permlevel": 0,
						"read": flags["read"],
						"write": flags["write"],
					}
				).insert(ignore_permissions=True)

		frappe.clear_cache()


@frappe.whitelist()
def apply_access(name):
	frappe.only_for("System Manager")
	doc = frappe.get_doc("NCE Access Profile", name)
	doc.apply_table_access()
	return {"ok": True}


@frappe.whitelist()
def get_profile_users(name):
	frappe.only_for("System Manager")
	role = frappe.db.get_value("NCE Access Profile", name, "role")
	if not role:
		return []
	rows = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, fields=["parent"]
	)
	users = []
	for r in rows:
		u = frappe.db.get_value(
			"User", r.parent, ["full_name", "email", "enabled"], as_dict=True
		)
		if u:
			u["user"] = r.parent
			users.append(u)
	users.sort(key=lambda u: (not u["enabled"], u["full_name"] or ""))
	return users


@frappe.whitelist()
def add_user_to_profile(name, user):
	frappe.only_for("System Manager")
	role = frappe.db.get_value("NCE Access Profile", name, "role")
	if not role:
		frappe.throw(frappe._("This profile has no linked Role yet — save it first."))
	user_doc = frappe.get_doc("User", user)
	if not any(r.role == role for r in user_doc.roles):
		user_doc.append("roles", {"role": role})
		user_doc.flags.ignore_permissions = True
		user_doc.save()
	return {"ok": True}


@frappe.whitelist()
def remove_user_from_profile(name, user):
	frappe.only_for("System Manager")
	role = frappe.db.get_value("NCE Access Profile", name, "role")
	if not role:
		return {"ok": True}
	user_doc = frappe.get_doc("User", user)
	user_doc.roles = [r for r in user_doc.roles if r.role != role]
	user_doc.flags.ignore_permissions = True
	user_doc.save()

	remaining = [r.role for r in user_doc.roles if r.role not in ("All", "Guest")]
	disabled = False
	if not remaining and user_doc.enabled:
		frappe.db.set_value("User", user, "enabled", 0)
		disabled = True
	return {"ok": True, "disabled": disabled}


@frappe.whitelist()
def invite_user_to_profile(name, email, full_name):
	frappe.only_for("System Manager")
	role = frappe.db.get_value("NCE Access Profile", name, "role")
	if not role:
		frappe.throw(frappe._("This profile has no linked Role yet — save it first."))

	if frappe.db.exists("User", email):
		user_doc = frappe.get_doc("User", email)
		if not any(r.role == role for r in user_doc.roles):
			user_doc.append("roles", {"role": role})
		user_doc.enabled = 1
		user_doc.flags.ignore_permissions = True
		user_doc.save()
		return {"ok": True, "created": False}

	user_doc = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"full_name": full_name,
			"send_welcome_email": 1,
			"user_type": "System User",
			"roles": [{"role": role}],
		}
	)
	user_doc.flags.ignore_permissions = True
	user_doc.insert()
	return {"ok": True, "created": True}
