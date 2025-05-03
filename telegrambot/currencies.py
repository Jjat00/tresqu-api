"""
Este módulo contiene información sobre monedas según ISO 4217
"""

# Monedas comunes con bandera de país
COMMON_CURRENCIES = [
    {"code": "USD", "name": "Dólar estadounidense", "flag": "🇺🇸"},
    {"code": "EUR", "name": "Euro", "flag": "🇪🇺"},
    {"code": "GBP", "name": "Libra esterlina", "flag": "🇬🇧"},
    {"code": "JPY", "name": "Yen japonés", "flag": "🇯🇵"},
    {"code": "CNY", "name": "Yuan chino", "flag": "🇨🇳"},
    {"code": "AUD", "name": "Dólar australiano", "flag": "🇦🇺"},
    {"code": "CAD", "name": "Dólar canadiense", "flag": "🇨🇦"},
    {"code": "CHF", "name": "Franco suizo", "flag": "🇨🇭"},
    {"code": "MXN", "name": "Peso mexicano", "flag": "🇲🇽"},
    {"code": "BRL", "name": "Real brasileño", "flag": "🇧🇷"},
    {"code": "ARS", "name": "Peso argentino", "flag": "🇦🇷"},
    {"code": "COP", "name": "Peso colombiano", "flag": "🇨🇴"},
    {"code": "CLP", "name": "Peso chileno", "flag": "🇨🇱"},
    {"code": "PEN", "name": "Sol peruano", "flag": "🇵🇪"},
    {"code": "UYU", "name": "Peso uruguayo", "flag": "🇺🇾"},
    {"code": "BOB", "name": "Boliviano", "flag": "🇧🇴"},
    {"code": "VES", "name": "Bolívar soberano", "flag": "🇻🇪"},
    {"code": "CRC", "name": "Colón costarricense", "flag": "🇨🇷"},
    {"code": "DOP", "name": "Peso dominicano", "flag": "🇩🇴"},
    {"code": "GTQ", "name": "Quetzal guatemalteco", "flag": "🇬🇹"},
]

# Diccionario para validar monedas
ISO_4217_CODES = {
    "USD": "Dólar estadounidense",
    "EUR": "Euro",
    "GBP": "Libra esterlina",
    "JPY": "Yen japonés",
    "CNY": "Yuan chino",
    "AUD": "Dólar australiano",
    "CAD": "Dólar canadiense",
    "CHF": "Franco suizo",
    "MXN": "Peso mexicano",
    "BRL": "Real brasileño",
    "ARS": "Peso argentino",
    "COP": "Peso colombiano",
    "CLP": "Peso chileno",
    "PEN": "Sol peruano",
    "UYU": "Peso uruguayo",
    "BOB": "Boliviano",
    "VES": "Bolívar soberano",
    "CRC": "Colón costarricense",
    "DOP": "Peso dominicano",
    "GTQ": "Quetzal guatemalteco",
    "INR": "Rupia india",
    "RUB": "Rublo ruso",
    "ZAR": "Rand sudafricano",
    "SEK": "Corona sueca",
    "NOK": "Corona noruega",
    "DKK": "Corona danesa",
    "ILS": "Nuevo séquel israelí",
    "TRY": "Lira turca",
    "AED": "Dírham de los Emiratos Árabes Unidos",
    "SAR": "Riyal saudí",
    "HKD": "Dólar de Hong Kong",
    "SGD": "Dólar de Singapur",
    "NZD": "Dólar neozelandés",
    "THB": "Baht tailandés",
    "IDR": "Rupia indonesia",
    "MYR": "Ringgit malayo",
    "PHP": "Peso filipino",
    "KRW": "Won surcoreano",
    "TWD": "Nuevo dólar taiwanés",
    "PLN": "Złoty polaco",
    "HUF": "Forinto húngaro",
    "CZK": "Corona checa",
    "RON": "Leu rumano",
    "BGN": "Lev búlgaro",
    "HRK": "Kuna croata",
    "ISK": "Corona islandesa",
}


def is_valid_currency(code):
    """Verifica si un código de moneda es válido según ISO 4217"""
    return code.upper() in ISO_4217_CODES


def get_currency_name(code):
    """Devuelve el nombre de la moneda dado su código ISO 4217"""
    return ISO_4217_CODES.get(code.upper(), "Moneda desconocida")


def format_currency_option(currency):
    """Formatea una opción de moneda para mostrar al usuario"""
    return f"{currency['flag']} {currency['code']} - {currency['name']}"
