from ..agents.schemas import AgentStatus
from ..models import Service
from .schemas import ServiceStatus


def compute_status(service: Service, agent_status: AgentStatus) -> ServiceStatus:
    """Four-state service status.

    - agent not online -> ``unknown`` (we cannot tell what the service is doing)
    - service no longer reported by the agent -> ``stopped``
    - reported but its /metrics scrape failed -> ``degraded``
    - otherwise ``running``
    """
    if agent_status != "online":
        return "unknown"
    if not service.active:
        return "stopped"
    if not service.scrape_ok:
        return "degraded"
    return "running"
