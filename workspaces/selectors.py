from .models import Membership


def current_membership(user):
    if not user.is_authenticated:
        return None
    return (
        Membership.objects.select_related("workspace")
        .filter(user=user, is_active=True)
        .order_by("workspace__name", "id")
        .first()
    )


def current_shop(user):
    membership = current_membership(user)
    if not membership:
        return None
    return membership.workspace.shops.filter(is_active=True).order_by("id").first()
