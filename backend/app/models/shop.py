# Shop multi-tenancy model representation
from models import User
Shop = User # In Hisab AI, user/store multi-tenancy maps directly to user-isolated ledger stores
__all__ = ["Shop"]
