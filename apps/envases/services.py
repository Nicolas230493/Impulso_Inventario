"""A5. Envases retornables: saldo de envases por cliente o proveedor."""

import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.inventario.extensiones import al_aplicar
from apps.nucleo.decimales import CERO, cantidad, importe

from .models import EnvaseAsociado, MovimientoEnvase, SaldoEnvase, TipoMovimientoEnvase


def asociar_envase(producto, envase, cantidad_envases=1, presentacion=None) -> EnvaseAsociado:
    if not envase.es_retornable:
        raise ValidationError(f"{envase.nombre} no está marcado como envase retornable.")
    ea = EnvaseAsociado(
        producto=producto, envase=envase, cantidad=cantidad(cantidad_envases), presentacion=presentacion
    )
    ea.save()
    return ea


def _saldo(cliente, proveedor, envase):
    filtro = {"cliente": cliente, "proveedor": proveedor, "envase": envase}
    saldo = SaldoEnvase.objects.select_for_update().filter(**filtro).first()
    if saldo is None:
        try:
            with transaction.atomic():
                SaldoEnvase(**filtro).save()
        except IntegrityError:
            pass
        saldo = SaldoEnvase.objects.select_for_update().get(**filtro)
    return saldo


@transaction.atomic
def registrar_envases(
    envase, cantidad_envases, tipo, cliente=None, proveedor=None, movimiento=None, observacion=""
) -> SaldoEnvase:
    """cantidad > 0: el tercero nos debe más; < 0: nos debe menos (o la empresa le debe más)."""
    if (cliente is None) == (proveedor is None):
        raise ValidationError("Indicá un cliente o un proveedor.")
    saldo = _saldo(cliente, proveedor, envase)
    saldo.cantidad = cantidad(saldo.cantidad + cantidad(cantidad_envases))
    saldo.ultimo_movimiento = timezone.now()
    saldo.save(update_fields=["cantidad", "ultimo_movimiento", "actualizado_en"])
    MovimientoEnvase(
        cliente=cliente,
        proveedor=proveedor,
        envase=envase,
        cantidad=cantidad(cantidad_envases),
        tipo=tipo,
        movimiento=movimiento,
        observacion=observacion,
    ).save()
    return saldo


def _envases_de(linea):
    asociados = EnvaseAsociado.objects.filter(producto=linea.producto).select_related("envase", "presentacion")
    resultado = []
    for a in asociados:
        if a.presentacion_id:
            if linea.presentacion_id and linea.presentacion_id != a.presentacion_id:
                continue
            n = abs(linea.cantidad) / a.presentacion.factor * a.cantidad
        else:
            n = abs(linea.cantidad) * a.cantidad
        resultado.append((a.envase, cantidad(n)))
    return resultado


@al_aplicar
def envases_al_vender(mov, lineas, usuario, es_reversion):
    """Al vender o despachar un producto con envase asociado se suma a la deuda de envases del cliente."""
    codigo = mov.revierte_a.tipo.codigo if es_reversion and mov.revierte_a_id else mov.tipo.codigo
    if codigo != "venta" or mov.cliente_id is None:
        return
    signo = -1 if es_reversion else 1
    for linea in lineas:
        for envase, n in _envases_de(linea):
            registrar_envases(envase, signo * n, TipoMovimientoEnvase.ENTREGA, cliente=mov.cliente, movimiento=mov)


def envases_en_recepcion(rec, envases: list[tuple], usuario):
    """Recibir del proveedor N envases llenos: la empresa le debe N (saldo negativo)."""
    for envase, n in envases:
        registrar_envases(
            envase,
            -cantidad(n),
            TipoMovimientoEnvase.RECEPCION,
            proveedor=rec.proveedor,
            movimiento=rec.movimiento,
            observacion=f"Recepción {rec.numero}",
        )


@transaction.atomic
def devolver_vacios(envase, n, usuario, cliente=None, proveedor=None, deposito_envases=None):
    """El cliente devuelve vacíos (baja su deuda y entran al depósito de envases) o la empresa los devuelve al
    proveedor (baja lo que le debe y salen del depósito)."""
    from apps.inventario.services import registrar

    n = cantidad(n)
    if cliente is not None:
        saldo = registrar_envases(envase, -n, TipoMovimientoEnvase.DEVOLUCION, cliente=cliente)
        if deposito_envases is not None:
            registrar(
                "envases_vacios",
                deposito_envases.sucursal,
                usuario,
                [
                    {
                        "producto": envase,
                        "deposito": deposito_envases,
                        "valor": n,
                        "costo_unitario": envase.valor_deposito,
                    }
                ],
                motivo=f"Vacíos devueltos por {cliente}",
                verificar=False,
            )
        return saldo
    saldo = registrar_envases(envase, n, TipoMovimientoEnvase.DEVOLUCION, proveedor=proveedor)
    if deposito_envases is not None:
        registrar(
            "envases_vacios",
            deposito_envases.sucursal,
            usuario,
            [{"producto": envase, "deposito": deposito_envases, "valor": -n}],
            motivo=f"Vacíos devueltos a {proveedor}",
            verificar=False,
        )
    return saldo


def cobrar_deposito(envase, n, cliente):
    return registrar_envases(
        envase,
        -cantidad(n),
        TipoMovimientoEnvase.COBRO,
        cliente=cliente,
        observacion=f"Cobro de depósito {importe(cantidad(n) * envase.valor_deposito)}",
    )


def reporte_envases():
    filas = []
    for s in SaldoEnvase.objects.exclude(cantidad=0).select_related("cliente", "proveedor", "envase"):
        filas.append(
            {
                "tercero": s.tercero,
                "tipo": "cliente" if s.cliente_id else "proveedor",
                "envase": s.envase,
                "cantidad": s.cantidad,
                "valor": importe(s.cantidad * s.envase.valor_deposito),
            }
        )
    return sorted(filas, key=lambda f: (f["tipo"], str(f["tercero"]), f["envase"].nombre))


def alertas_envases(dias_sin_movimiento: int = 30):
    limite = timezone.now() - datetime.timedelta(days=dias_sin_movimiento)
    alertas = []
    for s in SaldoEnvase.objects.filter(cliente__isnull=False, cantidad__gt=0).select_related("cliente", "envase"):
        if s.cliente.limite_envases and s.cantidad > s.cliente.limite_envases:
            alertas.append(f"{s.cliente}: debe {s.cantidad} {s.envase.nombre} (límite {s.cliente.limite_envases}).")
        elif s.ultimo_movimiento and s.ultimo_movimiento < limite:
            alertas.append(
                f"{s.cliente}: {s.cantidad} {s.envase.nombre} sin movimiento hace más de {dias_sin_movimiento} días."
            )
    return alertas


def saldo(envase, cliente=None, proveedor=None):
    s = SaldoEnvase.objects.filter(envase=envase, cliente=cliente, proveedor=proveedor).first()
    return s.cantidad if s else CERO
