from django.db import migrations

from ._datos import TIPOS


def crear(apps, schema_editor):
    T = apps.get_model("inventario", "TipoMovimiento")
    for codigo, nombre, signo, costo, motivo, autorizacion, bloqueado, vencido, manual in TIPOS:
        T.objects.update_or_create(codigo=codigo, defaults={
            "nombre": nombre, "signo": signo, "afecta_costo": costo, "requiere_motivo": motivo,
            "requiere_autorizacion": autorizacion, "admite_lote_bloqueado": bloqueado,
            "admite_lote_vencido": vencido, "manual": manual})


class Migration(migrations.Migration):
    dependencies = [("inventario", "0003_initial")]
    operations = [migrations.RunPython(crear, migrations.RunPython.noop)]
