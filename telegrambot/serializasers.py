from pydantic import BaseModel, Field, field_validator
from typing import Optional
import datetime as _dt


class ExpenseData(BaseModel):
    """Estructura de un gasto individual."""
    amount: float = Field(..., gt=0, description="Monto numérico")
    currency: str = Field(..., min_length=3, max_length=5,
                          description="Código ISO‑4217, ej: COP")
    category: str = Field(..., description="Categoría del gasto")
    spent_at: Optional[str] = Field(
        None, description="Fecha YYYY‑MM‑DD o None → hoy")
    note: Optional[str] = Field(None, description="Nota del gasto")
    description: Optional[str] = Field(
        None, description="Descripción del gasto")

    # normalizamos fecha
    @field_validator("spent_at")
    @classmethod
    def _validate_date(cls, v):
        if v is None:
            return None
        # lanzará ValueError si es inválida
        _dt.datetime.strptime(v, "%Y-%m-%d")
        return v
