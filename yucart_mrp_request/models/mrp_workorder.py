from odoo import models, fields

class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    assigned_user_id = fields.Many2one('res.users', string='Assigned To')

    def _apply_assignment_visibility(self, domain):
        user = self.env.user
        if user.has_group('yucart_mrp_request.group_product_owner') or user.has_group('mrp.group_mrp_manager'):
            return domain
        if self.env.context.get('no_workorder_visibility_filter'):
            return domain
        domain = domain or []
        return ['|', ('assigned_user_id', '=', user.id), ('assigned_user_id', '=', False)] + domain

    def search(self, domain=None, offset=0, limit=None, order=None, count=False):
        domain = self._apply_assignment_visibility(domain)
        if count:
            return self.search_count(domain)
        return super(MrpWorkorder, self).search(domain=domain, offset=offset, limit=limit, order=order)