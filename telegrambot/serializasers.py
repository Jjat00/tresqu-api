from langchain_core.pydantic_v1 import BaseModel, Field
from datetime import datetime


class ExpenseData(BaseModel):
    """Información sobre un gasto financiero."""

    amount: float = Field(description="Cantidad numérica del gasto")
    currency: str = Field(
        description="Código ISO de moneda (COP, MXN, USD, etc.). Si no se proporciona, se usará la moneda por defecto del usuario.",
        default="")
    category: str = Field(description="Categoría (Comida, Transporte, etc.)")
    spent_at: str = Field(
        description="Fecha del gasto en formato YYYY-MM-DD", default=datetime.now().strftime("%Y-%m-%d"))
    note: str = Field(description="Descripción del gasto")
