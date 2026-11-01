from fastapi import HTTPException, status

class EntityNotFoundError(HTTPException):
    def __init__(self, detail: str = "Requested entity was not found."):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

class FinancialValidationError(HTTPException):
    def __init__(self, detail: str = "Financial validation error."):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
