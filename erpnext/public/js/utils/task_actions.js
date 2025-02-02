frappe.provide("erpnext.task_actions");

$.extend(erpnext.task_actions, {
	create_service_template_tasks(project, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.create_service_template_tasks",
			args: {
				"project": project,
			},
			callback: (r) => {
				callback?.(r);
			}
		});
	},

	start_task(task, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.start_task",
			args: {
				task: task,
			},
			callback: (r) => {
				callback?.(r);
			}
		});
	},

	pause_task(task, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.pause_task",
			args: {
				task: task,
			},
			callback: (r) => {
				callback?.(r);
			},
		});
	},

	complete_task(task, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.complete_task",
			args: {
				task: task,
			},
			callback: (r) => {
				callback?.(r);
			},
		});
	},

	resume_task(task, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.resume_task",
			args: {
				task: task,
			},
			callback: (r) => {
				callback?.(r);
			},
		});
	},

	reopen_task(task, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.reopen_task",
			args: {
				task: task,
			},
			callback: (r) => {
				callback?.(r);
			},
		});
	},

	cancel_task(task, callback) {
		return new Promise((resolve, reject) => {
			frappe.confirm(__("Are you sure you want to cancel this task?"), () => {
				return frappe.call({
					method: "erpnext.projects.doctype.task.task.cancel_task",
					args: {
						"task": task,
					},
					callback: (r) => {
						callback?.(r);
						resolve();
					},
				});
			}, () => reject());
		})
	},

	async get_dialog_fields(doctype, name, task_data, project_data, fields) {
		if (doctype == "Project") {
			if (!project_data) {
				project_data = await frappe.model.with_doc("Project", name);
			}
			fields = fields.concat(this.get_dialog_project_fields(project_data));
		} else if (doctype == "Task") {
			if (!task_data) {
				task_data = await frappe.model.with_doc("Task", name) || {};
			}
			if (!project_data && task_data.project) {
				project_data = await frappe.model.with_doc("Project", task_data.project) || {};
			}
			project_data = project_data || {};

			fields = fields.concat(this.get_dialog_task_fields(task_data));
			fields = fields.concat(this.get_dialog_project_fields(project_data, true));
		}

		return fields;
	},

	get_dialog_task_fields(task_data) {
		task_data = task_data || {};

		let fields = [
			{
				fieldtype: "Section Break",
			},
			{
				label: __("Task"),
				fieldname: "task",
				fieldtype: "Link",
				options: "Task",
				default: task_data.name,
				read_only: 1,
				reqd: 1,
			},
			{
				label: __("Subject"),
				fieldname: "subject",
				fieldtype: "Data",
				default: task_data.subject,
				read_only: 1,
			},
			{
				label: __("Task Type"),
				fieldname: "task_type",
				fieldtype: "Data",
				default: task_data.task_type,
				read_only: 1,
			},
		];

		let additional_fields = erpnext.task_actions.get_dialog_task_additional_fields?.(task_data);
		additional_fields = additional_fields || [];
		fields = fields.concat(additional_fields);

		return fields;
	},

	get_dialog_project_fields(project_data, project_non_mandatory) {
		project_data = project_data || {};

		let fields = [
			{
				fieldtype: "Section Break",
			},
			{
				label: __("Project"),
				fieldname: "project",
				fieldtype: "Link",
				options: "Project",
				default: project_data.name,
				read_only: 1,
				reqd: cint(!project_non_mandatory),
			},
		];

		let additional_fields = erpnext.task_actions.get_dialog_project_additional_fields?.(project_data);
		additional_fields = additional_fields || [];
		fields = fields.concat(additional_fields);

		return fields
	}
});
