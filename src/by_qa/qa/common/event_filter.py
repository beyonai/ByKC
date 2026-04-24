"""Event filter for QA stream events."""

from by_qa.qa.common.models import StreamEvent


class EventFilter:
    """Filter stream events by role whitelist, tracking visible instance ids.

    If visible_roles is None, all events pass through (no role filtering),
    but parent_id cleanup still runs.
    """

    def __init__(self, visible_roles: dict[str, list[str] | None] | None):
        self.visible_roles = visible_roles
        self._instance_role_map: dict[str, str] = {}
        self._visible_instance_ids: set[str] = set()

    def _is_event_visible(self, event: StreamEvent) -> bool:
        if self.visible_roles is None:
            return True
        allowed_event_types = self.visible_roles.get(event.role)
        if event.role not in self.visible_roles:
            return False
        if allowed_event_types is None:
            return True
        return event.type.value in allowed_event_types

    def filter_event(self, event: StreamEvent) -> StreamEvent | None:
        role = event.role
        instance_id = event.instance_id
        if instance_id:
            self._instance_role_map[instance_id] = role or ""
        if not self._is_event_visible(event):
            return None
        if instance_id:
            self._visible_instance_ids.add(instance_id)
        if event.parent_ids:
            event.parent_ids = [
                parent_id
                for parent_id in event.parent_ids
                if parent_id in self._visible_instance_ids
            ]
        return event


__all__ = ["EventFilter"]
