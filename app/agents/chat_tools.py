"""Chat agent tools: brand knowledge search, web search, draft post creation, plan generation."""
import asyncio
import logging
import uuid

from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import AsyncSessionLocal
from app.models.enums import PlanStatus, PostStatus
from app.models.content_plan import ContentPlan
from app.models.post import Post
from app.services.chat import get_or_create_chat_draft_plan
from app.services.event_bus import create as bus_create
from app.services.knowledge_search import search_knowledge

logger = logging.getLogger(__name__)


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Synchronous DuckDuckGo search — run via asyncio.to_thread."""
    from ddgs import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))

# Strong reference to prevent asyncio GC of fire-and-forget tasks.
_background_tasks: set[asyncio.Task] = set()


def make_chat_tools(
    workspace_id: str,
    brand_profile: dict,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> tuple[list, dict]:
    """Return (tools_list, tool_map) for the agentic loop.

    tool_map is {tool_name: tool_callable} used to dispatch tool calls.
    """

    @tool
    async def web_search(query: str) -> str:
        """Search the web for real-time information: competitors, market trends, industry news,
        pricing benchmarks, social media best practices, or any topic not in the brand knowledge base.

        Use this whenever the user asks about something external — competitors, market data,
        platform algorithms, trending topics, or anything that requires up-to-date information.
        Prefer specific queries (e.g. "منافسو شركة X في مصر 2024") over vague ones.
        """
        try:
            results = await asyncio.to_thread(_ddg_search, query, 6)
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for %r: %s", query, exc)
            return f"Web search temporarily unavailable: {exc}"

        if not results:
            return "No web results found for that query."

        lines = [f"Web search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")[:300]
            href = r.get("href", "")
            lines.append(f"{i}. **{title}**\n   {body}\n   Source: {href}")
        return "\n\n".join(lines)

    @tool
    async def search_brand_knowledge(query: str) -> str:
        """Search brand knowledge documents for specific brand information.

        Use this when you need more detail than what's already in context, e.g.,
        specific product specs, pricing language, or exact messaging guidelines.
        """
        async with session_factory() as db:
            chunks = await search_knowledge(query, workspace_id, db, k=3)
        if not chunks:
            return "No relevant knowledge found for that query."
        return "\n---\n".join(c.text for c in chunks)

    @tool
    async def create_draft_post(
        content: str,
        hashtags: list[str],
        suggested_time: str,
        theme: str,
    ) -> dict:
        """Draft a social media post and save it to the workspace draft queue.

        The user can then review and submit it for approval from the UI.
        Returns the post_id and a content preview.
        """
        async with session_factory() as db:
            plan = await get_or_create_chat_draft_plan(db, workspace_id)
            post = Post(
                id=str(uuid.uuid4()),
                plan_id=plan.id,
                workspace_id=workspace_id,
                day=0,
                theme=theme,
                format="post",
                angle="Chat draft",
                content=content,
                hashtags=hashtags,
                suggested_time=suggested_time,
                status=PostStatus.draft.value,
            )
            db.add(post)
            await db.commit()
            await db.refresh(post)
        preview = content[:100] + ("…" if len(content) > 100 else "")
        return {"post_id": post.id, "preview": preview}

    @tool
    async def trigger_plan_generation(goal: str) -> str:
        """Start a full 7-day content plan generation in the background.

        Returns the plan_id so the user can track it in the Plans section.
        """
        from app.services.generation import run_generation

        plan_id = str(uuid.uuid4())
        async with session_factory() as db:
            plan = ContentPlan(
                id=plan_id,
                workspace_id=workspace_id,
                goal=goal,
                status=PlanStatus.generating.value,
            )
            db.add(plan)
            await db.commit()

        bus_create(plan_id)
        task = asyncio.create_task(
            run_generation(plan_id, workspace_id, brand_profile, goal, session_factory)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return f"Plan generation started. Plan ID: {plan_id}"

    tools = [web_search, search_brand_knowledge, create_draft_post, trigger_plan_generation]
    tool_map = {t.name: t for t in tools}
    return tools, tool_map
