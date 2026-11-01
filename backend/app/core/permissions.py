from fastapi import HTTPException, status

def verify_shop_owner_or_manager(role: str):
    if role not in ("owner", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only shop owners or managers can perform this operation."
        )
