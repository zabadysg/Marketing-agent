import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents import graph as _graph_mod
from app.database import AsyncSessionLocal
from app.models.action_log import ActionLog
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus
from app.models.post import Post
from app.services import event_bus

logger = logging.getLogger(__name__)


async def run_generation(
    plan_id: str,
    workspace_id: str,
    brand_profile: dict,
    goal: str | None,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> None:
    """Background task: invoke the LangGraph workflow then persist results."""
    async with session_factory() as db:
        try:
            await event_bus.emit(plan_id, {"type": "status", "message": "Starting generation…"})
            initial_state = {
                "brand_profile": brand_profile,
                "goal": goal,
                "ideas": [],
                "current_idx": 0,
                "revision_count": 0,
                "current_content": None,
                "finished_posts": [],
                "action_logs": [],
                "workspace_id": workspace_id,
                "plan_id": plan_id,
            }

            final_state = await _graph_mod.generation_graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": plan_id}},
            )

            # Persist posts and action logs in one transaction.
            # Idempotency guard: if posts already exist (crash-after-commit scenario
            # where ainvoke returns from END checkpoint but status never updated),
            # skip the insert and just mark the plan ready.
            result = await db.execute(
                select(ContentPlan).where(ContentPlan.id == plan_id)
            )
            plan = result.scalar_one()

            existing = await db.execute(
                select(Post.id).where(Post.plan_id == plan_id).limit(1)
            )
            if existing.scalar_one_or_none() is None:
                for post_dict in final_state["finished_posts"]:
                    db.add(
                        Post(
                            plan_id=plan_id,
                            workspace_id=workspace_id,
                            day=post_dict["day"],
                            theme=post_dict["theme"],
                            format=post_dict["format"],
                            angle=post_dict["angle"],
                            content=post_dict["content"],
                            hashtags=post_dict.get("hashtags", []),
                            suggested_time=post_dict.get("suggested_time", ""),
                        )
                    )

                for log_dict in final_state["action_logs"]:
                    db.add(
                        ActionLog(
                            workspace_id=workspace_id,
                            actor=log_dict["actor"],
                            action=log_dict["action"],
                            payload=log_dict.get("payload", {}),
                            result=log_dict.get("result"),
                        )
                    )

            plan.status = PlanStatus.ready.value
            await db.commit()
            await event_bus.emit(plan_id, {"type": "done", "plan_id": plan_id})

        except Exception as exc:
            logger.exception("Generation failed for plan %s", plan_id)
            await event_bus.emit(plan_id, {"type": "error", "message": "Generation failed. Check server logs for details."})
            async with session_factory() as err_db:
                result = await err_db.execute(
                    select(ContentPlan).where(ContentPlan.id == plan_id)
                )
                plan = result.scalar_one_or_none()
                if plan:
                    plan.status = PlanStatus.failed.value
                    plan.error = type(exc).__name__
                    await err_db.commit()
        finally:
            event_bus.close(plan_id)
