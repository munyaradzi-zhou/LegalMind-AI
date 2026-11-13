"""Agent management endpoints for inspecting and configuring agents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from legalmind.agents.orchestrator import Orchestrator

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents() -> dict[str, Any]:
    """List all available specialist agents and their capabilities."""
    agents = Orchestrator().list_agents()

    return {
        "agents": [
            {
                "name": agent["name"],
                "description": agent["description"],
                "tools": agent["tools"],
            }
            for agent in agents
        ],
        "total": len(agents),
    }


@router.get("/{agent_name}")
async def get_agent_info(agent_name: str) -> dict[str, Any]:
    """Get detailed information about a specific agent."""
    agents = Orchestrator().list_agents()

    agent = next((a for a in agents if a["name"] == agent_name), None)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    return {
        "name": agent["name"],
        "description": agent["description"],
        "tools": agent["tools"],
        "system_prompt_preview": agent["system_prompt"][:200] + "...",
    }
