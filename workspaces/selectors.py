from .models import Membership


ACTIVE_SHOP_SESSION_KEY = "active_shop_id"
ALL_SHOPS_VALUE = "all"


def current_membership(user):
    if not user.is_authenticated:
        return None
    return (
        Membership.objects.select_related("workspace")
        .filter(user=user, is_active=True)
        .order_by("workspace__name", "id")
        .first()
    )


def available_shops(user):
    membership = current_membership(user)
    if not membership:
        return []
    return list(membership.workspace.shops.filter(is_active=True).order_by("name", "id"))


def shop_selection(request):
    membership = current_membership(request.user)
    shops = available_shops(request.user)
    if not shops:
        return membership, shops, None, False

    selected = request.session.get(ACTIVE_SHOP_SESSION_KEY)
    if selected == ALL_SHOPS_VALUE:
        return membership, shops, None, True

    selected_shop = next((shop for shop in shops if str(shop.id) == str(selected)), None)
    if selected_shop is None:
        selected_shop = shops[0]
        request.session[ACTIVE_SHOP_SESSION_KEY] = selected_shop.id
    return membership, shops, selected_shop, False
