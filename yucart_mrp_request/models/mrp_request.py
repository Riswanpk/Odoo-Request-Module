from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)


class MrpRequest(models.Model):
    _name = 'mrp.request'
    _description = 'Manufacturing Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # === Fields ===
    name = fields.Char(
        string='Request Number',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New')
    )
    yu_order_id = fields.Char(string='External Order Reference')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    qty = fields.Float(string='Quantity', required=True, default=1.0)
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unit of Measure',
        related='product_id.uom_id',
        readonly=True
    )
    start_date = fields.Datetime(string='Start Date')
    product_owner_ids = fields.Many2many(
        'res.users',
        compute='_compute_product_owner_ids',
        string='Allowed Product Owners'
    )
    admin_ids = fields.Many2many(
        'res.users',
        compute='_compute_admin_ids',
        string='Allowed Production Managers'
    )

    product_owner_id = fields.Many2one(
        'res.users',
        string='Product Owner',
        domain="[('id', 'in', product_owner_ids)]"
    )
    admin_id = fields.Many2one(
        'res.users',
        string='Production Manager',
        domain="[('id', 'in', admin_ids)]"
    )

    bom_exists = fields.Boolean(
        string='BOM Exists',
        compute='_compute_bom_exists',
        store=True
    )
    state = fields.Selection([
        ('new', 'New'),
        ('pending_po', 'Pending Product Owner'),
        ('change_requested', 'Change Requested'),
        ('waiting_admin', 'Waiting Admin'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='new', tracking=True)
    mrp_production_id = fields.Many2one('mrp.production', string='Manufacturing Order')
    note = fields.Text(string="Notes")
    bom_id = fields.Many2one(
        'mrp.bom',
        string='Bill of Materials',
        domain="[('product_tmpl_id', '=', product_tmpl_id)]"
    )
    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Product Template',
        related='product_id.product_tmpl_id',
        store=True,
        readonly=True,
    )
    notified_po = fields.Boolean(string="Product Owner Notified", default=False)
    start_date = fields.Datetime(string='Start Date')
    requested_date = fields.Datetime(
        string='Requested Date',
        required=True,
        default=fields.Datetime.now,
        tracking=True
    )
    expected_delivery_date = fields.Datetime(
        string='Expected Delivery Date',
        required=False,
        tracking=True
    )
    auto_submitted_po = fields.Boolean(string="Auto Submitted to Product Owner", default=False)

    # === Sequence Generation ===
    @api.model
    def create(self, vals):
        # Handle batch create (list of dicts)
        if isinstance(vals, list):
            for v in vals:
                # Generate custom request number
                if not v.get('name') or v['name'] == _('New'):
                    today = datetime.now()
                    date_str = today.strftime('%d%m%y')
                    count = self.search_count([
                        ('create_date', '>=', today.replace(hour=0, minute=0, second=0, microsecond=0)),
                        ('create_date', '<', today.replace(hour=23, minute=59, second=59, microsecond=999999))
                    ]) + 1
                    seq = str(count).zfill(3)
                    v['name'] = f"RQ{date_str}{seq}"

                # Auto-select BOM if exists
                if v.get('product_id'):
                    bom = self.env['mrp.bom'].search(
                        [('product_tmpl_id', '=', self.env['product.product'].browse(v['product_id']).product_tmpl_id.id)],
                        order='write_date desc, id desc',
                        limit=1
                    )
                    if bom:
                        v['bom_id'] = bom.id
                        # Autofill from BOM if present
                        if bom.product_uom_id:
                            v['uom_id'] = bom.product_uom_id.id
                        if bom.product_owner_id:
                            v['product_owner_id'] = bom.product_owner_id.id
                        if bom.admin_id:
                            v['admin_id'] = bom.admin_id.id

            records = super().create(vals)
            for rec in records:
                if not rec.bom_exists:
                    self._assign_design_team(rec)
                # Set MO name if created at submit to PO
                if rec.mrp_production_id:
                    rec.mrp_production_id.name = f"MO{rec.name[2:]}"

            return records
        else:
            # Single record create
            if not vals.get('name') or vals['name'] == _('New'):
                today = datetime.now()
                date_str = today.strftime('%d%m%y')
                count = self.search_count([
                    ('create_date', '>=', today.replace(hour=0, minute=0, second=0, microsecond=0)),
                    ('create_date', '<', today.replace(hour=23, minute=59, second=59, microsecond=999999))
                ]) + 1
                vals['name'] = f"RQ{date_str}{seq}"

            if vals.get('product_id'):
                bom = self.env['mrp.bom'].search(
                    [('product_tmpl_id', '=', self.env['product.product'].browse(vals['product_id']).product_tmpl_id.id)],
                    order='write_date desc, id desc',
                    limit=1
                )
                if bom:
                    vals['bom_id'] = bom.id
                    # Autofill from BOM if present
                    if bom.product_uom_id:
                        vals['uom_id'] = bom.product_uom_id.id
                    if bom.product_owner_id:
                        vals['product_owner_id'] = bom.product_owner_id.id
                    if bom.admin_id:
                        vals['admin_id'] = bom.admin_id.id

            record = super().create(vals)
            if not record.bom_exists:
                self._assign_design_team(record)
            if record.mrp_production_id:
                record.mrp_production_id.name = f"MO{record.name[2:]}"

            return record

    def _assign_design_team(self, rec):
        design_group = self.env.ref('yucart_mrp_request.group_design_team', raise_if_not_found=False)
        design_user = design_group.user_ids[:1] if design_group and design_group.user_ids else False
        if design_user:
            rec.admin_id = design_user.id
            rec.message_post(body=_("No BOM found. Routed to Design team for BOM creation."), partner_ids=[design_user.partner_id.id])

    # === Compute Methods ===
    @api.depends('product_id')
    def _compute_bom_exists(self):
        for rec in self:
            bom = self.env['mrp.bom'].search([
                ('product_tmpl_id', '=', rec.product_id.product_tmpl_id.id)
            ], limit=1)
            rec.bom_exists = bool(bom)

    @api.depends()
    def _compute_product_owner_ids(self):
        group = self.env.ref('yucart_mrp_request.group_product_owner', raise_if_not_found=False)
        for rec in self:
            rec.product_owner_ids = group.user_ids if group else self.env['res.users'].search([])

    @api.depends()
    def _compute_admin_ids(self):
        group = self.env.ref('mrp.group_mrp_manager', raise_if_not_found=False)
        for rec in self:
            rec.admin_ids = group.user_ids if group else self.env['res.users'].search([])

    # === Workflow Actions ===
    def _requests_list_action(self):
        """Build an act_window dict referencing view ids (no read on ir.actions.act_window)."""
        try:
            list_view = self.env.ref('yucart_mrp_request.view_mrp_request_list', raise_if_not_found=False)
            form_view = self.env.ref('yucart_mrp_request.view_mrp_request_form', raise_if_not_found=False)
            kanban_view = self.env.ref('yucart_mrp_request.view_mrp_request_kanban', raise_if_not_found=False)
            views = []
            if list_view:
                views.append((list_view.id, 'list'))    # changed 'tree' -> 'list'
            if form_view:
                views.append((form_view.id, 'form'))
            if kanban_view:
                views.append((kanban_view.id, 'kanban'))
            return {
                'type': 'ir.actions.act_window',
                'name': _('Requests'),
                'res_model': 'mrp.request',
                'view_mode': ','.join([t for (_id, t) in views]) if views else 'list,form,kanban',
                'views': views or False,
                'target': 'current',
            }
        except Exception:
            # Fallback to a minimal action dict (client may still choose default views)
            return {
                'type': 'ir.actions.act_window',
                'name': _('Requests'),
                'res_model': 'mrp.request',
                'view_mode': 'list,form,kanban',
                'target': 'current',
            }

    def action_submit_po(self):
        """ new → pending_po """
        for rec in self:
            rec.state = 'pending_po'
            # Create Manufacturing Order if not already created
            if not rec.mrp_production_id:
                mo_vals = {
                    'product_id': rec.product_id.id,
                    'product_qty': rec.qty,
                    'product_uom_id': rec.uom_id.id,
                    'bom_id': rec.bom_id.id,
                    'date_start': rec.start_date,
                    'date_deadline': rec.start_date,
                    'origin': rec.name,
                    'user_id': rec.product_owner_id.id,
                    'mrp_request_id': rec.id,
                    'name': f"MO{rec.name[2:]}",  # Set MO number to match request
                    'requested_date': rec.requested_date,
                    'expected_delivery_date': rec.expected_delivery_date,  # <-- sync expected_delivery_date
                }
                mo = self.env['mrp.production'].create(mo_vals)
                rec.mrp_production_id = mo.id
        return self._requests_list_action()

    def action_accept_by_po(self):
        """ pending_po → waiting_admin, only Product Owner """
        for rec in self:
            if rec.product_owner_id != self.env.user:
                raise UserError(_("Only the assigned Product Owner can verify this request."))
            rec.state = 'waiting_admin'
        return self._requests_list_action()

    def action_request_change(self):
        """ pending_po → change_requested, reason required, notify Admin """
        for rec in self:
            if not rec.note:
                raise UserError(_("Please provide a reason for the change request in the Notes tab."))
            rec.state = 'change_requested'
            # Notify Admin (Production Manager)
            if rec.admin_id:
                rec.message_post(
                    body=_("Change requested by Product Owner: %s") % rec.note,
                    partner_ids=[rec.admin_id.partner_id.id]
                )

    def action_approve_admin(self):
        """ waiting_admin → approved, only Admin """
        for rec in self:
            if rec.admin_id != self.env.user:
                raise UserError(_("Only the assigned Production Manager can approve this request."))
            if not rec.bom_id:
                raise UserError(_("Please select a Bill of Materials (BOM) for product %s") % rec.product_id.display_name)
            rec.state = 'approved'
            # Confirm the corresponding Manufacturing Order if exists
            if rec.mrp_production_id and rec.mrp_production_id.state == 'draft':
                rec.mrp_production_id.action_confirm()
        return self._requests_list_action()

    def action_reject(self):
        """ waiting_admin → rejected, only Admin """
        for rec in self:
            if rec.admin_id != self.env.user:
                raise UserError(_("Only the assigned Production Manager can reject this request."))
            rec.state = 'rejected'
            # Delete related manufacturing order if exists
            if rec.mrp_production_id:
                rec.mrp_production_id.unlink()
                rec.mrp_production_id = False

    def _create_activity_for_product_owner(self):
        """Create a reminder activity for Product Owner if request is pending."""
        for rec in self.filtered(lambda r: r.state == 'pending_po' and r.product_owner_id):
            self.env['mail.activity'].create({
                'res_model_id': self.env['ir.model']._get_id('mrp.request'),
                'res_id': rec.id,
                'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                'user_id': rec.product_owner_id.id,
                'summary': _('Pending Manufacturing Request'),
                'note': _('Please review and act on the pending manufacturing request: %s') % rec.name,
                'date_deadline': fields.Date.today(),
            })

    def _notify_product_owner(self):
        """Send a pop-up notification to Product Owner if request is pending."""
        for rec in self.filtered(lambda r: r.state == 'pending_po' and r.product_owner_id):
            rec.message_post(
                body=_('You have a pending manufacturing request to review: %s') % rec.name,
                partner_ids=[rec.product_owner_id.partner_id.id],
                message_type='notification',
                subtype_xmlid='mail.mt_note',
                notify_by_email=False,
                notify=True,
            )

    @api.model
    def cron_remind_product_owners(self, *, limit=100):
        """Create a To Do activity for Product Owner for each pending request, with request number in title and direct link."""
        domain = [
            ('state', '=', 'pending_po'),
            ('product_owner_id', '!=', False),
            ('notified_po', '=', False),
        ]
        requests = self.search(domain, limit=limit)
        for rec in requests:
            try:
                self.env['mail.activity'].create({
                    'res_model_id': self.env['ir.model']._get_id('mrp.request'),
                    'res_id': rec.id,
                    'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                    'user_id': rec.product_owner_id.id,
                    'summary': _('Review Request: %s') % rec.name,  # Request number in title
                    'note': _('You have a pending manufacturing request to review: %s') % rec.name,
                    'date_deadline': fields.Date.today(),
                })
                rec.notified_po = True
            except Exception as e:
                _logger.error("Failed to create activity for Product Owner for request %s: %s", rec.name, str(e))
        remaining = 0 if len(requests) == limit else self.search_count(domain)
        self.env['ir.cron']._commit_progress(len(requests), remaining=remaining)

    @api.model
    def cron_admin_pending_summary(self):
        """Scheduled action: Send summary to Admins of all pending requests from previous day."""
        yesterday = fields.Date.today() - timedelta(days=1)
        requests = self.search([
            ('state', '=', 'pending_po'),
            ('create_date', '>=', datetime.combine(yesterday, datetime.min.time())),
            ('admin_id', '!=', False)
        ])
        admin_map = {}
        for rec in requests:
            admin_map.setdefault(rec.admin_id, []).append(rec)
        for admin, reqs in admin_map.items():
            body = _("Pending Manufacturing Requests from yesterday:\n")
            for r in reqs:
                body += "- %s (%s)\n" % (r.name, r.product_id.display_name)
            admin.partner_id.message_post(body=body)

    @api.model
    def cron_delete_old_rejected_requests(self):
        """Delete requests that have been rejected for more than 3 days."""
        threshold = datetime.now() - timedelta(days=3)
        old_rejected = self.search([
            ('state', '=', 'rejected'),
            ('write_date', '<', threshold)
        ])
        old_rejected.unlink()

    @api.onchange('product_id')
    def _onchange_product_id_autofill_owner_admin(self):
        if self.product_id:
            bom = self.env['mrp.bom'].search(
                [('product_tmpl_id', '=', self.product_id.product_tmpl_id.id)],
                order='write_date desc, id desc',
                limit=1
            )
            if bom:
                self.bom_id = bom
                if bom.product_uom_id:
                    self.uom_id = bom.product_uom_id
                if bom.product_owner_id:
                    self.product_owner_id = bom.product_owner_id
                if bom.admin_id:
                    self.admin_id = bom.admin_id

    @api.onchange('bom_id')
    def _onchange_bom_id_update_fields(self):
        if self.bom_id:
            if self.bom_id.product_uom_id:
                self.uom_id = self.bom_id.product_uom_id
            if self.bom_id.product_owner_id:
                self.product_owner_id = self.bom_id.product_owner_id
            if self.bom_id.admin_id:
                self.admin_id = self.bom_id.admin_id

    def write(self, vals):
        tracked_fields = [
            'product_id', 'qty', 'uom_id', 'start_date', 'requested_date',
            'expected_delivery_date', 'bom_id', 'product_owner_id', 'admin_id', 'note'
        ]
        changes = []
        for rec in self:
            for field in tracked_fields:
                if field in vals:
                    old = rec[field]
                    new = vals[field]
                    if isinstance(rec._fields[field], fields.Many2one):
                        old_disp = old.display_name if old else False
                        new_disp = self.env[rec._fields[field].comodel_name].browse(new).display_name if new else False
                        if old_disp != new_disp:
                            changes.append(f"{rec._fields[field].string} changed from '{old_disp}' to '{new_disp}'")
                    else:
                        if old != new:
                            changes.append(f"{rec._fields[field].string} changed from '{old}' to '{new}'")
        # Only update state and note in the initial write, not in sync context
        if changes and not self.env.context.get('no_mrp_request_sync'):
            vals = dict(vals)  # copy to avoid mutating caller
            vals['state'] = 'change_requested'
            change_note = "Change Requested: " + "; ".join(changes)
            vals['note'] = (self[0].note or '') + "\n" + change_note if self[0].note else change_note
        res = super(MrpRequest, self).write(vals)
        # avoid recursion when called by production
        if self.env.context.get('no_mrp_request_sync'):
            return res
        for rec in self:
            if not rec.mrp_production_id:
                continue
            update_vals = {}
            if any(k in vals for k in ('start_date',)):
                update_vals['date_start'] = rec.start_date
                update_vals['date_deadline'] = rec.start_date
            if 'requested_date' in vals:
                update_vals['requested_date'] = rec.requested_date
            if 'expected_delivery_date' in vals:
                update_vals['expected_delivery_date'] = rec.expected_delivery_date
            if 'product_id' in vals:
                update_vals['product_id'] = rec.product_id.id if rec.product_id else False
            if 'bom_id' in vals:
                update_vals['bom_id'] = rec.bom_id.id if rec.bom_id else False
            if 'qty' in vals:
                update_vals['product_qty'] = rec.qty
            if 'uom_id' in vals:
                update_vals['product_uom_id'] = rec.uom_id.id if rec.uom_id else False
            if update_vals:
                try:
                    rec.mrp_production_id.with_context(no_mrp_production_sync=True).write(update_vals)
                except Exception as e:
                    _logger.exception("Failed to sync MRPO to MO for %s: %s", rec.name, e)
        return res

    @api.model
    def cron_auto_submit_to_po(self, limit=100):
        """Auto-submit requests to Product Owner 1 minute after creation if Product Owner is set."""
        threshold = datetime.now() - timedelta(minutes=1)
        domain = [
            ('state', '=', 'new'),
            ('product_owner_id', '!=', False),
            ('auto_submitted_po', '=', False),
            ('create_date', '<=', threshold),
        ]
        requests = self.search(domain, limit=limit)
        for rec in requests:
            try:
                rec.action_submit_po()
                rec.auto_submitted_po = True
            except Exception as e:
                _logger.error("Auto-submit to PO failed for %s: %s", rec.name, str(e))