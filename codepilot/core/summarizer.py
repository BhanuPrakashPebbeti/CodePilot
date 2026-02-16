"""LLM-based summarization for memory management.

Provides intelligent summarization of:
- Tool outputs (preserving key information)
- Conversation exchanges (what was accomplished)
- Rolling context summaries (ongoing work state)

This avoids information loss from simple truncation while
staying within token limits.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# SUMMARIZATION PROMPTS
# ============================================================================

TOOL_OUTPUT_SUMMARY_PROMPT = """Summarize this tool output concisely, preserving:
1. The key result or outcome (success/failure)
2. Important data, file paths, or values
3. Any errors or warnings
4. Relevant counts or metrics

Tool: {tool_name}
Output:
{output}

Provide a brief summary (2-5 sentences) that captures the essential information:"""

CONVERSATION_SUMMARY_PROMPT = """Summarize the following conversation exchange concisely.
Capture:
1. What the user requested
2. What actions were taken (tools used, files created/modified)
3. The outcome or result
4. Any important context for future reference

Exchange:
{exchange}

Provide a concise summary (3-6 sentences):"""

ROLLING_SUMMARY_PROMPT = """You have a previous context summary and new conversation events.
Create an updated summary that incorporates the new information while staying concise.

Previous Summary:
{previous_summary}

New Events:
{new_events}

Updated Summary (keep essential context, remove redundant details):"""

COMPRESS_MESSAGES_PROMPT = """Compress the following conversation history into a concise summary.
Preserve:
1. The original user task/goal
2. Key decisions and actions taken
3. Files created or modified
4. Current state of the work
5. Any unresolved issues

Conversation:
{messages}

Compressed Summary:"""


# ============================================================================
# SUMMARIZER CLASS
# ============================================================================

@dataclass
class SummaryResult:
    """Result of a summarization operation."""
    original_tokens: int
    summary_tokens: int
    summary: str
    success: bool = True
    error: Optional[str] = None
    
    @property
    def tokens_saved(self) -> int:
        return self.original_tokens - self.summary_tokens
    
    @property
    def compression_ratio(self) -> float:
        if self.original_tokens == 0:
            return 1.0
        return self.summary_tokens / self.original_tokens


@dataclass
class ConversationSummary:
    """A summary of part of the conversation."""
    summary_text: str
    messages_summarized: int
    original_tokens: int
    summary_tokens: int
    timestamp: float = field(default_factory=lambda: __import__('time').time())


class Summarizer:
    """LLM-based summarizer for memory management.
    
    Uses a lightweight LLM call to create intelligent summaries
    instead of naive truncation.
    """
    
    def __init__(
        self,
        llm: Optional[Any] = None,
        estimate_tokens_fn: Optional[Callable[[str], int]] = None,
    ):
        """Initialize summarizer.
        
        Args:
            llm: LangChain LLM instance for summarization.
            estimate_tokens_fn: Function to estimate token counts.
        """
        self.llm = llm
        self._estimate_tokens = estimate_tokens_fn or self._default_estimate_tokens
        
        # Cache for tool output summaries (avoid re-summarizing same output)
        self._tool_summary_cache: Dict[int, str] = {}
        
        # Rolling summary of conversation
        self.rolling_summary: Optional[str] = None
        self.rolling_summary_tokens: int = 0
    
    @staticmethod
    def _default_estimate_tokens(text: str) -> int:
        """Default token estimation based on character count."""
        if not text:
            return 0
        return int(len(text) / 3.5) + 10
    
    def set_llm(self, llm: Any) -> None:
        """Set or update the LLM instance.
        
        Args:
            llm: LangChain LLM instance.
        """
        self.llm = llm
    
    async def summarize_tool_output(
        self,
        tool_name: str,
        output: str,
        max_output_tokens: int = 1000,
    ) -> SummaryResult:
        """Summarize a tool output using LLM.
        
        Args:
            tool_name: Name of the tool.
            output: Raw tool output.
            max_output_tokens: Maximum tokens for output.
            
        Returns:
            SummaryResult with the summary.
        """
        original_tokens = self._estimate_tokens(output)
        
        # Check cache first
        cache_key = hash(f"{tool_name}:{output}")
        if cache_key in self._tool_summary_cache:
            cached = self._tool_summary_cache[cache_key]
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(cached),
                summary=cached,
            )
        
        # If output is already small enough, return as-is
        if original_tokens <= max_output_tokens:
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=original_tokens,
                summary=output,
            )
        
        # If no LLM available, fall back to smart extraction
        if self.llm is None:
            summary = self._extract_key_info(tool_name, output, max_output_tokens)
            result = SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(summary),
                summary=summary,
            )
            self._tool_summary_cache[cache_key] = summary
            return result
        
        # Use LLM for summarization
        try:
            # Truncate input if extremely large to avoid overwhelming the summarization call
            truncated_output = output
            if original_tokens > 10000:
                # Take first and last portions for context
                lines = output.split('\n')
                if len(lines) > 200:
                    truncated_output = '\n'.join(lines[:100]) + \
                        f"\n\n... [{len(lines) - 200} lines omitted] ...\n\n" + \
                        '\n'.join(lines[-100:])
            
            prompt = TOOL_OUTPUT_SUMMARY_PROMPT.format(
                tool_name=tool_name,
                output=truncated_output[:15000],  # Hard limit for safety
            )
            
            response = await self._invoke_llm(prompt)
            summary = response.strip()
            
            # Add metadata
            summary = f"[{tool_name} summary] {summary}"
            
            self._tool_summary_cache[cache_key] = summary
            
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(summary),
                summary=summary,
            )
            
        except Exception as e:
            logger.warning(f"LLM summarization failed: {e}, falling back to extraction")
            summary = self._extract_key_info(tool_name, output, max_output_tokens)
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(summary),
                summary=summary,
                success=False,
                error=str(e),
            )
    
    async def summarize_messages(
        self,
        messages: List[BaseMessage],
        target_tokens: int = 2000,
    ) -> SummaryResult:
        """Summarize a list of messages into a condensed form.
        
        Args:
            messages: Messages to summarize.
            target_tokens: Target token count for summary.
            
        Returns:
            SummaryResult with the summary.
        """
        if not messages:
            return SummaryResult(
                original_tokens=0,
                summary_tokens=0,
                summary="",
            )
        
        # Format messages for summarization
        formatted = self._format_messages_for_summary(messages)
        original_tokens = self._estimate_tokens(formatted)
        
        # If already small enough, return formatted version
        if original_tokens <= target_tokens:
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=original_tokens,
                summary=formatted,
            )
        
        # If no LLM, use extraction-based compression
        if self.llm is None:
            summary = self._compress_messages_without_llm(messages, target_tokens)
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(summary),
                summary=summary,
            )
        
        # Use LLM for summarization
        try:
            prompt = COMPRESS_MESSAGES_PROMPT.format(messages=formatted[:20000])
            response = await self._invoke_llm(prompt)
            summary = response.strip()
            
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(summary),
                summary=summary,
            )
            
        except Exception as e:
            logger.warning(f"Message summarization failed: {e}")
            summary = self._compress_messages_without_llm(messages, target_tokens)
            return SummaryResult(
                original_tokens=original_tokens,
                summary_tokens=self._estimate_tokens(summary),
                summary=summary,
                success=False,
                error=str(e),
            )
    
    async def update_rolling_summary(
        self,
        new_messages: List[BaseMessage],
        max_summary_tokens: int = 3000,
    ) -> str:
        """Update the rolling summary with new conversation events.
        
        Args:
            new_messages: New messages to incorporate.
            max_summary_tokens: Maximum tokens for summary.
            
        Returns:
            Updated rolling summary.
        """
        if not new_messages:
            return self.rolling_summary or ""
        
        # Format new events
        new_events = self._format_messages_for_summary(new_messages)
        
        # If no previous summary, create initial one
        if not self.rolling_summary:
            result = await self.summarize_messages(new_messages, max_summary_tokens)
            self.rolling_summary = result.summary
            self.rolling_summary_tokens = result.summary_tokens
            return self.rolling_summary
        
        # If no LLM, append key info
        if self.llm is None:
            new_summary = self._compress_messages_without_llm(new_messages, 500)
            combined = f"{self.rolling_summary}\n\nRecent: {new_summary}"
            # Trim if too long
            if self._estimate_tokens(combined) > max_summary_tokens:
                combined = combined[:max_summary_tokens * 3]  # Rough char limit
            self.rolling_summary = combined
            self.rolling_summary_tokens = self._estimate_tokens(combined)
            return self.rolling_summary
        
        # Use LLM to merge summaries
        try:
            prompt = ROLLING_SUMMARY_PROMPT.format(
                previous_summary=self.rolling_summary,
                new_events=new_events[:10000],
            )
            
            response = await self._invoke_llm(prompt)
            self.rolling_summary = response.strip()
            self.rolling_summary_tokens = self._estimate_tokens(self.rolling_summary)
            
            return self.rolling_summary
            
        except Exception as e:
            logger.warning(f"Rolling summary update failed: {e}")
            # Append minimal info
            new_summary = self._compress_messages_without_llm(new_messages, 300)
            self.rolling_summary = f"{self.rolling_summary}\n\nRecent: {new_summary}"
            return self.rolling_summary
    
    def clear(self) -> None:
        """Clear all cached summaries and rolling summary."""
        self._tool_summary_cache.clear()
        self.rolling_summary = None
        self.rolling_summary_tokens = 0
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    async def _invoke_llm(self, prompt: str) -> str:
        """Invoke LLM with a prompt.
        
        Args:
            prompt: The prompt to send.
            
        Returns:
            LLM response text.
        """
        if self.llm is None:
            raise ValueError("No LLM configured for summarization")
        
        # Try async invoke first, fall back to sync
        if hasattr(self.llm, 'ainvoke'):
            response = await self.llm.ainvoke(prompt)
        else:
            # Run sync invoke in thread pool
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, self.llm.invoke, prompt)
        
        # Extract content from response
        if hasattr(response, 'content'):
            return response.content
        return str(response)
    
    def _format_messages_for_summary(self, messages: List[BaseMessage]) -> str:
        """Format messages into readable text for summarization.
        
        Args:
            messages: Messages to format.
            
        Returns:
            Formatted text.
        """
        parts = []
        
        for msg in messages:
            if isinstance(msg, HumanMessage):
                parts.append(f"User: {msg.content}")
            elif isinstance(msg, AIMessage):
                content = msg.content
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(f"Assistant: {content}")
            elif isinstance(msg, ToolMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if len(content) > 300:
                    content = content[:300] + "..."
                tool_id = getattr(msg, 'tool_call_id', 'unknown')
                parts.append(f"Tool[{tool_id}]: {content}")
            elif isinstance(msg, SystemMessage):
                parts.append(f"System: {msg.content[:200]}...")
            else:
                parts.append(f"Message: {str(msg)[:200]}")
        
        return '\n\n'.join(parts)
    
    def _extract_key_info(
        self,
        tool_name: str,
        output: str,
        max_tokens: int,
    ) -> str:
        """Extract key information from tool output without LLM.
        
        Uses heuristics to identify important parts.
        
        Args:
            tool_name: Name of the tool.
            output: Raw output.
            max_tokens: Maximum tokens for result.
            
        Returns:
            Extracted key information.
        """
        lines = output.split('\n')
        
        # Identify important lines
        important_patterns = [
            'error', 'Error', 'ERROR', 'failed', 'Failed', 'FAILED',
            'success', 'Success', 'SUCCESS', 'created', 'Created',
            'warning', 'Warning', 'WARNING',
            '✓', '✗', '→', '=>',
            'result:', 'Result:', 'output:', 'Output:',
            'total', 'Total', 'count', 'Count',
            '.py', '.js', '.ts', '.json', '.yaml', '.yml',
            'http://', 'https://', 'localhost',
            'port', 'Port', 'PORT',
            'installed', 'Installed', 'added', 'Added',
        ]
        
        important_lines = []
        context_lines = []
        
        for i, line in enumerate(lines):
            is_important = any(p in line for p in important_patterns)
            
            if is_important:
                important_lines.append(line)
            elif i < 10 or i >= len(lines) - 10:
                # Keep first and last lines for context
                context_lines.append(line)
        
        # Build summary
        summary_parts = []
        
        # Add header
        summary_parts.append(f"[{tool_name} output summary]")
        
        # Add first few lines for context
        if context_lines and lines:
            summary_parts.append("Start: " + ' | '.join(lines[:3]))
        
        # Add important findings
        if important_lines:
            summary_parts.append("Key findings:")
            # Deduplicate and limit
            seen = set()
            for line in important_lines[:20]:
                line_clean = line.strip()
                if line_clean and line_clean not in seen:
                    seen.add(line_clean)
                    summary_parts.append(f"  • {line_clean[:150]}")
        
        # Add statistics
        summary_parts.append(f"Total lines: {len(lines)}")
        
        summary = '\n'.join(summary_parts)
        
        # Ensure within token limit
        while self._estimate_tokens(summary) > max_tokens and len(summary_parts) > 3:
            summary_parts.pop(-2)  # Remove from middle
            summary = '\n'.join(summary_parts)
        
        return summary
    
    def _compress_messages_without_llm(
        self,
        messages: List[BaseMessage],
        target_tokens: int,
    ) -> str:
        """Compress messages without LLM using extraction.
        
        Args:
            messages: Messages to compress.
            target_tokens: Target token count.
            
        Returns:
            Compressed summary.
        """
        parts = []
        
        # Group messages by type
        human_requests = []
        actions_taken = []
        outcomes = []
        
        for msg in messages:
            if isinstance(msg, HumanMessage):
                # Truncate long requests
                content = msg.content[:200] if len(msg.content) > 200 else msg.content
                human_requests.append(content)
            elif isinstance(msg, AIMessage):
                # Extract first sentence or key action
                content = msg.content
                first_sentence = content.split('.')[0][:150] if content else ""
                if first_sentence:
                    actions_taken.append(first_sentence)
            elif isinstance(msg, ToolMessage):
                # Extract outcome
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                # Look for success/failure indicators
                if any(x in content.lower() for x in ['success', 'created', 'done', '✓']):
                    outcomes.append("✓ " + content[:100])
                elif any(x in content.lower() for x in ['error', 'failed', '✗']):
                    outcomes.append("✗ " + content[:100])
        
        # Build compressed summary
        if human_requests:
            parts.append(f"Requests: {'; '.join(human_requests[:3])}")
        
        if actions_taken:
            parts.append(f"Actions: {'; '.join(actions_taken[:5])}")
        
        if outcomes:
            parts.append(f"Outcomes: {'; '.join(outcomes[:5])}")
        
        summary = '\n'.join(parts)
        
        # Trim if still too long
        max_chars = target_tokens * 3
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "..."
        
        return summary


# ============================================================================
# CONVERSATION COMPRESSOR
# ============================================================================

class ConversationCompressor:
    """Compresses conversation history intelligently.
    
    Strategies:
    1. Keep recent messages intact
    2. Summarize older exchanges into condensed form
    3. Maintain rolling summary of overall context
    """
    
    def __init__(
        self,
        summarizer: Summarizer,
        recent_messages_to_keep: int = 10,
        summary_threshold: int = 20,
    ):
        """Initialize conversation compressor.
        
        Args:
            summarizer: Summarizer instance for LLM calls.
            recent_messages_to_keep: Number of recent messages to keep intact.
            summary_threshold: Messages count before summarization kicks in.
        """
        self.summarizer = summarizer
        self.recent_messages_to_keep = recent_messages_to_keep
        self.summary_threshold = summary_threshold
        
        # Stored summaries of old conversation parts
        self.archived_summaries: List[ConversationSummary] = []
    
    async def compress(
        self,
        messages: List[BaseMessage],
        target_tokens: int,
        estimate_tokens_fn: Callable[[BaseMessage], int],
    ) -> List[BaseMessage]:
        """Compress messages to fit within target token count.
        
        Args:
            messages: All messages in conversation.
            target_tokens: Target token count.
            estimate_tokens_fn: Function to estimate message tokens.
            
        Returns:
            Compressed message list (may include summary messages).
        """
        if len(messages) < self.summary_threshold:
            return messages
        
        # Calculate current token usage
        total_tokens = sum(estimate_tokens_fn(m) for m in messages)
        
        if total_tokens <= target_tokens:
            return messages
        
        logger.info(f"Compressing conversation: {total_tokens} tokens -> target {target_tokens}")
        
        # Split into old and recent messages
        split_point = len(messages) - self.recent_messages_to_keep
        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]
        
        # Summarize old messages
        result = await self.summarizer.summarize_messages(old_messages, target_tokens // 3)
        
        # Create summary message
        summary_msg = SystemMessage(
            content=f"[Previous conversation summary]\n{result.summary}"
        )
        
        # Store summary for reference
        self.archived_summaries.append(ConversationSummary(
            summary_text=result.summary,
            messages_summarized=len(old_messages),
            original_tokens=result.original_tokens,
            summary_tokens=result.summary_tokens,
        ))
        
        # Build new message list
        compressed = [summary_msg] + recent_messages
        
        new_tokens = sum(estimate_tokens_fn(m) for m in compressed)
        logger.info(f"Compression complete: {total_tokens} -> {new_tokens} tokens")
        
        return compressed
    
    def clear(self) -> None:
        """Clear archived summaries."""
        self.archived_summaries.clear()
