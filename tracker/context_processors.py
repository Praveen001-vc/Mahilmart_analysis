from .access_control import get_resolved_permissions


def permission_context(request):
    return {
        "perm": get_resolved_permissions(getattr(request, "user", None)),
    }
