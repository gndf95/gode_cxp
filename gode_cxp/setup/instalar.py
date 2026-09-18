"""Configuración que la app garantiza en cada migración (idempotente)."""
import frappe


def asegurar_configuracion():
    """Se completa en las tareas siguientes: campos, roles, flujo y workspace."""
    frappe.clear_cache()
