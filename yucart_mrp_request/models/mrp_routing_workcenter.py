from odoo import models, fields

class MrpRoutingWorkcenter(models.Model):
    _inherit = 'mrp.routing.workcenter'

    assigned_user_id = fields.Many2one('res.users', string='Assigned To')