class ShopService:
    @staticmethod
    def get_shop_info(user):
        return {"shop_name": user.shop_name, "owner": user.full_name, "email": user.email}
