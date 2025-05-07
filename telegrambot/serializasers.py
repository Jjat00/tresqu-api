from pydantic import BaseModel, Field, field_validator
from typing import Optional
import datetime as _dt


class ExpenseData(BaseModel):
    """Datos de un gasto extraído de un mensaje de usuario"""
    amount: float = Field(..., description="Cantidad de dinero gastada.")
    currency: str = Field(...,
                          description="Código de moneda (USD, EUR, COP, etc.).")
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
    currency: str = Field(...,
                          description="Código de moneda (USD, EUR, COP, etc.).")
    category: str = Field(..., description="Categoría del ingreso.")
    received_at: Optional[str] = Field(
        None, description="Fecha del ingreso en formato YYYY-MM-DD.")
    note: Optional[str] = Field(
        None, description="Notas adicionales o descripción del ingreso.")
