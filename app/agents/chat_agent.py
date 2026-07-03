"""Chat agent — manual agentic loop using langchain-core primitives.

Uses astream_events (v2) to stream tokens and detect tool calls.
full_content accumulates monotonically across all iterations so the persisted
assistant message exactly matches what was streamed token-by-token to the user.
"""
import asyncio
import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.llm import get_llm
from app.agents.chat_tools import make_chat_tools
from app.database import AsyncSessionLocal
from app.models.chat import MessageRole
from app.services import event_bus
from app.services.chat import save_message

logger = logging.getLogger(__name__)


HISTORY_WINDOW = 20
MAX_TOOL_ROUNDS = 5  # safeguard against runaway tool-calling loops

_SYSTEM_TEMPLATE = """\
You are an AI marketing assistant for the following brand.

## Brand Profile
{brand_profile}

## Relevant Brand Knowledge
{retrieved_context}

## Capabilities
- Answer questions about the brand, its voice, audience, and positioning.
- Brainstorm content ideas tailored to the brand.
- Draft a single social media post using the create_draft_post tool.
- Start a full 7-day content plan using the trigger_plan_generation tool.
- Search brand knowledge documents using the search_brand_knowledge tool.
- Search the web using the web_search tool for real-time information.

## When to use web_search
Use web_search PROACTIVELY whenever the user asks about anything external:
- Competitors or competitor analysis (always search, never say "I don't know")
- Market trends, industry news, or benchmarks
- Social media platform algorithms or best practices
- Pricing of competitors
- Trending topics or hashtags
- Any question that requires up-to-date information beyond the brand profile

For competitor questions, run multiple targeted searches (e.g. by industry + market/country).
Synthesize the search results into a clear, structured answer — don't just dump raw results.

Be concise, helpful, and always reflect the brand's tone and guidelines.
When drafting a post, confirm the platform/format if not specified.
Respond in the same language the user uses.
"""


async def run_chat_agent(
    session_id: str,
    workspace_id: str,
    user_message: str,
    history: list[dict],
    brand_profile: dict,
    retrieved_context: str,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> None:
    """Background task. Streams tokens via event_bus, saves final message to DB."""
    try:
        tools, tool_map = make_chat_tools(workspace_id, brand_profile, session_factory)
        llm_with_tools = get_llm("cheap").bind_tools(tools)

        system_prompt = _SYSTEM_TEMPLATE.format(
            brand_profile=json.dumps(brand_profile, indent=2),
            retrieved_context=retrieved_context or "No knowledge documents indexed yet.",
        )

        messages: list = [SystemMessage(content=system_prompt)]
        for h in history:
            if h["role"] == "user":
                messages.append(HumanMessage(content=h["content"]))
            else:
                messages.append(AIMessage(content=h["content"]))
        messages.append(HumanMessage(content=user_message))

        # full_content accumulates monotonically — never reset between tool rounds.
        # Every token the user sees is appended here so the persisted message
        # exactly matches the streamed output.
        full_content = ""
        tool_rounds = 0

        while tool_rounds <= MAX_TOOL_ROUNDS:
            response = None

            async for ev in llm_with_tools.astream_events(messages, version="v2"):
                etype = ev["event"]
                if etype == "on_chat_model_stream":
                    chunk = ev["data"]["chunk"]
                    raw = chunk.content
                    # Gemini 2.5 returns content as list[dict] with {"type":"text","text":"..."}
                    if isinstance(raw, list):
                        text = "".join(
                            p.get("text", "")
                            for p in raw
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    else:
                        text = raw or ""
                    if text:
                        await event_bus.emit(session_id, {"type": "token", "content": text})
                        full_content += text
                elif etype == "on_chat_model_end":
                    response = ev["data"]["output"]

            if not response or not getattr(response, "tool_calls", None):
                break

            tool_rounds += 1
            if tool_rounds > MAX_TOOL_ROUNDS:
                logger.warning("Chat agent hit MAX_TOOL_ROUNDS (%d) for session %s", MAX_TOOL_ROUNDS, session_id)
                fallback = "\n\n[Reached maximum tool-call depth. Answering from available context.]"
                full_content += fallback
                await event_bus.emit(session_id, {"type": "token", "content": fallback})
                break

            # Execute tool calls, feed results back for next iteration.
            messages.append(response)  # AIMessage with tool_calls
            for tc in response.tool_calls:
                await event_bus.emit(
                    session_id, {"type": "tool_start", "tool": tc["name"]}
                )
                tool_fn = tool_map.get(tc["name"])
                if tool_fn is None:
                    result = f"Unknown tool: {tc['name']}"
                else:
                    try:
                        result = await tool_fn.ainvoke(tc["args"])
                    except Exception as exc:
                        logger.warning("Tool %s failed: %s", tc["name"], exc)
                        result = f"Error: {exc}"
                await event_bus.emit(
                    session_id, {"type": "tool_end", "tool": tc["name"]}
                )
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tc["id"])
                )
            # DO NOT reset full_content here. Pre-tool text is already accumulated
            # and was already emitted to the user. Next iteration appends to it.

        # Persist the complete assistant message.
        async with session_factory() as db:
            await save_message(
                db,
                session_id=session_id,
                workspace_id=workspace_id,
                role=MessageRole.assistant,
                content=full_content,
            )
            await db.commit()

        await event_bus.emit(session_id, {"type": "done"})

    except asyncio.CancelledError:
        # Client disconnected mid-stream — expected, not an error.
        logger.debug("Chat agent cancelled (client disconnected) for session %s", session_id)
        raise
    except Exception as exc:
        logger.exception("Chat agent failed for session %s", session_id)
        await event_bus.emit(session_id, {"type": "error", "message": str(exc)})
    finally:
        event_bus.close(session_id)
