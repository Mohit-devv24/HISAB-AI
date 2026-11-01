import pytest
from core.permissions import verify_shop_owner_or_manager
from fastapi import HTTPException

def test_verify_shop_owner_or_manager():
    verify_shop_owner_or_manager("owner")
    verify_shop_owner_or_manager("manager")
    with pytest.raises(HTTPException):
        verify_shop_owner_or_manager("staff")
