# Copyright (c) 2026, Oliver Reid and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

ROLE_PREFIX = "NCE "


class NCEAccessProfile(Document):
	def validate(self):
		self._ensure_role()
		self._sync_wp_table_rows()

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

		Role-level settings (Home Page, Desk Access, Two Factor
		Authentication, Disabled) work the same way: this profile
		is the single place to edit them. The first time a profile is saved
		against a Role that already existed, its current settings are
		pulled in rather than overwritten with blanks -- from then on,
		whatever is set on this form is pushed onto the Role on every save.
		"""
		role_name = f"{ROLE_PREFIX}{(self.profile_name or '').strip()}".strip()
		if not self.profile_name:
			return

		role_existed = frappe.db.exists("Role", role_name)
		if not role_existed:
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role_name,
					"desk_access": 1,
				}
			).insert(ignore_permissions=True)
		elif self.is_new() or not self.role:
			self._pull_role_fields(role_name)

		self.role = role_name
		self._push_role_fields(role_name)

	def _pull_role_fields(self, role_name):
		role_vals = frappe.db.get_value(
			"Role",
			role_name,
			["home_page", "desk_access", "two_factor_auth", "disabled"],
			as_dict=True,
		)
		if not role_vals:
			return
		self.home_page = role_vals.home_page
		self.desk_access = role_vals.desk_access
		self.two_factor_auth = role_vals.two_factor_auth
		self.disabled = role_vals.disabled

	def _push_role_fields(self, role_name):
		frappe.db.set_value(
			"Role",
			role_name,
			{
				"home_page": self.home_page,
				"desk_access": 1 if self.desk_access else 0,
				"two_factor_auth": 1 if self.two_factor_auth else 0,
				"disabled": 1 if self.disabled else 0,
			},
		)

	def _sync_wp_table_rows(self):
		"""Make sure every WP Tables-registered DocType has a Table Access
		row on this profile, defaulting to no access (Read/Write both off),
		so a newly mirrored WP table shows up here automatically instead of
		silently being missing. Rows that already exist -- WP-table ones or
		anything you've added by hand for other DocTypes -- are left
		exactly as they are; this only ever adds missing rows, never
		removes or changes existing ones.
		"""
		existing = {row.document_type for row in self.table_access if row.document_type}
		for doctype_name in _wp_table_doctypes():
			if doctype_name not in existing:
				self.append(
					"table_access",
					{
						"document_type": doctype_name,
						"read": 0,
						"write": 0,
					},
				)

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


def _wp_table_doctypes():
	"""Every distinct Frappe DocType name registered in WP Tables (skips rows
	where the mirrored doctype hasn't been set up yet)."""
	rows = frappe.get_all(
		"WP Tables",
		filters={"frappe_doctype": ["is", "set"]},
		fields=["frappe_doctype"],
		distinct=True,
	)
	return [r.frappe_doctype for r in rows if r.frappe_doctype]


def sync_new_wp_table_to_profiles(doc, method=None):
	"""doc_event hook -- WP Tables after_insert (see hooks.py). Pushes a new,
	no-access Table Access row for this WP table's frappe_doctype onto every
	existing NCE Access Profile, so profiles pick up newly mirrored tables
	without needing to be manually reopened and re-saved.
	"""
	frappe_doctype = doc.get("frappe_doctype")
	if not frappe_doctype:
		return
	for profile_name in frappe.get_all("NCE Access Profile", pluck="name"):
		profile = frappe.get_doc("NCE Access Profile", profile_name)
		if any(row.document_type == frappe_doctype for row in profile.table_access):
			continue
		profile.append(
			"table_access",
			{
				"document_type": frappe_doctype,
				"read": 0,
				"write": 0,
			},
		)
		profile.flags.ignore_permissions = True
		profile.save()


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
