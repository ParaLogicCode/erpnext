frappe.provide("erpnext.task_actions");

$.extend(erpnext.task_actions, {
	async create_project_task(project, project_data, callback) {
		let fields = await this.get_dialog_fields(
			"Project", project,
			null, project_data,
			erpnext.task_actions.get_create_task_fields(project_data),
		);

		let dialog = new frappe.ui.Dialog({
			title: __('Create Task'),
			fields: fields,
			primary_action: () => {
				let values = dialog.get_values();
				return frappe.call({
					method: "erpnext.projects.doctype.task.task.create_project_task",
					args: {
						subject: values.subject,
						project: values.project,
						task_type: values.task_type,
						expected_time: values.expected_time,
						description: values.description,
						additional_values: values,
					},
					callback: () => {
						dialog.hide();
						callback?.();
					},
				});
			},
			secondary_action: () => {
				frappe.model.with_doctype("Task", () => {
					let values = dialog.get_values(true);
					let doc = frappe.model.get_new_doc("Task");
					frappe.model.set_value(doc.doctype, doc.name, values);
					dialog.hide();
					frappe.set_route("Form", "Task", doc.name);
				});
			},
			primary_action_label: __('Create'),
			secondary_action_label: __('Edit Full Form'),
		});
		dialog.show();
	},

	create_service_template_tasks(project, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.create_service_template_tasks",
			args: {
				"project": project,
			},
			callback: (r) => {
				callback?.(r);
			},
			freeze: 1,
			freeze_message: __("Creating Tasks..."),
		});
	},

	async edit_task(task, task_data, project_data, callback) {
		let fields = await this.get_dialog_fields(
			"Project",
			task_data.project,
			task_data,
			project_data,
			erpnext.task_actions.get_create_task_fields(project_data, task_data),
			true
		)

		let dialog = new frappe.ui.Dialog({
			title: __('Edit Task'),
			fields: fields,
			primary_action: () => {
				let values = dialog.get_values();
				return frappe.call({
					method: "erpnext.projects.doctype.task.task.edit_task",
					args: {
						task: values.task,
						subject: values.subject,
						task_type: values.task_type,
						expected_time: flt(values.expected_time),
						description: values.description,
						additional_values: values,
					},
					callback: (r) => {
						dialog.hide();
						callback?.(r);
					}
				});
			},
			primary_action_label: __('Save')
		});
		dialog.show();
	},

	start_task(task, callback) {
		return frappe.call({
			method: "erpnext.projects.doctype.task.task.start_task",
			args: {
				task: task,
			},
			callback: (r) => {
				callback?.(r);
			},
			freeze: 1,
			freeze_message: __("Starting..."),
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
			freeze: 1,
			freeze_message: __("Pausing..."),
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
			freeze: 1,
			freeze_message: __("Completing..."),
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
			freeze: 1,
			freeze_message: __("Resuming..."),
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
			freeze: 1,
			freeze_message: __("Re-Opening..."),
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
					freeze: 1,
					freeze_message: __("Cancelling..."),
				});
			}, () => reject());
		})
	},

	async get_dialog_fields(doctype, name, task_data, project_data, fields, project_non_mandatory) {
		if (doctype == "Project") {
			if (!project_data) {
				project_data = await frappe.model.with_doc("Project", name);
			}
			fields = fields.concat(this.get_dialog_project_fields(project_data, project_non_mandatory));
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
	},

	get_create_task_fields(project_data, task_data) {
		project_data = project_data || {};
		task_data = task_data || {};

		let fields = [
			{
				"label": __("Subject"),
				"fieldname": "subject",
				"fieldtype": "Data",
				"default": task_data.subject,
				"reqd": 1,
			},
			{
				"label": __("Task Type"),
				"fieldname": "task_type",
				"fieldtype": "Link",
				"options": "Task Type",
				"default": task_data.task_type,
			},
			{
				"label": __("Expected Time (Hrs)"),
				"fieldname": "expected_time",
				"fieldtype": "Float",
				"default": flt(task_data.expected_time),
			},
		];

		let additional_fields = erpnext.task_actions.get_additional_create_task_fields?.(project_data, task_data);
		additional_fields = additional_fields || [];
		fields = fields.concat(additional_fields);

		fields = fields.concat([
			{
				"label": __("Description"),
				"fieldname": "description",
				"fieldtype": "Text Editor",
				"default": task_data.task_description || task_data.description,
				"max_height": "100px",
			},
		])

		if (task_data.name) {
			fields = fields.concat([
				{
					label: __("Task"),
					fieldname: "task",
					fieldtype: "Link",
					options: "Task",
					default: task_data.name,
					read_only: 1,
					reqd: 1,
				},
			])
		}

		return fields;
	},
});
