from apps.inventario.extensiones import al_aplicar, validador

from . import services

validador(services.validar_series)
al_aplicar(services.aplicar_series)
