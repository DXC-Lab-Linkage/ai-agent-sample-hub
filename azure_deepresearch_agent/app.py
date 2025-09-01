# -*- coding: utf-8 -*-
"""
Chainlit + Azure AI Foundry Agents Deep Research (Non-Streaming Polling Sample)

This sample demonstrates:
- Using an existing Deep Research agent via AGENT_ID
- Non-streaming approach with polling (recommended for Deep Research)
- Displaying real-time progress (cot_summary and URL citations)
- Thread reuse within Chainlit sessions
- Run serialization to prevent multiple concurrent runs

References:
 - Deep research tool: https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/deep-research
 - Non-streaming samples: https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/deep-research-samples
"""
import os
import asyncio
import time
import re
from typing import Optional
import chainlit as cl
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import MessageRole, ThreadMessage
from azure.core.exceptions import HttpResponseError

# =========================
# Configuration
# =========================
load_dotenv()
PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "").strip()
AGENT_ID = os.environ.get("AGENT_ID", "").strip()

# Polling settings
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "1.5"))
RUN_TIMEOUT_SEC = int(os.environ.get("RUN_TIMEOUT_SEC", "1800"))  # 30 minutes


# =========================
# Azure AI Clients
# =========================
def _create_clients():
    """Create AIProjectClient & AgentsClient."""
    if not PROJECT_ENDPOINT:
        raise RuntimeError("PROJECT_ENDPOINT is not set.")
    if not AGENT_ID:
        raise RuntimeError("AGENT_ID is not set.")
    
    cred = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=cred)
    agents_client = project_client.agents
    return project_client, agents_client


# =========================
# Thread Management
# =========================
def _get_or_create_thread_id(agents_client: AgentsClient) -> str:
    """Get existing thread_id from session or create new one."""
    thread_id = cl.user_session.get("thread_id")
    if thread_id:
        return thread_id
    
    thread = agents_client.threads.create()
    cl.user_session.set("thread_id", thread.id)
    return thread.id


# =========================
# Run Management
# =========================
def _is_run_active() -> bool:
    """Check if a run is currently active in this session."""
    return cl.user_session.get("active_run_id") is not None


def _set_active_run(run_id: Optional[str]):
    """Set or clear the active run ID."""
    cl.user_session.set("active_run_id", run_id)


def _start_run(agents_client: AgentsClient, thread_id: str, agent_id: str):
    """Start a new run and mark it as active."""
    if _is_run_active():
        raise RuntimeError("A run is already active in this session.")
    
    run = agents_client.runs.create(thread_id=thread_id, agent_id=agent_id)
    _set_active_run(run.id)
    
    # Initialize progress tracking cache
    cl.user_session.set("emitted_cot_set", set())
    cl.user_session.set("emitted_url_lines", set())
    return run


def _end_run():
    """Mark run as completed."""
    _set_active_run(None)


# =========================
# Progress Polling
# =========================
async def _poll_run_and_show_progress(
    agents_client: AgentsClient,
    thread_id: str,
    run_id: str,
    status_msg: cl.Message,
    poll_interval: float = POLL_INTERVAL_SEC,
    timeout_sec: int = RUN_TIMEOUT_SEC,
) -> None:
    """
    Poll run status and display real-time progress.
    Shows cot_summary blocks and URL citations as they become available.
    """
    start_time = time.time()
    emitted_cot_set = cl.user_session.get("emitted_cot_set") or set()
    emitted_url_lines = cl.user_session.get("emitted_url_lines") or set()

    while True:
        try:
            run = agents_client.runs.get(thread_id=thread_id, run_id=run_id)
        except HttpResponseError as e:
            await status_msg.stream_token(f"\n❗ Error getting run status: {e}")
            await asyncio.sleep(poll_interval)
            if time.time() - start_time > timeout_sec:
                await status_msg.stream_token("\n⚠️ Timeout exceeded.")
                break
            continue
        except Exception as e:
            await status_msg.stream_token(f"\n❗ Unexpected error: {e}")
            await asyncio.sleep(poll_interval)
            if time.time() - start_time > timeout_sec:
                await status_msg.stream_token("\n⚠️ Timeout exceeded.")
                break
            continue

        # Check if run is still in progress
        if run.status in ("queued", "in_progress"):
            # Get latest agent message to show progress
            try:
                last_message = agents_client.messages.get_last_message_by_role(
                    thread_id=thread_id, role=MessageRole.AGENT
                )
                if last_message and last_message.text_messages:
                    # Extract and display cot_summary
                    text = "\n\n".join(
                        [t.text.value.strip() for t in last_message.text_messages]
                    )
                    
                    # Extract cot_summary blocks
                    cot_pattern = r"(?is)cot_summary:\s*(.*?)(?=\n\s*(?:[A-Z][^:]*:|$)|$)"
                    cot_match = re.search(cot_pattern, text)
                    if cot_match:
                        cot_content = cot_match.group(1).strip()
                        if cot_content:
                            cot_block = f"cot_summary: {cot_content}"
                            if cot_block not in emitted_cot_set:
                                await status_msg.stream_token("\n\n-----\n" + cot_block)
                                emitted_cot_set.add(cot_block)
                                cl.user_session.set("emitted_cot_set", emitted_cot_set)

                    # Extract and display URL citations
                    if last_message.url_citation_annotations:
                        for ann in last_message.url_citation_annotations:
                            url = ann.url_citation.url
                            title = ann.url_citation.title or url
                            citation = f"[{title}]({url})"
                            if citation not in emitted_url_lines:
                                await status_msg.stream_token(f"\nURL Citation: {citation}")
                                emitted_url_lines.add(citation)
                        cl.user_session.set("emitted_url_lines", emitted_url_lines)
            except Exception:
                # Continue polling even if progress extraction fails
                pass

            # Check timeout
            if time.time() - start_time > timeout_sec:
                await status_msg.stream_token("\n⚠️ Timeout exceeded.")
                break
            
            await asyncio.sleep(poll_interval)
            continue

        # Run completed or failed
        if run.status == "failed":
            await status_msg.stream_token(f"\n❌ Run failed.")
            if run.last_error:
                await status_msg.stream_token(f"\nError: {run.last_error}")
        #else:
        #    await status_msg.stream_token("\n✅ Research completed.")
        break


# =========================
# Final Results Display
# =========================
async def _display_final_results(agents_client: AgentsClient, thread_id: str, output_msg: cl.Message):
    """Display the final research results with citations."""
    try:
        final_message = agents_client.messages.get_last_message_by_role(
            thread_id=thread_id, role=MessageRole.AGENT
        )
    except Exception as e:
        await output_msg.stream_token(f"Error retrieving final results: {e}")
        return

    if not final_message:
        await output_msg.stream_token("No results available.")
        return

    # Display main content
    if final_message.text_messages:
        text_content = "\n\n".join([t.text.value.strip() for t in final_message.text_messages])
        await output_msg.stream_token(text_content)
    else:
        await output_msg.stream_token("No content available.")

    # Display references
    if final_message.url_citation_annotations:
        seen_urls = set()
        references = []
        for ann in final_message.url_citation_annotations:
            url = ann.url_citation.url
            title = ann.url_citation.title or url
            if url not in seen_urls:
                references.append(f"- [{title}]({url})")
                seen_urls.add(url)
        
        if references:
            await output_msg.stream_token("\n\n### References\n" + "\n".join(references))


# =========================
# Chainlit Event Handlers
# =========================
@cl.on_chat_start
async def on_chat_start():
    """Initialize the chat session."""
    await cl.Message("🧭 Deep Research Agent ready. Send me a research question!").send()


@cl.on_message
async def on_message(message: cl.Message):
    """Handle user messages and run deep research."""
    user_query = (message.content or "").strip()
    if not user_query:
        await cl.Message("Please provide a research question.").send()
        return

    # Validate environment variables
    if not PROJECT_ENDPOINT or not AGENT_ID:
        await cl.Message("❌ Missing required environment variables: PROJECT_ENDPOINT or AGENT_ID").send()
        return

    # Initialize UI panels
    status_panel = await cl.Message(content="⏳ Starting deep research...").send()
    results_panel = await cl.Message(content="").send()

    try:
        project_client, agents_client_ctx = _create_clients()
    except Exception as e:
        await status_panel.stream_token(f"\n❌ Failed to initialize clients: {e}")
        return

    try:
        with agents_client_ctx as agents_client:
            # Get or create thread for this session
            thread_id = _get_or_create_thread_id(agents_client)

            # Check if another run is active
            if _is_run_active():
                await cl.Message("⚠️ Another research is in progress. Please wait for completion.").send()
                return

            # Add user message to thread
            agents_client.messages.create(
                thread_id=thread_id,
                role="user",
                content=user_query
            )

            #await status_panel.stream_token(f"\nThread ID: {thread_id}\nAgent ID: {AGENT_ID}")

            # Start the research run
            try:
                run = _start_run(agents_client, thread_id, AGENT_ID)
            except Exception as e:
                await status_panel.stream_token(f"\n❌ Failed to start research: {e}")
                _end_run()
                return

            try:
                # Poll for completion and show progress
                await _poll_run_and_show_progress(
                    agents_client=agents_client,
                    thread_id=thread_id,
                    run_id=run.id,
                    status_msg=status_panel,
                )

                # Display final results
                await _display_final_results(agents_client, thread_id, results_panel)
            finally:
                _end_run()

    except Exception as e:
        await status_panel.stream_token(f"\n❗ Unexpected error: {e}")
        _end_run()