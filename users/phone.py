"""Cómo se escribe un teléfono y qué formas son la misma persona.

Vive en ``users`` porque el teléfono es lo único que identifica a la misma
persona en Telegram, WhatsApp y la web. Cada canal lo entrega con un formato
distinto (WhatsApp manda el ``wa_id``, Telegram el contacto compartido), así
que si cada app lo normaliza a su manera la misma persona acaba con dos
cuentas y sus gastos partidos en dos.

El caso espinoso es México: el móvil se marca con o sin un "1" después del
código de país (``521...`` frente a ``52...``) y ambas formas circulan. Se
guarda siempre la de WhatsApp (``521``), que es la que llega de Meta, y se
busca por las dos con :func:`phone_variants`.
"""

# Códigos de área móviles mexicanos (los dos dígitos que siguen al 52)
MEXICO_MOBILE_AREA_CODES = frozenset([
    '21', '22', '24', '25', '26', '27', '28', '29', '31', '32', '33', '34',
    '35', '36', '37', '38', '43', '44', '45', '46', '47', '48', '49', '52',
    '53', '55', '56', '58', '59', '61', '62', '63', '64', '65', '66', '67',
    '68', '69', '71', '72', '73', '74', '75', '76', '77', '78', '81', '83',
    '84', '86', '87', '88', '89', '92', '93', '94', '95', '96', '97', '98',
    '99',
])


def normalize_phone_number(phone_number):
    """Deja el número en la forma canónica: sin "+", sin separadores y, en
    México, con el "1" que exige WhatsApp.

    Ejemplos:
        "+52 55 2899 5412" -> "5215528995412"
        "525528995412"     -> "5215528995412"
        "5215528995412"    -> "5215528995412"
        "+56 9 4247 9733"  -> "56942479733"
    """
    if not phone_number:
        return None

    normalized = phone_number.strip().lstrip('+')
    for sobrante in (' ', '-', '(', ')'):
        normalized = normalized.replace(sobrante, '')

    if not normalized.startswith('52'):
        return normalized

    # 12 dígitos: le falta el "1" (525528995412)
    if len(normalized) == 12 and normalized[2:4] in MEXICO_MOBILE_AREA_CODES:
        return '521' + normalized[2:]

    # 13 dígitos sin el "1" en su sitio: se reordena
    if (len(normalized) == 13 and normalized[2] != '1'
            and normalized[2:4] in MEXICO_MOBILE_AREA_CODES):
        return '521' + normalized[2:]

    return normalized


def phone_variants(phone_number):
    """Formas equivalentes del mismo número, para buscar una cuenta ya creada.

    Un mexicano registrado desde WhatsApp está guardado como ``521...``; su
    contacto de Telegram llega como ``52...``. Buscar solo por la forma
    canónica le crearía una segunda cuenta.
    """
    normalized = normalize_phone_number(phone_number)
    if not normalized:
        return []

    variantes = [normalized]

    if normalized.startswith('521') and len(normalized) == 13:
        variantes.append('52' + normalized[3:])
    elif normalized.startswith('52') and len(normalized) == 12:
        variantes.append('521' + normalized[2:])

    return variantes
