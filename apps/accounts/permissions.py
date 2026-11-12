from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core.exceptions import ApiError
from apps.core.models import SecuritySettings


def sections_for(view, method):
    action_name = getattr(view, "action", None)
    action_sections = getattr(view, "action_sections", {})
    if action_name in action_sections:
        return action_sections[action_name]
    if method in SAFE_METHODS and hasattr(view, "read_sections"):
        return view.read_sections
    section = getattr(view, "section", None)
    if isinstance(section, (tuple, list)):
        return tuple(section)
    return (section,) if section else ()


class HasSection(BasePermission):
    message = "ليس لديك صلاحية الوصول إلى هذا القسم"

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if SecuritySettings.load().two_factor_required and not user.totp_enabled:
            raise ApiError("يجب تفعيل التحقق بخطوتين أولاً", "two_factor_setup_required", 403)
        sections = sections_for(view, request.method)
        return not sections or any(user.has_section(section) for section in sections)
