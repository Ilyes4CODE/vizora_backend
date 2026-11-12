from .models import AuditLog


def record(action, message, request=None, user=None, target=None):
    actor = user
    if actor is None and request is not None and request.user.is_authenticated:
        actor = request.user
    AuditLog.objects.create(
        user=actor,
        user_name=actor.full_name if actor else "",
        action=action,
        message=message[:255],
        target_type=target.__class__.__name__ if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        ip_address=request.META.get("REMOTE_ADDR") if request is not None else None,
    )
