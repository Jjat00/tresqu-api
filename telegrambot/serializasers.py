from pydantic import BaseModel, Field, field_validator
from typing import Optional
import datetime as _dt


class ExpenseData(BaseModel):
    """Datos de un gasto extraído de un mensaje de usuario"""
    amount: float = Field(
        ...,
        description=(
            "Cantidad de dinero gastada, ya expandida: abreviaturas como '20k', "
            "'1.5M' o '90 mil' se convierten a 20000, 1500000, 90000. Aplica la "
            "regla de ESCALA DE MONTOS COLOQUIALES del prompt: en monedas de alta "
            "denominación (COP, CLP...), un monto implausiblemente bajo para lo "
            "comprado significa miles ('gasté 90 en una camisa' → 90000)."
        ),
    )
    currency: Optional[str] = Field(
        None,
        description=(
            "Código de moneda SOLO si el usuario la menciona de forma EXPLÍCITA e "
            "inequívoca (código ISO como USD/EUR/ARS/COP, o un nombre claro: "
            "'dólares'→USD, 'euros'→EUR, 'pesos colombianos'→COP, 'pesos argentinos'→ARS). "
            "NUNCA la infieras ni adivines: si el usuario solo dice 'pesos' a secas u otra "
            "palabra ambigua, o no menciona moneda, déjalo en null. El sistema usará la "
            "moneda por defecto del usuario."
        ),
    )
    category: str = Field(
        ...,
        description=(
            "Categoría TEMÁTICA del gasto. Debe ser, siempre que alguna encaje, una de "
            "las categorías existentes del usuario provistas en el prompt. NUNCA uses el "
            "nombre literal del ítem o actividad (no 'cena' ni 'taxi', sino 'Alimentación' "
            "o 'Transporte y Movilidad'). Solo propón una categoría nueva si ninguna "
            "existente encaja."
        ),
    )
    spent_at: Optional[str] = Field(
        None, description="Fecha del gasto en formato YYYY-MM-DD.")
    note: Optional[str] = Field(
        None, description="Notas adicionales o descripción del gasto.")

    # normalizamos fecha
    @field_validator("spent_at")
    @classmethod
    def _validate_date(cls, v):
        if v is None:
            return None
        # lanzará ValueError si es inválida
        _dt.datetime.strptime(v, "%Y-%m-%d")
        return v


class IncomeData(BaseModel):
    """Datos de un ingreso extraído de un mensaje de usuario"""
    amount: float = Field(
        ...,
        description=(
            "Cantidad de dinero recibida, ya expandida: abreviaturas como '20k', "
            "'1.5M' o '200 mil' se convierten a 20000, 1500000, 200000. Aplica la "
            "regla de ESCALA DE MONTOS COLOQUIALES del prompt: en monedas de alta "
            "denominación (COP, CLP...), un monto implausiblemente bajo para el "
            "concepto significa miles ('me pagaron 200 de un proyecto' → 200000)."
        ),
    )
    currency: Optional[str] = Field(
        None,
        description=(
            "Código de moneda SOLO si el usuario la menciona de forma EXPLÍCITA e "
            "inequívoca (código ISO como USD/EUR/ARS/COP, o un nombre claro: "
            "'dólares'→USD, 'euros'→EUR, 'pesos colombianos'→COP, 'pesos argentinos'→ARS). "
            "NUNCA la infieras ni adivines: si el usuario solo dice 'pesos' a secas u otra "
            "palabra ambigua, o no menciona moneda, déjalo en null. El sistema usará la "
            "moneda por defecto del usuario."
        ),
    )
    category: str = Field(
        ...,
        description=(
            "Categoría TEMÁTICA del ingreso. Debe ser, siempre que alguna encaje, una de "
            "las categorías de ingreso existentes del usuario provistas en el prompt. "
            "NUNCA uses el nombre literal de la fuente puntual; clasifícala en su categoría "
            "temática. Solo propón una categoría nueva si ninguna existente encaja."
        ),
    )
    received_at: Optional[str] = Field(
        None, description="Fecha del ingreso en formato YYYY-MM-DD.")
    note: Optional[str] = Field(
        None, description="Notas adicionales o descripción del ingreso.")
