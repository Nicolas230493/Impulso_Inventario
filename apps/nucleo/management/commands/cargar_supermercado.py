"""Demo completa de supermercado: más de 500 productos, dos sucursales, proveedores, 30 días de compras, ventas,
reposición de góndola y todos los procesos del sistema (producción, calidad, cadena de frío, trazabilidad, kits,
envases, inventarios, ajustes y alertas). Todo pasa por los servicios reales, así el stock cierra con el Kardex.

    python manage.py cargar_supermercado [--dias 30] [--semilla 7]
"""

import datetime
import math
import random
import time
from collections import defaultdict
from decimal import ROUND_HALF_UP
from decimal import Decimal as D

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.empresas.models import Empresa, Sucursal
from apps.empresas.services import crear_empresa
from apps.nucleo.contexto import empresa_activa
from apps.usuarios.models import Membresia, Rol, Usuario

from ._catalogo_supermercado import CATALOGO, CLIENTES, PROVEEDORES

CLAVE = "demo-impulso-2026"
DOMINIO = "super.local"
USUARIOS = [
    ("dueno", "Marta", "Gómez", "dueno"),
    ("encargado", "Jorge", "Pereyra", "encargado"),
    ("encargado.norte", "Lucía", "Fernández", "encargado"),
    ("supervisor", "Ramiro", "Sosa", "supervisor_produccion"),
    ("carnicero", "Héctor", "Ibáñez", "operario"),
    ("panadero", "Silvia", "Acosta", "operario"),
    ("cajero", "Brenda", "Molina", "vendedor"),
    ("calidad", "Paula", "Rinaldi", "calidad"),
    ("contador", "Esteban", "Luna", "lectura"),
]
PREFIJOS = {
    "Almacén seco": "ALM",
    "Panificados envasados": "PAN",
    "Bebidas": "BEB",
    "Lácteos": "LAC",
    "Fiambres": "FIA",
    "Congelados": "CON",
    "Frutas y verduras": "FYV",
    "Limpieza": "LIM",
    "Perfumería": "PER",
    "Golosinas": "GOL",
    "Mascotas": "MAS",
    "Bazar": "BAZ",
}
FACTOR_MARCA = [D("1"), D("0.9"), D("0.82"), D("0.95")]
DEMANDA_UN = {5: 14, 4: 8, 3: 4, 2: 2, 1: 0.7}
DEMANDA_KG = {5: 9, 4: 6, 3: 3.5, 2: 2, 1: 0.8}
PESO_MARCA = [1.3, 1.0, 0.7, 0.6]
UNIDADES_CONTENIDO = {"kg", "g", "l", "ml", "m", "un"}


def gs1(base: str) -> str:
    """Dígito verificador GS1 (EAN-13 y DUN-14)."""
    s = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(base)))
    return base + str((10 - s % 10) % 10)


def cuit(prefijo: str, cuerpo: int) -> str:
    from apps.nucleo.validadores import cuit_valido

    while True:
        base = f"{prefijo}{cuerpo:08d}"
        for v in range(10):
            if cuit_valido(base + str(v)):
                return base + str(v)
        cuerpo += 1


def q3(x) -> D:
    return D(str(x)).quantize(D("0.001"), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Crea «Súper Impulso»: supermercado de demostración con +500 productos y 30 días de operación."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=30)
        parser.add_argument("--semilla", type=int, default=7)

    def log(self, texto):
        self.stdout.write(f"[{time.time() - self.t0:6.1f}s] {texto}")
        self.stdout.flush()

    def handle(self, dias, semilla, **options):
        self.t0 = time.time()
        self.rnd = random.Random(semilla)
        self.cuit_empresa = cuit("30", 71555000)
        if Empresa.objects.filter(cuit=self.cuit_empresa).exists():
            raise CommandError("La empresa «Súper Impulso» ya existe.")
        with transaction.atomic():
            self.usuarios = self._usuarios()
            self.empresa = crear_empresa(
                razon_social="Supermercados Impulso S.A.",
                nombre_fantasia="Súper Impulso",
                cuit=self.cuit_empresa,
                condicion_iva="RI",
                dueno=self.usuarios["dueno"],
                rubro="supermercado",
            )
            self._membresias()
            with empresa_activa(self.empresa, usuario=self.usuarios["dueno"]):
                self._estructura()
                self._catalogo()
                self._proveedores_y_clientes()
                self._extras_catalogo()
                self.log(f"Catálogo listo: {len(self.productos)} productos de reventa.")
                self._simular(dias)
                self._produccion()
                self._procesos_de_inventario()
                self._compras_en_curso()
                self._kits_y_envases()
                self._cadena_frio(dias=7)
                self._calidad()
                self._trazabilidad_y_alertas()
        self._resumen()

    # ------------------------------------------------------------------------------------------------------------
    # Usuarios, empresa y estructura
    # ------------------------------------------------------------------------------------------------------------

    def _usuarios(self):
        usuarios = {}
        for alias, nombre, apellido, _rol in USUARIOS:
            email = f"{alias}@{DOMINIO}"
            u = Usuario.objects.filter(email=email).first()
            if u is None:
                u = Usuario(email=email, username=email, first_name=nombre, last_name=apellido)
                u.set_password(CLAVE)
                u.save()
            usuarios[alias] = u
        return usuarios

    def _membresias(self):
        for alias, _n, _a, rol in USUARIOS[1:]:
            Membresia.objects.create(
                usuario=self.usuarios[alias],
                empresa=self.empresa,
                rol=Rol.objects.get(empresa=None, codigo=rol),
                todas_las_sucursales=True,
            )
        admin = Usuario.objects.filter(is_superuser=True).order_by("id")
        for su in admin:
            for emp in Empresa.objects.all():
                Membresia.objects.get_or_create(
                    usuario=su,
                    empresa=emp,
                    defaults={
                        "rol": Rol.objects.get(empresa=None, codigo="administrador"),
                        "todas_las_sucursales": True,
                    },
                )

    def _estructura(self):
        from apps.depositos.models import Deposito
        from apps.desposte.services import precargar_polleria
        from apps.mercaderia.onboarding import precargar_rubro

        self.u = self.usuarios["dueno"]
        central = Sucursal.objects.get()
        central.direccion, central.localidad, central.provincia = "Av. Rivadavia 15200", "Ramos Mejía", "Buenos Aires"
        central.save()
        precargar_rubro("supermercado")
        self.polleria = precargar_polleria()
        norte = Sucursal(
            nombre="Sucursal Norte", direccion="Av. Maipú 2100", localidad="Vicente López", provincia="Buenos Aires"
        )
        norte.save()
        self.central, self.norte = central, norte

        def dep(nombre, tipo, sucursal=central, **kw):
            d = Deposito.objects.filter(nombre=nombre, sucursal=sucursal).first()
            if d is None:
                d = Deposito(sucursal=sucursal, nombre=nombre, tipo=tipo, **kw)
                d.save()
            for k, v in kw.items():
                setattr(d, k, v)
            d.save()
            return d

        self.dep = {
            "deposito": dep("Depósito", "general"),
            "salon": dep("Salón", "mostrador", permite_venta=True),
            "camara_frescos": dep("Cámara de frescos", "refrigerada"),
            "heladeras": dep("Heladeras de salón", "refrigerada", permite_venta=True),
            "camara_congelados": dep("Cámara de congelados", "congelados"),
            "freezers": dep("Freezers de salón", "congelados", permite_venta=True),
            "verduleria": dep("Verdulería", "mostrador", permite_venta=True),
            "camara_carnes": dep("Cámara de carnes", "refrigerada"),
            "mostrador_carniceria": dep("Mostrador carnicería", "mostrador", permite_venta=True),
            "sala_despiece": dep("Sala de despiece", "desposte", permite_produccion=True),
            "sala_aves": dep("Sala de desposte de aves", "desposte", permite_produccion=True),
            "panaderia": dep("Panadería", "general", permite_produccion=True, permite_venta=True),
            "rotiseria": dep("Rotisería", "general", permite_produccion=True, permite_venta=True),
            "fiambreria": dep("Fiambrería", "refrigerada", permite_produccion=True, permite_venta=True),
            "insumos": dep("Depósito de insumos", "general"),
            "envases": dep("Envases vacíos", "envases"),
            "sector_limpieza": dep("Sector limpieza y mantenimiento", "sector"),
            "salon_norte": dep("Salón Norte", "mostrador", sucursal=norte, permite_venta=True),
            "heladeras_norte": dep("Heladeras Norte", "refrigerada", sucursal=norte, permite_venta=True),
            "deposito_norte": dep("Depósito Norte", "general", sucursal=norte),
        }
        # guarda: (depósito de recepción, depósito de venta central, depósito de venta norte)
        d = self.dep
        self.guarda = {
            "seco": (d["deposito"], d["salon"], d["salon_norte"]),
            "frio": (d["camara_frescos"], d["heladeras"], d["heladeras_norte"]),
            "congelado": (d["camara_congelados"], d["freezers"], d["heladeras_norte"]),
            "fresco": (d["verduleria"], d["verduleria"], d["salon_norte"]),
        }
        from apps.consumo.models import CentroCosto

        self.centros = {
            n: CentroCosto.objects.get_or_create(nombre=n, defaults={"sucursal": central, "empresa": self.empresa})[0]
            for n in ("Limpieza del local", "Cocina de rotisería", "Administración", "Cafetería del personal")
        }
        self.log("Empresa, sucursales y depósitos creados.")

    # ------------------------------------------------------------------------------------------------------------
    # Catálogo
    # ------------------------------------------------------------------------------------------------------------

    def _catalogo(self):
        from apps.mercaderia.models import Presentacion, ReglaPrecio, Ubicacion
        from apps.mercaderia.services import precio_sugerido
        from apps.productos.models import Categoria, CodigoBarras, Marca, Producto, UnidadMedida

        unidades = {u.codigo: u for u in UnidadMedida.objects.filter(empresa=None)}
        marcas = {}
        self.productos = []  # dicts con producto, demanda, guarda, vida, proveedor_cat
        ean_seq = 100000000
        gondola = 0
        for top, (iva, markup, guarda, subs) in CATALOGO.items():
            cat_top, _ = Categoria.objects.get_or_create(nombre=top, padre=None, defaults={"empresa": self.empresa})
            if not ReglaPrecio.objects.filter(alcance="categoria", categoria=cat_top).exists():
                ReglaPrecio(alcance="categoria", categoria=cat_top, markup=D(markup), redondeo="10").save()
            n = 0
            for sub, items in subs.items():
                nombre_sub, _, vida = sub.partition("|")
                vida = int(vida) if vida else None
                cat_sub, _ = Categoria.objects.get_or_create(
                    nombre=nombre_sub, padre=cat_top, defaults={"empresa": self.empresa}
                )
                gondola += 1
                dep_venta = self.guarda[guarda][1]
                ubic, _ = Ubicacion.objects.get_or_create(
                    deposito=dep_venta,
                    codigo=f"G{gondola:02d}",
                    defaults={"descripcion": f"{top} › {nombre_sub}", "empresa": self.empresa},
                )
                for item in items:
                    base, lista_marcas, tamanos, rot = item.split("|")
                    rot = int(rot)
                    lista_marcas = [] if lista_marcas == "-" else lista_marcas.split("/")
                    for im, marca in enumerate(lista_marcas or [None]):
                        for it, tam_costo in enumerate(tamanos.split(",")):
                            tam, costo = tam_costo.rsplit(":", 1)
                            tam = tam.strip()
                            por_kg = tam == "kg"
                            costo = (D(costo) * FACTOR_MARCA[im % 4]).quantize(D("0.01"))
                            if marca and marca not in marcas:
                                marcas[marca], _ = Marca.objects.get_or_create(
                                    nombre=marca, defaults={"empresa": self.empresa}
                                )
                            n += 1
                            partes = [base] + ([marca] if marca else []) + ([] if por_kg or tam == "-" else [tam])
                            nombre = " ".join(partes) + (" (x kg)" if por_kg else "")
                            contenido, u_cont = D("0"), None
                            if not por_kg and " " in tam:
                                valor, uni = tam.split(" ", 1)
                                if uni in UNIDADES_CONTENIDO:
                                    contenido, u_cont = D(valor), unidades[uni]
                            p = Producto(
                                codigo=f"{PREFIJOS[top]}-{n:04d}",
                                sku=f"{PREFIJOS[top]}{n:04d}",
                                nombre=nombre,
                                categoria=cat_sub,
                                marca=marcas.get(marca),
                                unidad_base=unidades["kg" if por_kg else "un"],
                                controla_lote=bool(vida),
                                controla_vencimiento=bool(vida),
                                vida_util_dias=vida,
                                perfil_conservacion={
                                    "frio": "Refrigerado",
                                    "congelado": "Supercongelado",
                                    "fresco": "Fresco ambiente",
                                }.get(guarda, ""),
                                contenido_neto=contenido,
                                unidad_contenido=u_cont,
                                alicuota_iva=D(iva),
                                costo_reposicion=costo,
                                ubicacion_habitual=ubic,
                                vende_por_medida=por_kg,
                                venta_minima=D("0.1") if por_kg else D("0"),
                            )
                            demanda = (DEMANDA_KG if por_kg else DEMANDA_UN)[rot]
                            demanda *= PESO_MARCA[im % 4] * (1 if it == 0 else 0.7)
                            p.stock_minimo = q3(max(demanda * 3, 1))
                            p.stock_maximo = q3(max(demanda * 30, 6))
                            p.save()
                            p.precio_venta = precio_sugerido(p)
                            p.save(update_fields=["precio_venta"])
                            if not por_kg:
                                ean_seq += self.rnd.randint(1, 97)
                                CodigoBarras(producto=p, codigo=gs1(f"779{ean_seq:09d}"), tipo="ean13").save()
                                if rot >= 3:
                                    bulto = 6 if top == "Bebidas" else 12
                                    Presentacion(
                                        producto=p,
                                        nombre=f"Bulto x{bulto}",
                                        factor=D(bulto),
                                        codigo_barras=gs1(f"1779{ean_seq:09d}"),
                                        se_compra=True,
                                        se_vende=top == "Bebidas",
                                    ).save()
                            self.productos.append(
                                {
                                    "p": p,
                                    "demanda": demanda,
                                    "guarda": guarda,
                                    "vida": vida,
                                    "top": top,
                                    "costo": costo,
                                    "por_kg": por_kg,
                                }
                            )
        self.por_codigo = {f["p"].codigo: f for f in self.productos}

    def _proveedores_y_clientes(self):
        from apps.clientes.models import Cliente
        from apps.proveedores.models import ProductoProveedor, Proveedor

        hoy = timezone.localdate()
        self.proveedores = {}
        for i, (razon, rubros, direccion, localidad) in enumerate(PROVEEDORES):
            slug = razon.split()[0].lower().replace("í", "i").replace("á", "a")
            prov = Proveedor(
                razon_social=razon,
                cuit=cuit("30", 70100000 + i * 13579),
                contacto=self.rnd.choice(["Carlos", "Mariana", "Diego", "Valeria", "Pablo", "Laura"])
                + " "
                + self.rnd.choice(["Rodríguez", "López", "Martínez", "García", "Romero", "Díaz"]),
                telefono=f"11 4{self.rnd.randint(100, 999)}-{self.rnd.randint(1000, 9999)}",
                email=f"ventas@{slug}{i}.example.com",
                direccion=direccion,
                localidad=localidad,
                habilitacion_numero=f"RNE {self.rnd.randint(10, 99)}-{self.rnd.randint(100000, 999999)}",
                habilitacion_vencimiento=hoy + datetime.timedelta(days=self.rnd.choice([18, 95, 240, 400])),
                calificacion=self.rnd.choice([3, 4, 4, 5]),
            )
            prov.save()
            for r in rubros.split(","):
                self.proveedores[r] = prov
        for f in self.productos:
            prov = self.proveedores[f["top"]]
            f["proveedor"] = prov
            ProductoProveedor(
                proveedor=prov,
                producto=f["p"],
                codigo_proveedor=f"{prov.pk:02d}-{f['p'].sku}",
                ultimo_precio=f["costo"],
                fecha_ultimo_precio=hoy - datetime.timedelta(days=40),
            ).save()
        self.clientes = {}
        for i, (razon, doc, iva) in enumerate(CLIENTES):
            c = Cliente(razon_social=razon, documento=doc and cuit(doc[:2], 30000000 + i * 777), condicion_iva=iva)
            c.save()
            self.clientes[razon] = c
        self.log(f"{len(PROVEEDORES)} proveedores y {len(CLIENTES)} clientes.")

    def _extras_catalogo(self):
        """Envase retornable y el producto que lo usa (reventa)."""
        from apps.envases.services import asociar_envase
        from apps.productos.models import Categoria, Marca, Producto, UnidadMedida

        un = UnidadMedida.objects.get(empresa=None, codigo="un")
        cervezas = Categoria.objects.get(nombre="Cervezas")
        self.envase = Producto(
            codigo="ENV-BOT1L",
            nombre="Envase retornable botella 1 l",
            unidad_base=un,
            es_retornable=True,
            valor_deposito=D("900"),
            categoria=Categoria.objects.get_or_create(nombre="Envases", padre=None, defaults={"empresa": self.empresa})[
                0
            ],
        )
        self.envase.save()
        p = Producto(
            codigo="BEB-RET-1L",
            sku="BEBRET1L",
            nombre="Cerveza rubia retornable Quilmes 1 l (sin envase)",
            unidad_base=un,
            categoria=cervezas,
            marca=Marca.objects.get(nombre="Quilmes"),
            alicuota_iva=D("21"),
            costo_reposicion=D("1500"),
            precio_venta=D("2690"),
            stock_minimo=D("24"),
            stock_maximo=D("240"),
        )
        p.save()
        asociar_envase(p, self.envase, 1)
        f = {
            "p": p,
            "demanda": 6,
            "guarda": "seco",
            "vida": None,
            "top": "Bebidas",
            "costo": D("1500"),
            "por_kg": False,
            "proveedor": self.proveedores["Bebidas"],
        }
        self.productos.append(f)
        self.por_codigo[p.codigo] = f

    # ------------------------------------------------------------------------------------------------------------
    # Simulación de 30 días: compras, reposición de góndola y ventas
    # ------------------------------------------------------------------------------------------------------------

    def _momento(self, dia: datetime.date, hora: int, minuto: int = 0):
        return timezone.make_aware(datetime.datetime.combine(dia, datetime.time(hora, minuto)))

    def _fechar(self, mov, cuando):
        from apps.inventario.models import MovimientoStock

        MovimientoStock.objects.filter(pk=mov.pk).update(fecha=cuando, confirmado_en=cuando)

    def _intervalo(self, f):
        if not f["vida"] or f["vida"] >= 60:
            return None
        return max(2, min(10, f["vida"] // 2))

    def _reloj(self, cuando):
        """Mueve el reloj del sistema a `cuando` (vencimientos, FEFO, numeración y auditoría con fecha real)."""
        timezone.now = lambda: cuando

    def _simular(self, dias):
        from apps.lotes.services import marcar_vencidos

        hoy = timezone.localdate()
        inicio = hoy - datetime.timedelta(days=dias)
        self.recepciones = []
        original = timezone.now
        try:
            self._simular_dias(dias, hoy, inicio, marcar_vencidos)
        finally:
            timezone.now = original

    def _simular_dias(self, dias, hoy, inicio, marcar_vencidos):
        # Compra inicial (día −dias − 2) de todo; luego reposición periódica.
        for i in range(-2, dias + 1):
            dia = inicio + datetime.timedelta(days=i)
            self._reloj(self._momento(dia, 0, 5))
            marcar_vencidos()
            pedidos = defaultdict(list)
            for f in self.productos:
                intervalo = self._intervalo(f)
                if i == -2:
                    dias_cubre = intervalo * 1.2 if intervalo else 22
                elif intervalo and (i + 2) % intervalo == 0 and i < dias:
                    dias_cubre = intervalo * 1.15
                elif not intervalo and i == dias // 2 + 1:
                    dias_cubre = 20
                else:
                    continue
                q = f["demanda"] * dias_cubre * 1.4  # ambas sucursales
                q = q3(q) if f["por_kg"] else D(max(6, math.ceil(q / 6) * 6))
                pedidos[f["proveedor"]].append((f, q))
            for prov, lineas in pedidos.items():
                self._comprar(prov, lineas, dia)
            if i >= 0 and dia < hoy:
                self._reponer(dia)
                self._vender(dia, self.central, central=True)
                self._vender(dia, self.norte, central=False)
            self.log(f"Simulado hasta {dia:%d/%m}.")

    def _comprar(self, prov, lineas, dia, observar=None):
        from apps.compras.models import LineaOrdenCompra, OrdenCompra
        from apps.compras.services import confirmar_recepcion, enviar_orden, recepcion_desde_orden

        u = self.usuarios["encargado"]
        por_dep = defaultdict(list)
        for f, q in lineas:
            por_dep[self.guarda[f["guarda"]][0]].append((f, q))
        for dep, items in por_dep.items():
            self._reloj(self._momento(dia - datetime.timedelta(days=2), 10))
            oc = OrdenCompra(
                proveedor=prov,
                sucursal=self.central,
                deposito_destino=dep,
                fecha=dia - datetime.timedelta(days=2),
                fecha_esperada=dia,
            )
            oc.save()
            for f, q in items:
                LineaOrdenCompra(orden=oc, producto=f["p"], cantidad_pedida=q, precio_unitario=f["costo"]).save()
            enviar_orden(oc, u)
            self._reloj(self._momento(dia, 7, self.rnd.randint(0, 50)))
            rec = recepcion_desde_orden(
                oc,
                u,
                fecha_hora=timezone.now(),
                remito=f"0003-{self.rnd.randint(10000, 99999):08d}",
                temperatura=D("3.5") if dep.tipo == "refrigerada" else (D("-19") if dep.tipo == "congelados" else None),
            )
            for linea in rec.lineas.select_related("producto"):
                linea.peso_recibido = linea.peso_declarado
                linea.lote_proveedor = f"L{dia:%y%j}{self.rnd.randint(10, 99)}" if linea.producto.controla_lote else ""
                linea.save()
            rec = confirmar_recepcion(rec, u)
            self._fechar(rec.movimiento, rec.fecha_hora)
            self.recepciones.append(rec)

    def _disponible(self, producto, deposito):
        from apps.inventario.models import Stock
        from apps.lotes.services import lotes_disponibles

        if producto.controla_lote or producto.controla_vencimiento:
            return sum((s.libre for s in lotes_disponibles(producto, deposito)), D("0"))
        st = Stock.objects.filter(producto=producto, deposito=deposito, lote=None, dueno=None).first()
        return st.cantidad - st.cantidad_reservada if st else D("0")

    def _transferir(self, origen, destino, items, cuando, usuario, motivo):
        from apps.inventario import services as inv
        from apps.lotes.services import sugerir_fefo

        if not items:
            return
        mov = inv.crear_movimiento("transferencia", origen.sucursal, usuario, motivo=motivo, fecha=cuando)
        for p, q in items:
            if p.controla_lote or p.controla_vencimiento:
                partes = sugerir_fefo(p, origen, q)
            else:
                partes = [(None, q)]
            for lote, parte in partes:
                inv.agregar_linea(mov, p, origen, -parte, lote=lote)
                inv.agregar_linea(mov, p, destino, parte, lote=lote)
        inv.confirmar_movimiento(mov, usuario)
        self._fechar(mov, cuando)

    def _reponer(self, dia):
        """Reposición de góndola (central) y envío a la sucursal Norte."""
        envios = defaultdict(list)
        for f in self.productos:
            p = f["p"]
            origen, venta_c, venta_n = self.guarda[f["guarda"]]
            restante = None  # stock de origen todavía no asignado a otro envío
            for destino, factor in ((venta_c, 1.0), (venta_n, 0.4)):
                if destino.pk == origen.pk:
                    continue
                demanda = f["demanda"] * factor
                if self._disponible(p, destino) >= demanda * 2.5:
                    continue
                if restante is None:
                    restante = self._disponible(p, origen)
                q = min(restante, D(str(demanda * 6)))
                q = q3(q) if f["por_kg"] else D(math.floor(q))
                if q > 0:
                    envios[(origen, destino)].append((p, q))
                    restante -= q
        self._reloj(self._momento(dia, 8, 10))
        for (origen, destino), items in envios.items():
            u = self.usuarios["encargado.norte" if destino.sucursal_id == self.norte.pk else "encargado"]
            motivo = "Envío a Sucursal Norte" if destino.sucursal_id == self.norte.pk else "Reposición de góndola"
            self._transferir(origen, destino, items, self._momento(dia, 8, 10), u, motivo)

    def _vender(self, dia, sucursal, central):
        from apps.inventario import services as inv
        from apps.lotes.services import agregar_linea_con_lote, sugerir_fefo

        factor_dia = 1.35 if dia.weekday() in (4, 5) else (0.8 if dia.weekday() == 0 else 1.0)
        factor = factor_dia * (1.0 if central else 0.4)
        u = self.usuarios["cajero"]
        self._reloj(self._momento(dia, 21, 0 if central else 30))
        mov = inv.crear_movimiento(
            "venta", sucursal, u, motivo=f"Cierre de caja {dia:%d/%m/%Y}", fecha=self._momento(dia, 21)
        )
        n = 0
        for f in self.productos:
            p = f["p"]
            dep = self.guarda[f["guarda"]][1 if central else 2]
            media = f["demanda"] * factor
            if f["por_kg"]:
                q = q3(max(0, self.rnd.gauss(media, media * 0.25)))
            else:
                q = D(self._poisson(media))
            if q <= 0:
                continue
            q = min(q, self._disponible(p, dep))
            if not f["por_kg"]:
                q = D(math.floor(q))
            if q <= 0 or (f["por_kg"] and q < D("0.1")):
                continue
            if f["por_kg"] and f["vida"]:
                # por FEFO, sin vender restos de lote por debajo de la venta mínima
                partes = [(lt, x) for lt, x in sugerir_fefo(p, dep, q) if x >= p.venta_minima]
                if not partes:
                    continue
                for lt, x in partes:
                    inv.agregar_linea(mov, p, dep, x, lote=lt)
                n += 1
                continue
            agregar_linea_con_lote(mov, p, dep, q)
            n += 1
        if n:
            inv.confirmar_movimiento(mov, u, verificar=False)  # cierre del punto de venta
            self._fechar(mov, self._momento(dia, 21))
        else:
            mov.delete()
        # una venta a cuenta corriente con envases retornables, dos veces por semana
        if central and dia.weekday() in (1, 4):
            ret = self.por_codigo["BEB-RET-1L"]["p"]
            disp = self._disponible(ret, self.dep["salon"])
            if disp >= 12:
                self._reloj(self._momento(dia, 11))
                mov = inv.crear_movimiento(
                    "venta", sucursal, u, motivo="Venta a Bar El Farolito", fecha=self._momento(dia, 11)
                )
                mov.cliente = self.clientes["Bar El Farolito"]
                mov.save(update_fields=["cliente"])
                inv.agregar_linea(mov, ret, self.dep["salon"], D("12"))
                inv.confirmar_movimiento(mov, u, verificar=False)  # cierre del punto de venta
                self._fechar(mov, self._momento(dia, 11))

    def _poisson(self, lam):
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, round(self.rnd.gauss(lam, math.sqrt(lam))))
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rnd.random()
            if p <= L:
                return k
            k += 1

    # ------------------------------------------------------------------------------------------------------------
    # Producción propia: carnicería, aves, fiambrería, panadería y rotisería
    # ------------------------------------------------------------------------------------------------------------

    def _recibir_directo(self, prov, deposito, lineas, usuario, temperatura=None, observar=()):
        from apps.compras.models import LineaRecepcion, Recepcion
        from apps.compras.services import confirmar_recepcion

        rec = Recepcion(
            proveedor=prov,
            sucursal=deposito.sucursal,
            deposito=deposito,
            recibido_por=usuario,
            remito=f"0001-{self.rnd.randint(10000, 99999):08d}",
            temperatura=temperatura,
        )
        rec.save()
        for datos in lineas:
            datos = dict(datos)
            codigo = datos.pop("producto").codigo
            from apps.productos.models import Producto

            producto = Producto.objects.get(codigo=codigo)
            datos.setdefault("peso_declarado", datos["peso_recibido"])
            if codigo in observar:
                datos["resultado"] = "observado"
                datos["observacion"] = "Envase con golpes: queda en análisis"
            LineaRecepcion(recepcion=rec, producto=producto, **datos).save()
        return confirmar_recepcion(rec, self.usuarios["encargado"])

    def _procesar(self, plantilla, origen, destino, peso, usuario, pieza=None, lote=None, ruido=0.015):
        from apps.produccion import services as prod
        from apps.produccion.models import CategoriaMerma

        entrada = {"unidad_origen": pieza} if pieza else {"lote": lote, "peso": D(peso)}
        orden = prod.crear_orden(plantilla, origen, destino, self.usuarios["carnicero"], usuario, entradas=[entrada])
        prod.reservar_materia_prima(orden, usuario)
        prod.iniciar_proceso(orden, usuario)
        prod.finalizar_entrada(orden, usuario)
        peso = D(peso)
        for salida in orden.salidas.select_related("linea_plantilla"):
            rend = salida.linea_plantilla.rendimiento_estandar * D(str(1 + self.rnd.uniform(-ruido, ruido)))
            q = (peso * rend / 100).quantize(D("0.001"))
            if q > 0:
                prod.registrar_pesaje(orden, salida, q, usuario=usuario)
        resto = prod.balance(orden)["diferencia"]
        if resto > 0:
            cat = (
                CategoriaMerma.objects.filter(nombre="Oreo / goteo").first()
                or CategoriaMerma.objects.filter(imputacion="absorbe").first()
            )
            prod.clasificar_diferencia(orden, cat, resto, usuario)
        prod.enviar_a_control(orden, usuario)
        prod.aprobar_orden(orden, self.usuarios["supervisor"], "Desvíos dentro de lo habitual")
        orden.refresh_from_db()
        return orden

    def _tandas(self, receta, deposito, tope, paso=D("0.1")):
        """Máximo de tandas que alcanza con el stock del depósito (hacia abajo, en pasos de `paso`)."""
        maximo = tope
        for linea in receta.lineas.select_related("insumo"):
            if linea.cantidad > 0:
                maximo = min(maximo, self._disponible(linea.insumo, deposito) / linea.cantidad)
        return (maximo / paso).to_integral_value(rounding="ROUND_FLOOR") * paso

    def _produccion(self):
        from apps.elaboracion import services as el
        from apps.elaboracion.models import Receta
        from apps.produccion import services as prod
        from apps.produccion.models import CategoriaMerma, PlantillaTransformacion
        from apps.productos.models import Producto

        P = {p.codigo: p for p in Producto.objects.filter(codigo__regex=r"^(VAC|CER|FIA|INS|EMP|PAN|ROT|CAR|POL)")}
        pl = {p.familia: p for p in PlantillaTransformacion.objects.filter(activa=True)}
        d, sup = self.dep, self.usuarios["dueno"]  # administra producción; aprueba el supervisor
        carnes, aves, insumos = self.proveedores["Carnes"], self.proveedores["Aves"], self.proveedores["Insumos"]

        # --- vacuno: 4 medias reses, 2 despostadas en dos etapas -------------------------------------------------
        rec = self._recibir_directo(
            carnes,
            d["camara_carnes"],
            [
                {
                    "producto": P["VAC-MEDIARES"],
                    "unidades": 4,
                    "peso_recibido": D("441.6"),
                    "peso_declarado": D("444"),
                    "precio_unitario": D("6200"),
                },
                {"producto": P["CER-RES"], "unidades": 2, "peso_recibido": D("178.4"), "precio_unitario": D("3900")},
            ],
            sup,
            temperatura=D("2.8"),
        )
        lote_media = rec.lineas.get(producto=P["VAC-MEDIARES"]).lote_generado
        lote_cerdo = rec.lineas.get(producto=P["CER-RES"]).lote_generado
        piezas = prod.registrar_unidades(
            lote_media,
            [("T38-G1041", "111.2"), ("T38-G1042", "109.8"), ("T38-G1043", "110.6"), ("T38-G1044", "110.0")],
            sup,
            tipo="media res",
        )
        for pieza in piezas[:2]:
            cuarteo = self._procesar(
                pl["cuarteo-media-res"], d["camara_carnes"], d["camara_carnes"], pieza.peso, sup, pieza=pieza
            )
            for cuarto in cuarteo.unidades_generadas.all():
                familia = (
                    "desposte-cuarto-delantero"
                    if cuarto.producto.codigo == "VAC-CUARTO-DEL"
                    else "desposte-cuarto-trasero"
                )
                self._procesar(pl[familia], d["camara_carnes"], d["camara_carnes"], cuarto.peso, sup, pieza=cuarto)
        self._procesar(pl["desposte-porcino"], d["camara_carnes"], d["camara_carnes"], "89.6", sup, lote=lote_cerdo)

        # --- aves: 120 pollos, despostados 60 ---------------------------------------------------------------------
        rec = self._recibir_directo(
            aves,
            d["sala_aves"],
            [
                {
                    "producto": self.polleria["pollo"],
                    "unidades": 120,
                    "peso_recibido": D("301.2"),
                    "peso_declarado": D("303"),
                    "precio_unitario": D("2900"),
                }
            ],
            sup,
            temperatura=D("1.6"),
        )
        lote_pollo = rec.lineas.get().lote_generado
        orden = prod.crear_orden(
            self.polleria["plantilla"],
            d["sala_aves"],
            d["camara_carnes"],
            self.usuarios["carnicero"],
            sup,
            entradas=[{"lote": lote_pollo, "peso": D("150.6"), "unidades": 60}],
            temperatura_sector=D("9.5"),
        )
        prod.reservar_materia_prima(orden, sup)
        prod.iniciar_proceso(orden, sup)
        prod.registrar_pesaje(orden, orden.entradas.get(), D("150.6"), usuario=sup, unidades=60)
        prod.finalizar_entrada(orden, sup)
        for s in orden.salidas.select_related("linea_plantilla"):
            q = (D("150.6") * s.linea_plantilla.rendimiento_estandar / 100).quantize(D("0.001"))
            if q > 0:
                prod.registrar_pesaje(orden, s, q, usuario=sup)
        resto = prod.balance(orden)["diferencia"]
        if resto > 0:
            goteo = (
                CategoriaMerma.objects.filter(nombre="Goteo").first()
                or CategoriaMerma.objects.filter(imputacion="absorbe").first()
            )
            prod.clasificar_diferencia(orden, goteo, resto, sup)
        prod.enviar_a_control(orden, sup)
        prod.aprobar_orden(orden, self.usuarios["supervisor"], "Desposte del día")

        # --- fiambrería: 3 hormas de tybo fraccionadas ----------------------------------------------------------
        rec = self._recibir_directo(
            self.proveedores["Lácteos"],
            d["fiambreria"],
            [
                {
                    "producto": P["FIA-HORMA-TYBO"],
                    "unidades": 3,
                    "peso_recibido": D("12.3"),
                    "precio_unitario": D("6400"),
                }
            ],
            sup,
            temperatura=D("4.0"),
        )
        self._procesar(
            pl["fraccionado-horma-tybo"],
            d["fiambreria"],
            d["fiambreria"],
            "12.3",
            sup,
            lote=rec.lineas.get().lote_generado,
        )

        # --- insumos de elaboración ------------------------------------------------------------------------------
        ins = [
            ("INS-HARINA", "150"),
            ("INS-SAL", "10"),
            ("INS-LEVADURA", "5"),
            ("INS-HUEVO", "180"),
            ("INS-PANRALLADO", "20"),
            ("INS-CONDIMENTO", "5"),
            ("INS-TRIPA", "300"),
            ("EMP-BANDEJA", "500"),
            ("EMP-FILM", "1500"),
        ]
        self._recibir_directo(
            insumos,
            d["insumos"],
            [
                {"producto": P[c], "peso_recibido": D(q), "precio_unitario": P[c].costo_reposicion or D("100")}
                for c, q in ins
            ],
            sup,
        )
        enc = self.usuarios["encargado"]
        cuando = timezone.now()
        self._transferir(
            d["insumos"],
            d["panaderia"],
            [(P["INS-HARINA"], D("100")), (P["INS-SAL"], D("4")), (P["INS-LEVADURA"], D("3"))],
            cuando,
            enc,
            "Insumos para panadería",
        )
        self._transferir(
            d["insumos"],
            d["camara_carnes"],
            [
                (P["INS-HUEVO"], D("72")),
                (P["INS-PANRALLADO"], D("10")),
                (P["INS-CONDIMENTO"], D("2")),
                (P["INS-TRIPA"], D("150")),
                (P["EMP-BANDEJA"], D("100")),
            ],
            cuando,
            enc,
            "Insumos para carnicería y rotisería",
        )

        receta = lambda codigo: Receta.objects.get(producto__codigo=codigo, activa=True)  # noqa: E731
        # pan francés: 3 bolsas de harina
        o = el.crear_orden_elaboracion(
            receta("PAN-FRANCES"),
            D("3"),
            d["panaderia"],
            d["panaderia"],
            self.usuarios["supervisor"],
            observaciones="Horneada de la mañana",
        )
        el.registrar_produccion(o, D("106.5"), sup)
        el.aprobar_elaboracion(o, self.usuarios["supervisor"])
        # chorizo con los recortes del cerdo
        tandas = self._tandas(receta("CAR-CHORIZO"), d["camara_carnes"], D("1"))
        if tandas > 0:
            o = el.crear_orden_elaboracion(
                receta("CAR-CHORIZO"), tandas, d["camara_carnes"], d["mostrador_carniceria"], sup
            )
            el.registrar_produccion(o, (D("9.8") * tandas).quantize(D("0.001")), sup, unidades=int(98 * tandas))
            el.aprobar_elaboracion(o, self.usuarios["supervisor"])
        # milanesas con nalga propia
        tandas = self._tandas(receta("ROT-MILA"), d["camara_carnes"], D("1.5"))
        if tandas > 0:
            o = el.crear_orden_elaboracion(receta("ROT-MILA"), tandas, d["camara_carnes"], d["rotiseria"], sup)
            el.registrar_produccion(o, (D("12.7") * tandas).quantize(D("0.001")), sup, unidades=int(26 * tandas))
            el.aprobar_elaboracion(o, self.usuarios["supervisor"], "Desvío menor en pan rallado")
        # queso rallado con los recortes de horma
        tandas = self._tandas(receta("FIA-RALLADO"), d["fiambreria"], D("1"), paso=D("0.01"))
        if tandas > D("0.05"):
            o = el.crear_orden_elaboracion(receta("FIA-RALLADO"), tandas, d["fiambreria"], d["fiambreria"], sup)
            el.registrar_produccion(o, (D("4.8") * tandas).quantize(D("0.001")), sup)
            el.aprobar_elaboracion(o, self.usuarios["supervisor"])
        # cortes a mostrador
        cortes = [
            (P[c], min(self._disponible(P[c], d["camara_carnes"]), D("8")))
            for c in ("VAC-ASADO", "VAC-VACIO", "VAC-NALGA", "VAC-BIFE", "CER-BONDIOLA")
        ]
        self._transferir(
            d["camara_carnes"],
            d["mostrador_carniceria"],
            [c for c in cortes if c[1] > 0],
            timezone.now(),
            enc,
            "Armado de mostrador",
        )
        self.log("Producción propia: medias reses, cerdo, pollos, hormas, pan, chorizo, milanesas y rallado.")

    # ------------------------------------------------------------------------------------------------------------
    # Ajustes, mermas, consumos, devoluciones, inventarios
    # ------------------------------------------------------------------------------------------------------------

    def _elegir(self, guarda=None, con_lote=None, deposito=None, n=5, minimo=D("3")):
        candidatos = []
        for f in self.productos:
            if guarda and f["guarda"] != guarda:
                continue
            if con_lote is not None and bool(f["vida"]) != con_lote:
                continue
            dep = deposito or self.guarda[f["guarda"]][1]
            if self._disponible(f["p"], dep) >= minimo:
                candidatos.append(f["p"])
            if len(candidatos) >= n * 4:
                break
        self.rnd.shuffle(candidatos)
        return candidatos[:n]

    def _procesos_de_inventario(self):
        from apps.analisis.services import generar_conteo_ciclico
        from apps.compras.models import DevolucionProveedor, LineaDevolucionProveedor
        from apps.compras.services import (
            aprobar_inventario,
            cerrar_conteo,
            confirmar_devolucion,
            iniciar_inventario,
            registrar_conteo,
        )
        from apps.inventario import services as inv
        from apps.lotes.models import EstadoLote
        from apps.lotes.services import marcar_vencidos
        from apps.mercaderia.services import aplicar_actualizacion

        d, enc, dueno = self.dep, self.usuarios["encargado"], self.usuarios["dueno"]
        # merma por rotura (requiere autorización → la autoriza el dueño)
        mov = inv.crear_movimiento("merma", self.central, self.usuarios["cajero"], motivo="Rotura en góndola")
        for p in self._elegir("seco", con_lote=False, n=3):
            inv.agregar_linea(mov, p, d["salon"], D("1"))
        mov = inv.confirmar_movimiento(mov, self.usuarios["cajero"], verificar=False)
        if mov.estado == "pendiente_autorizacion":
            inv.autorizar_movimiento(mov, dueno, "Roturas verificadas en góndola")
        # ajuste negativo que queda pendiente de autorización
        mov = inv.crear_movimiento(
            "ajuste_negativo", self.central, self.usuarios["cajero"], motivo="Faltante detectado en control de góndola"
        )
        for p in self._elegir("seco", con_lote=False, n=2, minimo=D("8")):
            inv.agregar_linea(mov, p, d["salon"], D("6"))
        inv.confirmar_movimiento(mov, self.usuarios["cajero"], verificar=False)
        # consumo interno con centro de costo
        mov = inv.crear_movimiento("consumo_interno", self.central, enc, motivo="Limpieza semanal del local")
        limpieza = [
            f["p"] for f in self.productos if f["top"] == "Limpieza" and self._disponible(f["p"], d["deposito"]) >= 3
        ][:4]
        for p in limpieza:
            inv.agregar_linea(mov, p, d["deposito"], D("2"), centro_costo=self.centros["Limpieza del local"])
        if limpieza:
            inv.confirmar_movimiento(mov, enc)
        # devolución al proveedor de lácteos (yogures con envase dañado)
        yogures = [
            f["p"]
            for f in self.productos
            if f["top"] == "Lácteos" and f["vida"] and self._disponible(f["p"], d["camara_frescos"]) >= 4
        ][:2]
        if yogures:
            dev = DevolucionProveedor(
                proveedor=self.proveedores["Lácteos"],
                sucursal=self.central,
                motivo="Envases inflados: cadena de frío del transporte",
            )
            dev.save()
            from apps.lotes.services import sugerir_fefo

            for p in yogures:
                for lote, q in sugerir_fefo(p, d["camara_frescos"], D("4")):
                    LineaDevolucionProveedor(
                        devolucion=dev, producto=p, deposito=d["camara_frescos"], lote=lote, cantidad=q
                    ).save()
            confirmar_devolucion(dev, enc)
        # vencimientos: marcar y descartar casi todos los vencidos (quedan algunos para la alerta)
        marcar_vencidos()
        vencidos = []
        from apps.inventario.models import Stock

        for st in Stock.objects.filter(lote__estado=EstadoLote.VENCIDO, cantidad__gt=0).select_related(
            "lote", "producto", "deposito"
        ):
            vencidos.append(st)
        a_descartar = vencidos[:-3] if len(vencidos) > 3 else []
        if a_descartar:
            mov = inv.crear_movimiento(
                "descarte_vencimiento", self.central, enc, motivo="Descarte de mercadería vencida (control diario)"
            )
            for st in a_descartar:
                inv.agregar_linea(mov, st.producto, st.deposito, st.cantidad, lote=st.lote)
            mov = inv.confirmar_movimiento(mov, enc)
        # devolución de cliente
        mov = inv.crear_movimiento(
            "devolucion_cliente", self.central, enc, motivo="Cliente devuelve producto sin abrir"
        )
        p = self._elegir("seco", con_lote=False, n=1)[0]
        inv.agregar_linea(mov, p, d["salon"], D("1"), costo_unitario=p.costo_reposicion)
        inv.confirmar_movimiento(mov, enc)
        # inventario físico parcial aprobado en el salón
        productos = self._elegir("seco", con_lote=False, n=25)
        inventario = iniciar_inventario(d["salon"], enc, tipo="parcial", productos=productos)
        for i, linea in enumerate(inventario.lineas.all()):
            dif = D("0") if i % 4 else D(self.rnd.choice([-2, -1, 1]))
            registrar_conteo(
                linea,
                max(D("0"), linea.cantidad_sistema + dif),
                "" if dif == 0 else ("Faltante sin explicar" if dif < 0 else "Mercadería mal ubicada"),
            )
        cerrar_conteo(inventario, enc)
        aprobar_inventario(inventario, dueno)
        # conteo cíclico en curso en el depósito
        generar_conteo_ciclico(d["deposito"], enc, n=30)
        # actualización masiva de precios (+4 % en bebidas) que queda en el historial
        from apps.productos.models import Categoria

        aplicar_actualizacion(
            "venta", {"categoria": Categoria.objects.get(nombre="Bebidas").pk}, dueno, porcentaje=D("4")
        )
        self.log("Mermas, ajustes, consumo interno, devoluciones, vencidos, inventario y precios.")

    def _compras_en_curso(self):
        from apps.compras.models import LineaOrdenCompra, OrdenCompra
        from apps.compras.services import confirmar_recepcion, enviar_orden, recepcion_desde_orden
        from apps.mercaderia.services import orden_desde_sugerencia, sugerencia_pedido

        enc, hoy = self.usuarios["encargado"], timezone.localdate()
        # borrador desde la sugerencia de pedido (stock máximo − stock − pedidos en curso)
        prov = self.proveedores["Limpieza"]
        filas = sugerencia_pedido(prov, self.dep["deposito"])[:20]
        if filas:
            orden_desde_sugerencia(prov, self.dep["deposito"], filas, enc)
        # enviada, pendiente de recibir
        for clave in ("Almacén seco", "Golosinas"):
            prov = self.proveedores[clave]
            oc = OrdenCompra(
                proveedor=prov,
                sucursal=self.central,
                deposito_destino=self.dep["deposito"],
                fecha_esperada=hoy + datetime.timedelta(days=2),
                observaciones="Pedido semanal",
            )
            oc.save()
            for f in [f for f in self.productos if f["proveedor"] == prov and not f["vida"]][:15]:
                LineaOrdenCompra(orden=oc, producto=f["p"], cantidad_pedida=D("24"), precio_unitario=f["costo"]).save()
            enviar_orden(oc, enc)
        # parcialmente recibida, con una línea observada (lote bloqueado)
        prov = self.proveedores["Fiambres"]
        oc = OrdenCompra(
            proveedor=prov, sucursal=self.central, deposito_destino=self.dep["camara_frescos"], fecha_esperada=hoy
        )
        oc.save()
        items = [f for f in self.productos if f["proveedor"] == prov][:6]
        for f in items:
            LineaOrdenCompra(orden=oc, producto=f["p"], cantidad_pedida=D("30"), precio_unitario=f["costo"]).save()
        enviar_orden(oc, enc)
        rec = recepcion_desde_orden(oc, enc, temperatura=D("5.5"), remito="0004-00012877")
        for i, linea in enumerate(rec.lineas.all()):
            if i < 3:
                linea.peso_recibido = D("30") if i else D("28")
                if i == 2:
                    linea.resultado = "observado"
                    linea.observacion = "Rotulado incompleto: se pide certificado al proveedor"
                linea.save()
            else:
                linea.delete()
        confirmar_recepcion(rec, enc)
        self.log("Órdenes de compra en borrador, enviadas y parcialmente recibidas.")

    # ------------------------------------------------------------------------------------------------------------
    # Kits, envases, cadena de frío, calidad, trazabilidad
    # ------------------------------------------------------------------------------------------------------------

    def _kits_y_envases(self):
        from apps.envases.services import devolver_vacios
        from apps.kits.services import armar_kits, definir_kit
        from apps.productos.models import Categoria, Producto, UnidadMedida

        un = UnidadMedida.objects.get(empresa=None, codigo="un")
        combos = Categoria.objects.get_or_create(
            nombre="Combos y canastas", padre=None, defaults={"empresa": self.empresa}
        )[0]

        def buscar(texto):
            return next(f["p"] for f in self.productos if f["p"].nombre.startswith(texto))

        combo = Producto(
            codigo="KIT-FERNET",
            nombre="Combo Fernet Branca 750 ml + Coca-Cola 2.25 l",
            unidad_base=un,
            categoria=combos,
            precio_venta=D("19990"),
        )
        combo.save()
        definir_kit(combo, "virtual", [(buscar("Fernet Branca 750"), 1), (buscar("Gaseosa cola Coca-Cola 2.25"), 1)])
        canasta = Producto(
            codigo="KIT-CANASTA",
            nombre="Canasta navideña clásica",
            unidad_base=un,
            categoria=combos,
            precio_venta=D("32990"),
            vida_util_dias=90,
            controla_vencimiento=True,
        )
        canasta.save()
        definir_kit(
            canasta,
            "armado",
            [
                (buscar("Sidra Real"), 1),
                (buscar("Pan dulce con frutas Bauducco"), 1),
                (buscar("Turrón blando de almendras Arcor"), 2),
                (buscar("Garrapiñadas de maní"), 1),
            ],
        )
        try:
            armar_kits(canasta, 5, self.dep["deposito"], self.usuarios["encargado"])
        except Exception as e:  # sin stock suficiente de algún componente: el kit queda definido
            self.log(f"Canasta sin armar: {e}")
        devolver_vacios(
            self.envase,
            24,
            self.usuarios["cajero"],
            cliente=self.clientes["Bar El Farolito"],
            deposito_envases=self.dep["envases"],
        )
        self.log("Combo virtual, canasta armada y envases retornables.")

    def _cadena_frio(self, dias):
        from apps.cadena_frio.models import Camara, PerfilConservacion
        from apps.cadena_frio.services import precargar_perfiles, registrar_accion_correctiva, registrar_temperatura
        from apps.lotes.models import Lote
        from apps.lotes.services import liberar_lote

        cal = self.usuarios["calidad"]
        precargar_perfiles()
        perfiles = {p.nombre: p for p in PerfilConservacion.objects.all()}
        d = self.dep
        camaras = [
            Camara(
                deposito=d["camara_frescos"], identificador="CAM-01", tipo="refrigerada", minimo=D("0"), maximo=D("5")
            ),
            Camara(
                deposito=d["camara_carnes"],
                identificador="CAM-02",
                tipo="refrigerada",
                perfil=perfiles.get("Refrigerado carne fresca"),
                minimo=D("-1"),
                maximo=D("4"),
            ),
            Camara(
                deposito=d["camara_congelados"],
                identificador="CAM-03",
                tipo="congelados",
                perfil=perfiles.get("Supercongelado"),
                maximo=D("-18"),
            ),
            Camara(deposito=d["heladeras"], identificador="EXH-01", tipo="exhibidora", minimo=D("0"), maximo=D("6")),
            Camara(deposito=d["freezers"], identificador="EXH-02", tipo="exhibidora", maximo=D("-15")),
            Camara(
                deposito=d["sala_despiece"],
                identificador="SALA-01",
                tipo="sala",
                etapa="proceso",
                maximo=D("12"),
                frecuencia_horas=0,
            ),
            Camara(
                deposito=d["heladeras_norte"], identificador="EXH-N1", tipo="exhibidora", minimo=D("0"), maximo=D("6")
            ),
        ]
        for c in camaras:
            c.save()
        ahora = timezone.now()
        for c in camaras:
            lo, hi = c.rango()
            centro = (lo + hi) / 2 if lo is not None and hi is not None else (hi - D("3") if hi is not None else D(2))
            t = ahora - datetime.timedelta(days=dias)
            while t <= ahora:
                valor = centro + D(str(round(self.rnd.uniform(-1.2, 1.2), 1)))
                if lo is not None:
                    valor = max(valor, lo)
                if hi is not None:
                    valor = min(valor, hi)
                registrar_temperatura(valor, cal, camara=c, fecha_hora=t, verificar=False)
                t += datetime.timedelta(hours=4)
        # un corte de frío en la exhibidora: registro fuera de rango + acción correctiva
        reg = registrar_temperatura(
            D("9.4"),
            cal,
            camara=camaras[3],
            fecha_hora=ahora - datetime.timedelta(hours=30),
            observacion="Puerta abierta durante la reposición",
            verificar=False,
        )
        registrar_accion_correctiva(
            reg, "Se cerró la puerta, se controló a la hora (4,8 °C) y se revisó el burlete.", cal
        )
        for lote in Lote.objects.filter(estado="bloqueado", motivo_bloqueo__startswith="Temperatura fuera de rango"):
            liberar_lote(lote, cal, "Producto controlado: temperatura del núcleo dentro de rango")
        # registro pendiente de acción correctiva (alerta abierta) en la Sucursal Norte
        registrar_temperatura(
            D("7.8"), cal, camara=camaras[6], fecha_hora=ahora - datetime.timedelta(hours=2), verificar=False
        )
        for lote in Lote.objects.filter(estado="bloqueado", motivo_bloqueo__startswith="Temperatura fuera de rango"):
            liberar_lote(lote, cal, "Liberado para la demo: temperatura del producto conforme")
        self.log("Cámaras y registros de temperatura de 7 días.")

    def _calidad(self):
        from apps.calidad import services as cal
        from apps.calidad.models import PlantillaControl

        u = self.usuarios["calidad"]
        hoy = timezone.localdate()
        cal.precargar_plantillas()
        plantilla = PlantillaControl.objects.filter(etapa="recepcion").first()
        if plantilla:
            items = list(plantilla.items.all())
            for i, rec in enumerate(self.recepciones[-12:]):
                resultados = {it.pk: ("cumple", "") for it in items}
                if i == 3:
                    no_critico = next((it for it in items if not it.critico), None)
                    if no_critico:
                        resultados[no_critico.pk] = ("no_cumple", "Remito sin número de lote")
                cal.registrar_inspeccion(plantilla, resultados, u, recepcion=rec)
        nc = cal.abrir_no_conformidad("Exhibidora EXH-01 con burlete dañado: pérdida de frío", u, gravedad="menor")
        cal.tratar_no_conformidad(nc, "Se cambió el burlete y se agregó control cada 2 h por 48 h", u)
        cal.cerrar_no_conformidad(nc, "Registros de 48 h dentro de rango", u)
        cal.abrir_no_conformidad("Proveedor de fiambres entregó mercadería con rotulado incompleto", u)
        for nombre, poes in (
            ("Sala de despiece", "POES-02 Limpieza de sala"),
            ("Panadería", "POES-04 Limpieza de hornos y mesadas"),
            ("Verdulería", "POES-06 Limpieza de exhibidores"),
        ):
            for k in range(3):
                reg = cal.registrar_limpieza(
                    self.dep[
                        {"Sala de despiece": "sala_despiece", "Panadería": "panaderia", "Verdulería": "verduleria"}[
                            nombre
                        ]
                    ],
                    poes,
                    self.usuarios["carnicero"],
                    fecha_hora=timezone.now() - datetime.timedelta(days=k, hours=2),
                )
                if k:
                    cal.verificar_limpieza(reg, u, conforme=True)
        cal.cargar_documento(
            "habilitacion",
            "Habilitación municipal del local",
            u,
            de_la_empresa=True,
            vence_el=hoy + datetime.timedelta(days=210),
        )
        cal.cargar_documento(
            "certificado",
            "Certificado sanitario SENASA del frigorífico",
            u,
            proveedor=self.proveedores["Carnes"],
            vence_el=hoy + datetime.timedelta(days=15),
        )
        cal.cargar_documento(
            "analisis",
            "Análisis microbiológico de agua de red",
            u,
            de_la_empresa=True,
            vence_el=hoy + datetime.timedelta(days=60),
        )
        cal.cargar_documento(
            "certificado",
            "Certificado de habilitación avícola",
            u,
            proveedor=self.proveedores["Aves"],
            vence_el=hoy + datetime.timedelta(days=120),
        )
        self.log("Inspecciones, no conformidades, limpieza POES y documentos.")

    def _trazabilidad_y_alertas(self):
        from apps.calidad.services import revisar_alertas
        from apps.inventario.services import conciliar_stock
        from apps.lotes.models import Lote
        from apps.trazabilidad.services import iniciar_retiro

        lote = (
            Lote.objects.filter(producto__categoria__nombre="Fiambres envasados", estado="disponible")
            .order_by("-fecha_recepcion")
            .first()
        )
        if lote:
            iniciar_retiro(lote, "Alerta del proveedor: posible falla de sellado del envase", self.usuarios["calidad"])
        conciliar_stock()
        revisar_alertas()
        self.log("Retiro preventivo, conciliación de stock y alertas.")

    # ------------------------------------------------------------------------------------------------------------

    def _resumen(self):
        from apps.inventario.models import LineaMovimiento, MovimientoStock, Stock
        from apps.lotes.models import Lote
        from apps.productos.models import Producto

        with empresa_activa(self.empresa):
            datos = {
                "productos": Producto.objects.count(),
                "movimientos": MovimientoStock.objects.count(),
                "líneas de movimiento": LineaMovimiento.objects.count(),
                "lotes": Lote.objects.count(),
                "filas de stock": Stock.objects.exclude(cantidad=0).count(),
            }
        self.stdout.write(
            self.style.SUCCESS("Súper Impulso listo · " + " · ".join(f"{k}: {v}" for k, v in datos.items()))
        )
        self.stdout.write(f"Usuarios ({CLAVE}): " + ", ".join(f"{a}@{DOMINIO}" for a, *_ in USUARIOS))
