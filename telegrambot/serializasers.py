from pydantic import BaseModel, Field, field_validator
from typing import Optional
import datetime as _dt


class ExpenseData(BaseModel):
    """Datos de un gasto extraído de un mensaje de usuario"""
    amount: float = Field(..., description="Cantidad de dinero gastada.")
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
    category: str = Field(..., description="Categoría del gasto.")
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
    amount: float = Field(..., description="Cantidad de dinero recibida.")
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
    category: str = Field(..., description="Categoría del ingreso.")
    received_at: Optional[str] = Field(
        None, description="Fecha del ingreso en formato YYYY-MM-DD.")
    note: Optional[str] = Field(
        None, description="Notas adicionales o descripción del ingreso.")
