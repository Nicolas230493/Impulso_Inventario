from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.empresas.models import Empresa, Sucursal
from apps.empresas.services import crear_empresa
from apps.nucleo.contexto import empresa_activa
from apps.usuarios.models import Membresia, Rol, Usuario

CLAVE = "demo-impulso-2026"
USUARIOS = [
    ("dueno@demo.local", "Dueño", "dueno"),
    ("supervisor@demo.local", "Supervisor", "supervisor_produccion"),
    ("operario@demo.local", "Operario", "operario"),
    ("calidad@demo.local", "Calidad", "calidad"),
]


class Command(BaseCommand):
    help = "Crea una pollería de demostración con usuarios por rol, una recepción y un desposte aprobado."

    @transaction.atomic
    def handle(self, **options):
        if Empresa.objects.filter(cuit="30500010912").exists():
            self.stdout.write("La empresa demo ya existe.")
            return
        usuarios = {}
        for email, nombre, _rol in USUARIOS:
            u = Usuario(email=email, username=email, first_name=nombre, last_name="Demo")
            u.set_password(CLAVE)
            u.save()
            usuarios[email] = u
        dueno = usuarios["dueno@demo.local"]
        empresa = crear_empresa(
            razon_social="Pollería Demo S.A.",
            nombre_fantasia="Pollería Demo",
            cuit="30-50001091-2",
            condicion_iva="RI",
            dueno=dueno,
            rubro="polleria",
        )
        for email, _nombre, rol in USUARIOS[1:]:
            Membresia.objects.create(
                usuario=usuarios[email],
                empresa=empresa,
                rol=Rol.objects.get(empresa=None, codigo=rol),
                todas_las_sucursales=True,
            )
        with empresa_activa(empresa, usuario=dueno):
            self._datos(dueno)
        self.stdout.write(
            self.style.SUCCESS(
                "Demo lista. Usuarios: " + ", ".join(e for e, _, _ in USUARIOS) + f" · contraseña: {CLAVE}"
            )
        )

    def _datos(self, dueno):
        from apps.compras.models import CostoAdicional, LineaRecepcion, Recepcion
        from apps.compras.services import confirmar_recepcion
        from apps.depositos.models import Deposito
        from apps.desposte.services import precargar_polleria
        from apps.produccion import services as prod
        from apps.produccion.models import CategoriaMerma
        from apps.proveedores.models import Proveedor

        suc = Sucursal.objects.first()
        cat = precargar_polleria()
        sala = Deposito(sucursal=suc, nombre="Sala de desposte", tipo="desposte", permite_produccion=True)
        sala.save()
        camara = Deposito(sucursal=suc, nombre="Cámara de cortes", tipo="refrigerada", permite_venta=True)
        camara.save()
        Deposito(sucursal=suc, nombre="Mostrador", tipo="mostrador", permite_venta=True).save()
        prov = Proveedor(razon_social="Granja Los Álamos S.R.L.", cuit="30716583704", habilitacion_numero="SENASA 1234")
        prov.save()
        rec = Recepcion(
            proveedor=prov,
            sucursal=suc,
            deposito=sala,
            recibido_por=dueno,
            remito="0001-00004567",
            temperatura=D("1.5"),
        )
        rec.save()
        LineaRecepcion(
            recepcion=rec,
            producto=cat["pollo"],
            unidades=100,
            peso_declarado=D("251"),
            peso_recibido=D("250"),
            precio_unitario=D("3000"),
        ).save()
        CostoAdicional(recepcion=rec, concepto="flete", importe=D("25000"), criterio="peso").save()
        rec = confirmar_recepcion(rec, dueno)
        lote = rec.lineas.get().lote_generado
        orden = prod.crear_orden(
            cat["plantilla"], sala, camara, dueno, dueno, entradas=[{"lote": lote, "peso": D("125"), "unidades": 50}]
        )
        prod.reservar_materia_prima(orden, dueno)
        prod.iniciar_proceso(orden, dueno)
        prod.registrar_pesaje(orden, orden.entradas.get(), D("125"), usuario=dueno, unidades=50)
        prod.finalizar_entrada(orden, dueno)
        pesos = {"PECH": "32.5", "PMUS": "27.5", "MUSL": "22.5", "CARC": "20", "ALAS": "10", "MENU": "5", "RECO": "2.5"}
        for s in orden.salidas.select_related("producto"):
            prod.registrar_pesaje(orden, s, D(pesos[s.producto.codigo]), usuario=dueno)
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre="Goteo"), D("3"), dueno)
        prod.clasificar_diferencia(orden, CategoriaMerma.objects.get(nombre="Pérdida por limpieza"), D("2"), dueno)
        prod.enviar_a_control(orden, dueno)
        prod.aprobar_orden(orden, dueno, "Orden de demostración")
