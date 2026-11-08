from django.core.exceptions import ValidationError

from apps.empresas.services import tiene_actividad
from apps.produccion.extensiones import al_crear_orden

from .models import Etapa


@al_crear_orden
def temperatura_del_sector(orden, usuario):
    """5.5: una sala de despiece de aves exige la temperatura del sector en cada orden; si viene, queda registrada."""
    if orden.tipo != "desposte":
        return
    if orden.temperatura_sector is None:
        if tiene_actividad("despiece_aves", declarada=True):
            raise ValidationError("La empresa declara despiece de aves: registrá la temperatura del sector.")
        return
    from .services import perfil_sala_aves, registrar_temperatura

    camara = orden.deposito_origen.camaras.filter(activa=True).first()
    registrar_temperatura(
        orden.temperatura_sector,
        usuario,
        camara=camara,
        etapa=Etapa.PROCESO,
        perfil=None if camara else perfil_sala_aves(),
        orden=orden,
        observacion=f"Temperatura del sector en la orden {orden.numero}",
        verificar=False,
    )
