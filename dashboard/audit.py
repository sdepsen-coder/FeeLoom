import logging

from .models import AuditEvent


logger = logging.getLogger("feeloom.audit")


def record_audit(
    request, *, workspace, action, shop=None, user=None, summary="", metadata=None
):
    actor = user or (request.user if request.user.is_authenticated else None)
    request_id = getattr(request, "request_id", "")
    event = AuditEvent.objects.create(
        workspace=workspace,
        shop=shop,
        user=actor,
        action=action,
        summary=summary[:255],
        metadata=metadata or {},
        request_id=request_id,
    )
    logger.info(
        "workspace_audit",
        extra={
            "action": action,
            "workspace_id": workspace.id,
            "shop_id": shop.id if shop else None,
            "user_id": actor.id if actor else None,
        },
    )
    return event
