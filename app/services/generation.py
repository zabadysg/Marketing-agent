from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.graph import generation_graph
from app.database import AsyncSessionLocal
from app.models.action_log import ActionLog
from app.models.content_plan import ContentPlan
from app.models.enums import PlanStatus
from app.models.post import Post


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

            final_state = await generation_graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": plan_id}},
            )

            # Persist posts and action logs in one transaction
            result = await db.execute(
                select(ContentPlan).where(ContentPlan.id == plan_id)
            )
            plan = result.scalar_one()

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

        except Exception as exc:
            async with session_factory() as err_db:
                result = await err_db.execute(
                    select(ContentPlan).where(ContentPlan.id == plan_id)
                )
                plan = result.scalar_one_or_none()
                if plan:
                    plan.status = PlanStatus.failed.value
                    plan.error = str(exc)
                    await err_db.commit()
