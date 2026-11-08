"""Actualizaciones masivas eficientes.

`QuerySet.bulk_update` arma un `CASE WHEN pk = … THEN …` por fila: en PostgreSQL el costo crece con
filas × tamaño del lote y miles de productos tardan decenas de segundos. Acá se usa
`UPDATE … FROM (VALUES …)`, que resuelve cada lote con un solo join.
"""

from django.db import connection, transaction

TAMANO_LOTE = 1000


def actualizar_en_bloque(model, objs, campos, tamano_lote=TAMANO_LOTE) -> int:
    """Escribe `campos` de `objs` (ya cargados desde el manager con empresa) en lotes. Devuelve filas tocadas."""
    objs = [o for o in objs if o.pk is not None]
    if not objs or not campos:
        return 0
    if connection.vendor != "postgresql":
        return model._base_manager.bulk_update(objs, campos, batch_size=tamano_lote)
    meta = model._meta
    pk = meta.pk
    fields = [meta.get_field(c) for c in campos]
    q = connection.ops.quote_name
    tipos = [pk.db_type(connection)] + [f.db_type(connection) for f in fields]
    columnas = ", ".join(["pk"] + [f"c{i}" for i in range(len(fields))])
    asignaciones = ", ".join(f"{q(f.column)} = v.c{i}" for i, f in enumerate(fields))
    fila_sql = "(" + ", ".join(f"%s::{t}" for t in tipos) + ")"
    total = 0
    with transaction.atomic(savepoint=False), connection.cursor() as cursor:
        for inicio in range(0, len(objs), tamano_lote):
            lote = objs[inicio : inicio + tamano_lote]
            params = []
            for o in lote:
                params.append(pk.get_db_prep_value(o.pk, connection))
                params.extend(f.get_db_prep_save(getattr(o, f.attname), connection) for f in fields)
            sql = (
                f"UPDATE {q(meta.db_table)} AS t SET {asignaciones} "
                f"FROM (VALUES {', '.join([fila_sql] * len(lote))}) AS v({columnas}) "
                f"WHERE t.{q(pk.column)} = v.pk"
            )
            cursor.execute(sql, params)
            total += cursor.rowcount
    return total


def actualizar_con_historial(model, objs, campos, usuario=None, motivo=None, tamano_lote=TAMANO_LOTE) -> int:
    """Como `simple_history.utils.bulk_update_with_history`, pero con `actualizar_en_bloque`.

    En PostgreSQL el historial se copia con un `INSERT … SELECT` desde la tabla ya actualizada: armar miles de
    filas históricas en Python (con ~50 columnas cada una) era lo que más tardaba.
    """
    from django.utils import timezone
    from simple_history.utils import get_history_manager_for_model, get_history_model_for_model

    objs = [o for o in objs if o.pk is not None]
    with transaction.atomic(savepoint=False):
        filas = actualizar_en_bloque(model, objs, campos, tamano_lote)
        if not objs:
            return filas
        if connection.vendor != "postgresql":
            get_history_manager_for_model(model).bulk_history_create(
                objs, batch_size=tamano_lote, update=True, default_user=usuario, default_change_reason=motivo
            )
            return filas
        historico = get_history_model_for_model(model)
        q = connection.ops.quote_name
        propios = [f.column for f in model._meta.concrete_fields]
        columnas = ", ".join(q(c) for c in propios)
        sql = (
            f"INSERT INTO {q(historico._meta.db_table)} ({columnas}, history_date, history_type, "
            f"history_user_id, history_change_reason) "
            f"SELECT {columnas}, %s, '~', %s, %s FROM {q(model._meta.db_table)} "
            f"WHERE {q(model._meta.pk.column)} = ANY(%s)"
        )
        with connection.cursor() as cursor:
            cursor.execute(sql, [timezone.now(), usuario.pk if usuario else None, motivo, [o.pk for o in objs]])
    return filas
