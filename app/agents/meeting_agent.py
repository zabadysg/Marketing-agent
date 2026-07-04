"""Multi-agent Meeting Room orchestrator.

Turn-taking model: relevance-based self-selection (bidding).

Flow per user message
─────────────────────
1. Seed transcript with user message.
2. Loop until stop condition:
   a. Emit bidding_start.
   b. Parallel bid — every agent except the last speaker.
   c. Apply recency decay, select winner.
   d. If max_score < SILENCE_THRESHOLD for 2 consecutive rounds → stop (consensus).
   e. If total_turns >= MEETING_HARD_CAP → stop (cap_reached).
   f. Emit agent_turn_start → stream tokens → emit agent_turn_end.
   g. Persist turn to DB, append to in-memory transcript.
3. Emit meeting_concluded.
4. Synthesis pass (Chief of Staff, uses tools, streams).
5. Emit synthesis_end → done.
"""
import asyncio
import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.chat_tools import make_chat_tools
from app.agents.llm import get_llm
from app.agents.meeting_roster import CHIEF_OF_STAFF, ROSTER, AgentPersona
from app.database import AsyncSessionLocal
from app.models.chat import MessageRole
from app.services import event_bus
from app.services.chat import save_message

logger = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────────────
MEETING_HARD_CAP = 10        # total agent turns before forced stop
SILENCE_THRESHOLD = 4        # max_bid < this for N consecutive rounds → consensus
CONSECUTIVE_SILENCE_LIMIT = 2
RECENCY_PENALTY = 2          # score deducted per appearance in the last-2-speakers window
RECENCY_WINDOW = 2           # how many recent speakers to penalise
BID_CONTEXT_WINDOW = 12      # how many transcript entries to show bidding agents
MAX_TOOL_ROUNDS = 5          # per individual agent turn (same as single-agent chat)


# ── bid schema ────────────────────────────────────────────────────────────────
class BidResult(BaseModel):
    score: int = Field(ge=0, le=10, description="Confidence 0-10 that I have something new to add")
    reason: str = Field(description="One-line reason (shown to user as bid signal)")


# ── helpers ───────────────────────────────────────────────────────────────────
def _format_transcript(transcript: list[dict], window: int | None = None) -> str:
    entries = transcript[-window:] if window else transcript
    lines = []
    for t in entries:
        if t["role"] == "user":
            lines.append(f"[User]: {t['content']}")
        else:
            label = f"{t.get('agent_name', 'Assistant')} ({t.get('agent_id', 'assistant')})"
            lines.append(f"[{label}]: {t['content']}")
    return "\n\n".join(lines)


async def _safe_bid(
    persona: AgentPersona,
    transcript: list[dict],
    recent_speakers: list[str],
    brand_profile: dict,
) -> BidResult:
    """Run one agent's bid call. Returns score=0 on any failure."""
    try:
        transcript_text = _format_transcript(transcript, window=BID_CONTEXT_WINDOW)

        system = f"""\
You are {persona.name}, a {persona.id} marketing specialist.

## Brand
{json.dumps(brand_profile, indent=2)}

## Your role
{persona.system_prompt}

## Bidding instructions
Decide whether you have something GENUINELY NEW to add to this marketing discussion.

Bid HIGH (7-10) only if you have a SPECIFIC, SUBSTANTIVE contribution:
- A clear disagreement with something already said
- An unanswered question you are best positioned to answer
- A critical missing perspective (e.g. an SEO angle nobody mentioned, a brand risk)
- A concrete next step you can execute right now

Bid LOW (0-3) if:
- The conversation already covers your perspective well
- You would just be repeating or agreeing with what was said
- You have nothing meaningfully new to add

Example of a correct low-score non-bid:
{{"score": 1, "reason": "The strategist already covered the campaign angle I had in mind."}}

Respond ONLY with JSON matching the schema: {{"score": 0-10, "reason": "<one line>"}}
"""
        messages = [
            SystemMessage(content=system),
            HumanMessage(
                content=f"Meeting transcript so far:\n\n{transcript_text}\n\n"
                        "Do you have something new to contribute?"
            ),
        ]

        llm = get_llm("cheap").with_structured_output(BidResult)
        result: BidResult = await llm.ainvoke(messages)

        # Recency decay — penalise agents who spoke recently
        penalty = recent_speakers.count(persona.id) * RECENCY_PENALTY
        result.score = max(0, result.score - penalty)
        return result

    except Exception as exc:
        logger.warning("Bid failed for agent %s: %s", persona.id, exc)
        return BidResult(score=0, reason=f"bid error: {exc}")


async def _run_agent_turn(
    session_id: str,
    persona: AgentPersona,
    user_message: str,
    transcript: list[dict],
    brand_profile: dict,
    retrieved_context: str,
    all_tools: list,
    tool_map: dict[str, Any],
) -> str:
    """Run one agent's turn, streaming tokens via event_bus. Returns full_content."""
    # Filter to this persona's allowed tools
    agent_tools = [t for t in all_tools if t.name in persona.tools]
    agent_tool_map = {k: v for k, v in tool_map.items() if k in persona.tools}
    llm_with_tools = get_llm("cheap").bind_tools(agent_tools)

    system = f"""\
You are {persona.name}, a {persona.id} marketing specialist participating in a team meeting.

## Brand Profile
{json.dumps(brand_profile, indent=2)}

## Relevant Brand Knowledge
{retrieved_context or "No knowledge documents indexed yet."}

## Your role in this meeting
{persona.system_prompt}

## Guidelines
- Be concise and direct — this is a fast-paced team discussion, not a monologue.
- Build on or challenge what others said; don't repeat what's already been covered.
- Use your tools when they would genuinely add value (research, drafting, searching).

## Language (CRITICAL)
Detect the language of the user's message and respond ONLY in that language.
- User writes in Arabic → respond entirely in Arabic.
- User writes in English → respond entirely in English.
Never switch languages regardless of the language used in the brand profile or these instructions.
"""

    transcript_text = _format_transcript(transcript)

    messages = [
        SystemMessage(content=system),
        HumanMessage(
            content=f"Original user request: {user_message}\n\n"
                    f"Meeting transcript so far:\n\n{transcript_text}\n\n"
                    f"It's your turn, {persona.name}. What's your specific contribution?"
        ),
    ]

    full_content = ""
    tool_rounds = 0

    while tool_rounds <= MAX_TOOL_ROUNDS:
        response = None

        async for ev in llm_with_tools.astream_events(messages, version="v2"):
            etype = ev["event"]
            if etype == "on_chat_model_stream":
                chunk = ev["data"]["chunk"]
                raw = chunk.content
                if isinstance(raw, list):
                    text = "".join(
                        p.get("text", "") for p in raw
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
            fallback = "\n\n[Reached tool-call depth limit. Answering from available context.]"
            full_content += fallback
            await event_bus.emit(session_id, {"type": "token", "content": fallback})
            break

        messages.append(response)
        for tc in response.tool_calls:
            await event_bus.emit(
                session_id,
                {"type": "tool_start", "tool": tc["name"], "agent": persona.id},
            )
            tool_fn = agent_tool_map.get(tc["name"])
            if tool_fn is None:
                result = f"Unknown tool: {tc['name']}"
            else:
                try:
                    result = await tool_fn.ainvoke(tc["args"])
                except Exception as exc:
                    logger.warning("Tool %s failed for %s: %s", tc["name"], persona.id, exc)
                    result = f"Error: {exc}"
            await event_bus.emit(
                session_id,
                {"type": "tool_end", "tool": tc["name"], "agent": persona.id},
            )
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    return full_content


async def _run_synthesis(
    session_id: str,
    user_message: str,
    transcript: list[dict],
    brand_profile: dict,
    retrieved_context: str,
    all_tools: list,
    tool_map: dict[str, Any],
) -> str:
    """Chief of Staff synthesis pass — streams tokens, may call tools."""
    persona = CHIEF_OF_STAFF
    agent_tools = [t for t in all_tools if t.name in persona.tools]
    agent_tool_map = {k: v for k, v in tool_map.items() if k in persona.tools}
    llm_with_tools = get_llm("cheap").bind_tools(agent_tools)

    system = f"""\
You are {persona.name}, the Chief of Staff.

## Brand Profile
{json.dumps(brand_profile, indent=2)}

## Relevant Brand Knowledge
{retrieved_context or "No knowledge documents indexed yet."}

## Your task
{persona.system_prompt}

## Language (CRITICAL)
Detect the language of the user's message and respond ONLY in that language.
- User writes in Arabic → respond entirely in Arabic.
- User writes in English → respond entirely in English.
Never switch languages regardless of the language used in the brand profile or these instructions.
"""
    transcript_text = _format_transcript(transcript)

    messages = [
        SystemMessage(content=system),
        HumanMessage(
            content=f"Original user request: {user_message}\n\n"
                    f"Full meeting transcript:\n\n{transcript_text}\n\n"
                    "Now synthesize the discussion and deliver a concrete outcome."
        ),
    ]

    full_content = ""
    tool_rounds = 0

    while tool_rounds <= MAX_TOOL_ROUNDS:
        response = None
        async for ev in llm_with_tools.astream_events(messages, version="v2"):
            etype = ev["event"]
            if etype == "on_chat_model_stream":
                chunk = ev["data"]["chunk"]
                raw = chunk.content
                if isinstance(raw, list):
                    text = "".join(
                        p.get("text", "") for p in raw
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
            break

        messages.append(response)
        for tc in response.tool_calls:
            await event_bus.emit(
                session_id,
                {"type": "tool_start", "tool": tc["name"], "agent": persona.id},
            )
            tool_fn = agent_tool_map.get(tc["name"])
            if tool_fn is None:
                result = f"Unknown tool: {tc['name']}"
            else:
                try:
                    result = await tool_fn.ainvoke(tc["args"])
                except Exception as exc:
                    logger.warning("Synthesis tool %s failed: %s", tc["name"], exc)
                    result = f"Error: {exc}"
            await event_bus.emit(
                session_id,
                {"type": "tool_end", "tool": tc["name"], "agent": persona.id},
            )
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    return full_content


# ── main orchestrator ─────────────────────────────────────────────────────────
async def run_meeting_agent(
    session_id: str,
    meeting_id: str,
    workspace_id: str,
    user_message: str,
    brand_profile: dict,
    retrieved_context: str,
    session_factory: async_sessionmaker = AsyncSessionLocal,
) -> None:
    """Background task. Orchestrates the full multi-agent meeting room."""
    try:
        all_tools, tool_map = make_chat_tools(workspace_id, brand_profile, session_factory)

        # Transcript: list of {role, content, agent_id?, agent_name?}
        transcript: list[dict] = [{"role": "user", "content": user_message}]
        # Tracks last RECENCY_WINDOW speakers for recency decay
        recent_speakers: list[str] = []
        consecutive_silence = 0
        total_turns = 0
        conclusion_reason = "cap_reached"

        while total_turns < MEETING_HARD_CAP:
            # ── bidding phase ────────────────────────────────────────────────
            await event_bus.emit(session_id, {"type": "bidding_start"})

            last_speaker = recent_speakers[-1] if recent_speakers else None
            eligible = [p for p in ROSTER if p.id != last_speaker]

            raw_bids: list[BidResult] = await asyncio.gather(
                *[_safe_bid(p, transcript, recent_speakers, brand_profile) for p in eligible]
            )

            max_score = max((b.score for b in raw_bids), default=0)

            if max_score < SILENCE_THRESHOLD:
                consecutive_silence += 1
                if consecutive_silence >= CONSECUTIVE_SILENCE_LIMIT:
                    conclusion_reason = "consensus"
                    break
            else:
                consecutive_silence = 0

            # Select winner: highest score; tie-break by least-recent speaker
            # Build a last-spoke-at map (higher index = more recent = penalised in tie)
            last_spoke: dict[str, int] = {}
            for i, entry in enumerate(transcript):
                if entry["role"] == "assistant" and entry.get("agent_id"):
                    last_spoke[entry["agent_id"]] = i

            def _rank(idx: int) -> tuple[int, int]:
                score = raw_bids[idx].score
                spoke_at = last_spoke.get(eligible[idx].id, -1)
                return (score, -spoke_at)  # higher score first, less-recent first on tie

            winner_idx = max(range(len(eligible)), key=_rank)
            winner = eligible[winner_idx]
            winner_bid = raw_bids[winner_idx]

            # Collect all bids for debuggability — stored in message metadata
            all_bids_debug = [
                {"agent": eligible[i].id, "score": raw_bids[i].score, "reason": raw_bids[i].reason}
                for i in range(len(eligible))
            ]

            # ── agent speaks ─────────────────────────────────────────────────
            await event_bus.emit(
                session_id,
                {
                    "type": "agent_turn_start",
                    "agent": winner.id,
                    "name": winner.name,
                    "bid_reason": winner_bid.reason,
                },
            )

            agent_content = await _run_agent_turn(
                session_id=session_id,
                persona=winner,
                user_message=user_message,
                transcript=transcript,
                brand_profile=brand_profile,
                retrieved_context=retrieved_context,
                all_tools=all_tools,
                tool_map=tool_map,
            )

            async with session_factory() as db:
                await save_message(
                    db,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    role=MessageRole.assistant,
                    content=agent_content,
                    agent_id=winner.id,
                    meeting_id=meeting_id,
                    turn_index=total_turns,
                    metadata={
                        "bid_score": winner_bid.score,
                        "bid_reason": winner_bid.reason,
                        "all_bids": all_bids_debug,
                    },
                )
                await db.commit()

            await event_bus.emit(
                session_id, {"type": "agent_turn_end", "agent": winner.id}
            )

            # Update in-memory transcript + recency tracker
            transcript.append(
                {
                    "role": "assistant",
                    "agent_id": winner.id,
                    "agent_name": winner.name,
                    "content": agent_content,
                }
            )
            recent_speakers.append(winner.id)
            if len(recent_speakers) > RECENCY_WINDOW:
                recent_speakers.pop(0)

            total_turns += 1

        # ── meeting ended ─────────────────────────────────────────────────────
        await event_bus.emit(
            session_id, {"type": "meeting_concluded", "reason": conclusion_reason}
        )

        # ── synthesis pass ────────────────────────────────────────────────────
        await event_bus.emit(session_id, {"type": "synthesis_start"})

        synthesis_content = await _run_synthesis(
            session_id=session_id,
            user_message=user_message,
            transcript=transcript,
            brand_profile=brand_profile,
            retrieved_context=retrieved_context,
            all_tools=all_tools,
            tool_map=tool_map,
        )

        async with session_factory() as db:
            await save_message(
                db,
                session_id=session_id,
                workspace_id=workspace_id,
                role=MessageRole.assistant,
                content=synthesis_content,
                agent_id=CHIEF_OF_STAFF.id,
                meeting_id=meeting_id,
                turn_index=total_turns,
                metadata={"synthesis": True},
            )
            await db.commit()

        await event_bus.emit(session_id, {"type": "synthesis_end"})
        await event_bus.emit(session_id, {"type": "done"})

    except asyncio.CancelledError:
        logger.debug("Meeting agent cancelled (client disconnected) for session %s", session_id)
        raise
    except Exception as exc:
        logger.exception("Meeting agent failed for session %s", session_id)
        await event_bus.emit(session_id, {"type": "error", "message": str(exc)})
    finally:
        event_bus.close(session_id)
