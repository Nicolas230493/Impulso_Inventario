from django.core.exceptions import ValidationError

from apps.compras.extensiones import al_confirmar_recepcion
from apps.inventario.extensiones import al_aplicar, validador

from . import services


@validador
def sin_stock_en_padre(mov, linea):
    if linea.producto.tiene_variantes and linea.producto.padre_id is None:
        raise ValidationError(
            f"{linea.producto.nombre} es un artículo con variantes: elegí talle, color u otra variante."
        )


validador(services.validar_medida)
al_aplicar(services.aplicar_piezas)
al_confirmar_recepcion(services.al_recibir)
al_confirmar_recepcion(services.piezas_al_recibir)
