import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    mrp_request_id = fields.Many2one('mrp.request', string='Request Id')
    request_approved = fields.Boolean(
        string='Request Approved',
        compute='_compute_request_approved',
        store=False
    )
    requested_date = fields.Datetime(string='Requested Date')
    expected_delivery_date = fields.Datetime(string='Expected Delivery Date')
    mrp_request_state = fields.Char(
        string='Request State',
        compute='_compute_mrp_request_state',
        store=False
    )

    def _compute_request_approved(self):
        for rec in self:
            rec.request_approved = rec.mrp_request_id.state == 'approved' if rec.mrp_request_id else True

    def _compute_mrp_request_state(self):
        for rec in self:
            rec.mrp_request_state = rec.mrp_request_id.state if rec.mrp_request_id else False

    def action_confirm(self):
        res = super().action_confirm()
        for production in self:
            for wo in production.workorder_ids:
                if wo.operation_id and hasattr(wo.operation_id, 'assigned_user_id') and wo.operation_id.assigned_user_id:
                    _logger.info("Assigning user from BOM operation: %s to workorder %s", wo.operation_id.assigned_user_id, wo.id)
                    wo.assigned_user_id = wo.operation_id.assigned_user_id
        return res

    def write(self, vals):
        tracked_fields = [
            'product_id', 'product_qty', 'product_uom_id', 'date_start', 'date_deadline',
            'requested_date', 'expected_delivery_date', 'bom_id'
        ]
        changes = []
        for rec in self:
            req = rec.mrp_request_id
            if not req:
                continue
            for field in tracked_fields:
                if field in vals:
                    old = rec[field]
                    new = vals[field]
                    # Skip change request for start date if from produce all
                    if field in ('date_start', 'date_deadline') and self.env.context.get('from_produce_all'):
                        continue
                    if isinstance(rec._fields[field], fields.Many2one):
                        old_disp = old.display_name if old else False
                        new_disp = self.env[rec._fields[field].comodel_name].browse(new).display_name if new else False
                        if old_disp != new_disp:
                            changes.append(f"{rec._fields[field].string} changed from '{old_disp}' to '{new_disp}'")
                    else:
                        if old != new:
                            changes.append(f"{rec._fields[field].string} changed from '{old}' to '{new}'")
        # Only update request state and note in the initial write, not in sync context
        # SKIP requesting change if moving to done or from produce all
        if changes and not self.env.context.get('no_mrp_production_sync') and not (vals.get('state') == 'done') and not self.env.context.get('from_produce_all'):
            req = self[0].mrp_request_id
            if req:
                change_note = "Change Requested: " + "; ".join(changes)
                req.with_context(no_mrp_request_sync=True).write({
                    'state': 'change_requested',
                    'note': (req.note or '') + "\n" + change_note if req.note else change_note
                })
        res = super(MrpProduction, self).write(vals)
        # avoid recursion when called by request
        if self.env.context.get('no_mrp_production_sync'):
            return res
        for rec in self:
            req = rec.mrp_request_id
            if not req:
                continue
            update_vals = {}
            if any(k in vals for k in ('date_start', 'date_deadline')):
                date_val = vals.get('date_start') or vals.get('date_deadline') or rec.date_start or rec.date_deadline
                update_vals['start_date'] = date_val
            if 'requested_date' in vals:
                update_vals['requested_date'] = rec.requested_date
            if 'expected_delivery_date' in vals:
                update_vals['expected_delivery_date'] = rec.expected_delivery_date
            if 'product_id' in vals:
                update_vals['product_id'] = rec.product_id.id if rec.product_id else False
            if 'bom_id' in vals:
                update_vals['bom_id'] = rec.bom_id.id if rec.bom_id else False
            if 'product_qty' in vals:
                update_vals['qty'] = rec.product_qty
            if 'product_uom_id' in vals:
                update_vals['uom_id'] = rec.product_uom_id.id if rec.product_uom_id else False
            if update_vals:
                try:
                    req.with_context(no_mrp_request_sync=True).write(update_vals)
                except Exception as e:
                    _logger.exception("Failed to sync MO to MRPO for MO %s: %s", rec.name, e)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        # Only allow manual MO creation if linked to a request, else create request and link
        new_records = []
        for vals in vals_list:
            req = None
            # If no request linked, create one and submit to admin
            if not vals.get('mrp_request_id'):
                req_vals = {
                    'product_id': vals.get('product_id'),
                    'qty': vals.get('product_qty', 1.0),
                    'uom_id': vals.get('product_uom_id'),
                    'start_date': vals.get('date_start') or vals.get('date_deadline'),
                    'requested_date': vals.get('requested_date'),
                    'expected_delivery_date': vals.get('expected_delivery_date'),
                    'bom_id': vals.get('bom_id'),
                    'note': _("Created automatically from MO by %s") % self.env.user.name,
                }
                req = self.env['mrp.request'].create(req_vals)
                # Directly submit to admin (skip Product Owner)
                req.state = 'waiting_admin'
                vals['mrp_request_id'] = req.id
            # Prevent duplicate MO for same request
            if vals.get('mrp_request_id'):
                existing_mo = self.search([('mrp_request_id', '=', vals['mrp_request_id'])], limit=1)
                if existing_mo:
                    raise UserError(_("A Manufacturing Order already exists for this request."))
            new_records.append(vals)
        records = super().create(new_records)
        # Sync MO fields to request
        for rec in records:
            if rec.mrp_request_id:
                update_vals = {
                    'start_date': rec.date_start or rec.date_deadline,
                    'product_id': rec.product_id.id,
                    'bom_id': rec.bom_id.id if rec.bom_id else False,
                    'qty': rec.product_qty,
                    'uom_id': rec.product_uom_id.id if rec.product_uom_id else False,
                    'requested_date': rec.requested_date,
                    'expected_delivery_date': rec.expected_delivery_date,
                    'mrp_production_id': rec.id,
                }
                rec.mrp_request_id.with_context(no_mrp_request_sync=True).write(update_vals)
        return records

    @api.onchange('bom_id')
    def _onchange_bom_id_update_fields(self):
        if self.bom_id:
            if self.bom_id.product_owner_id:
                self.user_id = self.bom_id.product_owner_id.id
            if self.bom_id.product_uom_id:
                self.product_uom_id = self.bom_id.product_uom_id.id
            # Sync to linked request
            if self.mrp_request_id:
                vals = {
                    'product_owner_id': self.bom_id.product_owner_id.id if self.bom_id.product_owner_id else False,
                    'admin_id': self.bom_id.admin_id.id if self.bom_id.admin_id else False,
                    'uom_id': self.bom_id.product_uom_id.id if self.bom_id.product_uom_id else False,
                }
                self.mrp_request_id.with_context(no_mrp_request_sync=True).write(vals)

    def button_mark_done(self):
        for rec in self:
            if rec.mrp_request_id and rec.mrp_request_id.state != 'approved':
                raise UserError(_("Cannot move the Manufacturing Order to 'Done' as the request is pending admin approval."))
        # Call super with context to indicate produce all
        return super(MrpProduction, self.with_context(from_produce_all=True)).button_mark_done()