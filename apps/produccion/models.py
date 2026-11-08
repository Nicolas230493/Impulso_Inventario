"""Motor genérico de transformación: una OrdenTransformacion con N entradas y N salidas (Fase 3)."""

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.nucleo.decimales import CampoCantidad, CampoCostoUnitario, CampoImporte, CampoPorcentaje
from apps.nucleo.models import ModeloEmpresa


class TipoTransformacion(models.TextChoices):
    DESPOSTE = "desposte", "Desposte"
    ELABORACION = "elaboracion", "Elaboración"
    ARMADO = "armado", "Armado de kit"
    DESARMADO = "desarmado", "Desarmado de kit"


class MetodoDistribucion(models.TextChoices):
    PESO = "peso", "A — Por peso"
    VALOR = "valor", "B — Por valor relativo de venta"


class PlantillaTransformacion(ModeloEmpresa):
    familia = models.SlugField(max_length=60, help_text="Identifica todas las versiones de una misma plantilla")
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=15, choices=TipoTransformacion.choices, default=TipoTransformacion.DESPOSTE)
    producto_entrada = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    version = models.PositiveIntegerField(default=1)
    vigente_desde = models.DateField(default=timezone.localdate)
    activa = models.BooleanField(default=True, help_text="Solo la versión vigente está activa")
    historial = HistoricalRecords()

    class Meta:
        ordering = ["nombre", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "familia", "version"], name="plantilla_version_unica")
        ]

    def __str__(self):
        return f"{self.nombre} v{self.version}"

    @property
    def merma_estandar_pct(self):
        return sum(
            (linea.rendimiento_estandar for linea in self.lineas.all() if linea.tipo_resultado == TipoResultado.MERMA),
            0,
        )


class TipoResultado(models.TextChoices):
    CORTE = "corte", "Corte"
    SUBPRODUCTO = "subproducto", "Subproducto"
    MERMA = "merma_normal", "Merma normal"
    RESIDUO = "residuo", "Residuo"


class CriterioCosto(models.TextChoices):
    PARTICIPA = "participa", "Participa del reparto"
    RECUPERO = "recupero", "Valor de recupero fijo"
    CERO = "cero", "Costo cero"


class LineaPlantilla(ModeloEmpresa):
    plantilla = models.ForeignKey(PlantillaTransformacion, on_delete=models.CASCADE, related_name="lineas")
    producto = models.ForeignKey(
        "productos.Producto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Vacío solo para la línea de merma normal",
    )
    tipo_resultado = models.CharField(max_length=15, choices=TipoResultado.choices, default=TipoResultado.CORTE)
    rendimiento_estandar = CampoPorcentaje()
    tolerancia = CampoPorcentaje(help_text="± puntos porcentuales")
    precio_referencia = CampoImporte(help_text="$/kg para el método B")
    criterio_costo = models.CharField(max_length=10, choices=CriterioCosto.choices, default=CriterioCosto.PARTICIPA)
    valor_recupero = CampoCostoUnitario(help_text="$/kg si el criterio es valor de recupero fijo")
    genera_lote = models.BooleanField(default=True)
    genera_unidad = models.BooleanField(
        default=False,
        help_text="El resultado es una pieza identificada que sigue en otra etapa (ej.: cuarto de una media res)",
    )
    deposito_destino = models.ForeignKey(
        "depositos.Deposito", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return f"{self.producto or self.get_tipo_resultado_display()} {self.rendimiento_estandar} %"


class EstadoOrden(models.TextChoices):
    BORRADOR = "borrador", "Borrador"
    RESERVADA = "reservada", "Materia prima reservada"
    EN_PROCESO = "en_proceso", "En proceso"
    PESAJE_FINAL = "pesaje_final", "Pendiente de pesaje final"
    CONTROL = "control", "Pendiente de control"
    APROBADA = "aprobada", "Aprobada (falta segunda firma)"
    STOCK_ACTUALIZADO = "stock_actualizado", "Stock actualizado"
    CERRADA = "cerrada", "Cerrada"
    CANCELADA = "cancelada", "Cancelada"
    REVERTIDA = "revertida", "Revertida"


E = EstadoOrden
TRANSICIONES_ORDEN = {
    E.BORRADOR: (E.RESERVADA, E.CANCELADA),
    E.RESERVADA: (E.EN_PROCESO, E.CANCELADA),
    E.EN_PROCESO: (E.PESAJE_FINAL, E.CANCELADA),
    E.PESAJE_FINAL: (E.CONTROL, E.EN_PROCESO, E.CANCELADA),
    E.CONTROL: (E.APROBADA, E.STOCK_ACTUALIZADO, E.PESAJE_FINAL, E.CANCELADA),
    E.APROBADA: (E.STOCK_ACTUALIZADO,),
    E.STOCK_ACTUALIZADO: (E.CERRADA, E.REVERTIDA),
    E.CERRADA: (E.REVERTIDA,),
}
ESTADOS_CANCELABLES = (E.BORRADOR, E.RESERVADA, E.EN_PROCESO, E.PESAJE_FINAL, E.CONTROL)


class OrdenTransformacion(ModeloEmpresa):
    numero = models.CharField(max_length=30, blank=True, db_index=True)
    tipo = models.CharField(max_length=15, choices=TipoTransformacion.choices, default=TipoTransformacion.DESPOSTE)
    plantilla = models.ForeignKey(PlantillaTransformacion, on_delete=models.PROTECT, related_name="ordenes")
    sucursal = models.ForeignKey("empresas.Sucursal", on_delete=models.PROTECT, related_name="+")
    deposito_origen = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    deposito_destino = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    operario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    segunda_firma = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    estado = models.CharField(max_length=20, choices=EstadoOrden.choices, default=EstadoOrden.BORRADOR)
    inicio = models.DateTimeField(null=True, blank=True)
    fin = models.DateTimeField(null=True, blank=True)
    temperatura_sector = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    metodo_distribucion = models.CharField(max_length=10, choices=MetodoDistribucion.choices)
    observaciones = models.TextField(blank=True)
    comentario_supervisor = models.TextField(blank=True)
    requiere_doble_firma = models.BooleanField(default=False)
    costo_conjunto = CampoImporte()
    perdida_periodo = CampoImporte()
    movimiento = models.OneToOneField(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    movimiento_reversion = models.OneToOneField(
        "inventario.MovimientoStock", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    receta = models.ForeignKey(
        "elaboracion.Receta", on_delete=models.PROTECT, null=True, blank=True, related_name="ordenes"
    )
    tandas = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    costo_proceso = CampoImporte(help_text="Costos de proceso asignados al aprobar (Fase 4)")

    class Meta:
        ordering = ["-creado_en", "-id"]
        verbose_name_plural = "órdenes de transformación"

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero or '(borrador)'}"


class EntradaTransformacion(ModeloEmpresa):
    orden = models.ForeignKey(OrdenTransformacion, on_delete=models.CASCADE, related_name="entradas")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    unidades = models.PositiveIntegerField(default=0, help_text="Cantidad de pollos")
    peso_reservado = CampoCantidad()
    peso_neto = CampoCantidad(help_text="Suma de pesajes de entrada (o el reservado si no se pesó)")
    costo_unitario = CampoCostoUnitario(help_text="Costo del lote al momento de reservar/aprobar")
    unidad_origen = models.ForeignKey(
        "produccion.UnidadOrigen", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    es_insumo = models.BooleanField(
        default=False, help_text="Empaque o insumo (bandeja, film): suma costo y descuenta stock, no entra al balance"
    )
    cantidad_teorica = CampoCantidad(help_text="Elaboración: cantidad que indica la receta para las tandas")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.producto} {self.lote or ''} {self.peso_neto}"


class FuentePesaje(models.TextChoices):
    MANUAL = "manual", "Manual"
    BALANZA = "balanza", "Balanza"


class SalidaTransformacion(ModeloEmpresa):
    orden = models.ForeignKey(OrdenTransformacion, on_delete=models.CASCADE, related_name="salidas")
    linea_plantilla = models.ForeignKey(
        LineaPlantilla, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    tipo_resultado = models.CharField(max_length=15, choices=TipoResultado.choices, default=TipoResultado.CORTE)
    unidades = models.PositiveIntegerField(default=0)
    peso_neto = CampoCantidad()
    deposito_destino = models.ForeignKey("depositos.Deposito", on_delete=models.PROTECT, related_name="+")
    lote_generado = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    costo_asignado = CampoImporte()
    costo_kg = CampoCostoUnitario()
    orden_pantalla = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["orden_pantalla", "id"]

    def __str__(self):
        return f"{self.producto} {self.peso_neto}"


class Pesaje(ModeloEmpresa):
    orden = models.ForeignKey(OrdenTransformacion, on_delete=models.CASCADE, related_name="pesajes")
    entrada = models.ForeignKey(
        EntradaTransformacion, on_delete=models.CASCADE, null=True, blank=True, related_name="pesajes"
    )
    salida = models.ForeignKey(
        SalidaTransformacion, on_delete=models.CASCADE, null=True, blank=True, related_name="pesajes"
    )
    secuencia = models.PositiveIntegerField()
    unidades = models.PositiveIntegerField(default=0)
    peso_bruto = CampoCantidad()
    tara = CampoCantidad()
    peso_neto = CampoCantidad()
    fuente = models.CharField(max_length=10, choices=FuentePesaje.choices, default=FuentePesaje.MANUAL)
    dispositivo = models.CharField(max_length=80, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    hora = models.DateTimeField(default=timezone.now)
    anulado = models.BooleanField(default=False)
    motivo_anulacion = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["orden_id", "secuencia"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(entrada__isnull=False, salida__isnull=True)
                    | models.Q(entrada__isnull=True, salida__isnull=False)
                ),
                name="pesaje_entrada_o_salida",
            )
        ]


class Imputacion(models.TextChoices):
    ABSORBE = "absorbe", "Se absorbe en los productos (hasta el estándar)"
    PERDIDA = "perdida", "Pérdida del período"
    PENDIENTE = "pendiente", "Pendiente de revisión (impide aprobar)"


class CategoriaMerma(ModeloEmpresa):
    nombre = models.CharField(max_length=120)
    aprovechable = models.BooleanField(default=False)
    imputacion = models.CharField(max_length=10, choices=Imputacion.choices, default=Imputacion.ABSORBE)
    activa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "categorías de merma"

    def __str__(self):
        return self.nombre


class DiferenciaClasificada(ModeloEmpresa):
    orden = models.ForeignKey(OrdenTransformacion, on_delete=models.CASCADE, related_name="diferencias")
    categoria = models.ForeignKey(CategoriaMerma, on_delete=models.PROTECT, related_name="+")
    peso = CampoCantidad()
    observacion = models.CharField(max_length=250, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["id"]


class LoteOrigen(ModeloEmpresa):
    """Arista del grafo de trazabilidad: qué lotes (y cuántos kilos) formaron un lote resultante."""

    lote_resultante = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, related_name="origenes")
    lote_origen = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, related_name="derivados")
    orden = models.ForeignKey(OrdenTransformacion, on_delete=models.PROTECT, related_name="+")
    peso_aportado = CampoCantidad()

    class Meta:
        indexes = [models.Index(fields=["empresa", "lote_origen"]), models.Index(fields=["empresa", "lote_resultante"])]


class ResultadoOrden(ModeloEmpresa):
    """Snapshot al aprobar: rendimientos, mermas, diferencia, costos por línea, real vs estándar."""

    orden = models.OneToOneField(OrdenTransformacion, on_delete=models.CASCADE, related_name="resultado")
    peso_entrada = CampoCantidad()
    peso_aprovechable = CampoCantidad()
    peso_descartado = CampoCantidad()
    rendimiento = CampoPorcentaje()
    merma = CampoPorcentaje()
    diferencia = CampoCantidad()
    merma_anormal_kg = CampoCantidad()
    datos = models.JSONField(encoder=DjangoJSONEncoder, default=dict)


# =============================================================================================================
# Fase 4 — piezas identificadas (media res, cuarto, res porcina, horma) y costos de proceso
# =============================================================================================================


class EstadoUnidad(models.TextChoices):
    DISPONIBLE = "disponible", "Disponible"
    EN_PROCESO = "en_proceso", "En proceso"
    PROCESADA = "procesada", "Procesada"
    ANULADA = "anulada", "Anulada"


class UnidadOrigen(ModeloEmpresa):
    """Una pieza identificada dentro de un lote: permite medir el rendimiento por pieza y no solo por lote."""

    lote = models.ForeignKey("lotes.Lote", on_delete=models.PROTECT, related_name="unidades_origen")
    producto = models.ForeignKey("productos.Producto", on_delete=models.PROTECT, related_name="+")
    identificador = models.CharField(max_length=60, help_text="Tropa/garrón, número de horma, etiqueta del proveedor")
    tipo = models.CharField(max_length=40, blank=True, help_text="media res, cuarto, res porcina, horma…")
    peso = CampoCantidad(help_text="Peso al recibirla (o al salir de la etapa anterior)")
    estado = models.CharField(max_length=12, choices=EstadoUnidad.choices, default=EstadoUnidad.DISPONIBLE)
    padre = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="hijas")
    orden_origen = models.ForeignKey(
        OrdenTransformacion, on_delete=models.PROTECT, null=True, blank=True, related_name="unidades_generadas"
    )

    class Meta:
        ordering = ["-creado_en", "identificador"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "producto", "identificador"], name="unidad_origen_unica")
        ]

    def __str__(self):
        return f"{self.tipo or self.producto} {self.identificador}"

    @property
    def raiz(self):
        u = self
        while u.padre_id:
            u = u.padre
        return u


class TipoConcepto(models.TextChoices):
    DIRECTO = "directo", "Directo"
    INDIRECTO = "indirecto", "Indirecto"


class ConceptoCostoProceso(ModeloEmpresa):
    nombre = models.CharField(max_length=80)
    tipo = models.CharField(max_length=10, choices=TipoConcepto.choices, default=TipoConcepto.INDIRECTO)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [models.UniqueConstraint(fields=["empresa", "nombre"], name="concepto_proceso_unico")]

    def __str__(self):
        return self.nombre


class CriterioTarifa(models.TextChoices):
    POR_ORDEN = "orden", "Por orden"
    POR_KG = "kg", "Por kg procesado"
    POR_HORA = "hora", "Por hora de proceso"
    POR_UNIDAD = "unidad", "Por unidad producida"


class TarifaCostoProceso(ModeloEmpresa):
    concepto = models.ForeignKey(ConceptoCostoProceso, on_delete=models.PROTECT, related_name="tarifas")
    sucursal = models.ForeignKey(
        "empresas.Sucursal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="Vacío = todas las sucursales",
    )
    tipo_transformacion = models.CharField(
        max_length=15, choices=TipoTransformacion.choices, blank=True, help_text="Vacío = todas"
    )
    criterio = models.CharField(max_length=10, choices=CriterioTarifa.choices, default=CriterioTarifa.POR_KG)
    importe = CampoCostoUnitario()
    vigente_desde = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["concepto__nombre", "-vigente_desde"]

    def __str__(self):
        return f"{self.concepto} {self.importe} {self.get_criterio_display().lower()}"


class CostoProcesoOrden(ModeloEmpresa):
    orden = models.ForeignKey(OrdenTransformacion, on_delete=models.CASCADE, related_name="costos_proceso")
    concepto = models.ForeignKey(ConceptoCostoProceso, on_delete=models.PROTECT, related_name="+")
    tarifa = models.ForeignKey(TarifaCostoProceso, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    criterio = models.CharField(max_length=10, choices=CriterioTarifa.choices)
    base = CampoCantidad(help_text="kg, horas, unidades o 1 orden")
    importe = CampoImporte()

    class Meta:
        ordering = ["id"]


class CostoRealMensual(ModeloEmpresa):
    """Cierre mensual: indirectos reales del mes para comparar contra lo tarifado (no recalcula órdenes)."""

    concepto = models.ForeignKey(ConceptoCostoProceso, on_delete=models.PROTECT, related_name="+")
    periodo = models.DateField(help_text="Primer día del mes")
    importe = CampoImporte()

    class Meta:
        ordering = ["-periodo", "concepto__nombre"]
        constraints = [
            models.UniqueConstraint(fields=["empresa", "concepto", "periodo"], name="costo_real_mensual_unico")
        ]
