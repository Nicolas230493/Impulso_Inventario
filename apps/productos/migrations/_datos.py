from decimal import Decimal

UNIDADES = [
    ("kg", "Kilogramo", "peso", Decimal("1")),
    ("g", "Gramo", "peso", Decimal("0.001")),
    ("un", "Unidad", "unidad", Decimal("1")),
    ("l", "Litro", "volumen", Decimal("1")),
    ("ml", "Mililitro", "volumen", Decimal("0.001")),
    ("cajon", "Cajón", "unidad", None),
    ("m", "Metro", "longitud", Decimal("1")),
    ("cm", "Centímetro", "longitud", Decimal("0.01")),
]
