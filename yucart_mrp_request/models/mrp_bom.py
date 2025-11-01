from odoo import models, fields, api

class MrpBom(models.Model):
    _inherit = 'mrp.bom'

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