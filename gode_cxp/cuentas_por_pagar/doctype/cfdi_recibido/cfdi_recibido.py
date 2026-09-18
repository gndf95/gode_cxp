from frappe.model.document import Document


class CFDIRecibido(Document):
    def before_save(self):
        partes = [self.nombre_emisor or self.rfc_emisor or "", (self.serie or "") + (self.folio or "")]
        self.titulo = " · ".join(p for p in partes if p)[:140]
