// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("erpnext.projects");

erpnext.projects.TaskController = class TaskController extends frappe.ui.form.Controller {
	setup() {
		this.frm.custom_make_buttons = {
			'Task': 'Create Child Task',
		};

		this.frm.make_methods = {
			'Timesheet': () => frappe.model.open_mapped_doc({
				method: 'erpnext.projects.doctype.task.task.make_timesheet',
				frm: this.frm
			})
		}

		this.setup_queries();
	}

	refresh() {
		erpnext.hide_company();
		this.setup_buttons();
	}

	setup_queries() {
		this.frm.set_query("task", "depends_on", () => {
			let filters = {
				name: ["!=", this.frm.doc.name]
			};
			if (this.frm.doc.project) {
				filters["project"] = this.frm.doc.project;
			}
			return {
				filters: filters
			};
		});

		this.frm.set_query("parent_task", () => {
			let filters = {};

			if (this.frm.doc.project) {
				filters.project = this.frm.doc.project
			} else if (this.frm.doc.issue) {
				filters.issue = this.frm.doc.issue
			}
			filters['is_group'] = 1;

			return {
				filters: filters
			};
		});
	}

	setup_buttons() {
		if (this.frm.doc.is_group) {
			this.frm.add_custom_button(__('Children Task List'), () => {
				frappe.set_route('List', 'Task', 'List', {parent_task: this.frm.doc.name});
			});
			this.frm.add_custom_button(__('Create Child Task'), () => {
				frappe.new_doc("Task", {
					parent_task: frm.doc.name,
					project: frm.doc.project,
					issue: frm.doc.issue,
				});
			});
		}
	}

	is_group() {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.check_if_child_exists",
			args: {
				name: this.frm.doc.name
			},
			callback: (r) => {
				if (r.message.length > 0) {
					frappe.msgprint(__(`Cannot convert it to non-group. The following child Tasks exist: ${r.message.join(", ")}.`));
					this.frm.reload_doc();
				}
			}
		})
	}
}

extend_cscript(cur_frm.cscript, new erpnext.projects.TaskController({ frm: cur_frm }));
