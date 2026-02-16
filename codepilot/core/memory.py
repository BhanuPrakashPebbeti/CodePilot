"""Memory management for CodePilot agent.

Handles:
- Token counting and estimation
- Message history pruning with sliding window
- Tool output truncation and summarization
- Context window management to prevent token limit errors
- LLM-based intelligent summarization (when available)
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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
# TOKEN ESTIMATION
# ============================================================================

# Approximate tokens per character for different content types
# These are conservative estimates (slightly overestimate to be safe)
CHARS_PER_TOKEN_TEXT = 3.5  # ~3.5 chars per token for English text
CHARS_PER_TOKEN_CODE = 3.0  # Code tends to have more tokens per char
CHARS_PER_TOKEN_JSON = 2.5  # JSON is dense with special chars


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string.
    
    Uses character-based heuristics since we don't have access to
    the actual tokenizer. Errs on the side of overestimation.
    
    Args:
        text: Text to estimate tokens for.
        
    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    
    # Detect content type and use appropriate ratio
    chars_per_token = CHARS_PER_TOKEN_TEXT
    
    # Check if it looks like JSON
    stripped = text.strip()
    if stripped.startswith('{') or stripped.startswith('['):
        chars_per_token = CHARS_PER_TOKEN_JSON
    # Check if it looks like code (has common code patterns)
    elif any(pattern in text for pattern in ['def ', 'class ', 'function ', 'import ', 'const ', 'let ', 'var ']):
        chars_per_token = CHARS_PER_TOKEN_CODE
    
    # Calculate base estimate
    estimated = len(text) / chars_per_token
    
    # Add overhead for message formatting (role tags, etc.)
    overhead = 10
    
    return int(estimated + overhead)


def estimate_message_tokens(message: BaseMessage) -> int:
    """Estimate token count for a LangChain message.
    
    Args:
        message: LangChain message object.
        
    Returns:
        Estimated token count.
    """
    content = message.content if hasattr(message, 'content') else str(message)
    
    if isinstance(content, str):
        tokens = estimate_tokens(content)
    elif isinstance(content, list):
        # Handle content blocks (e.g., tool results with multiple parts)
        tokens = sum(
            estimate_tokens(block.get('text', str(block)) if isinstance(block, dict) else str(block))
            for block in content
        )
    else:
        tokens = estimate_tokens(str(content))
    
    # Add overhead for message type/role
    tokens += 5
    
    # Tool messages have additional metadata
    if isinstance(message, ToolMessage):
        tokens += 20  # tool_call_id, name, etc.
    
    return tokens


# ============================================================================
# CONTENT TRUNCATION
# ============================================================================

@dataclass
class TruncationConfig:
    """Configuration for content truncation."""
    max_tool_output_tokens: int = 8000  # Max tokens for a single tool output
    max_tool_output_lines: int = 200    # Max lines for tool output
    max_message_tokens: int = 16000     # Max tokens for any single message
    preserve_head_lines: int = 50       # Lines to keep from start
    preserve_tail_lines: int = 50       # Lines to keep from end
    truncation_marker: str = "\n\n... [{lines_removed} lines truncated for brevity] ...\n\n"


def truncate_by_lines(
    text: str,
    max_lines: int,
    preserve_head: int = 50,
    preserve_tail: int = 50,
) -> Tuple[str, int]:
    """Truncate text by line count, preserving head and tail.
    
    Args:
        text: Text to truncate.
        max_lines: Maximum lines allowed.
        preserve_head: Lines to keep from start.
        preserve_tail: Lines to keep from end.
        
    Returns:
        Tuple of (truncated_text, lines_removed).
    """
    lines = text.split('\n')
    
    if len(lines) <= max_lines:
        return text, 0
    
    # Calculate how many lines to remove
    lines_to_keep = preserve_head + preserve_tail
    lines_removed = len(lines) - lines_to_keep
    
    if lines_removed <= 0:
        # Can't truncate meaningfully, just take max_lines
        return '\n'.join(lines[:max_lines]), len(lines) - max_lines
    
    head = lines[:preserve_head]
    tail = lines[-preserve_tail:]
    
    truncation_marker = f"\n... [{lines_removed} lines truncated] ...\n"
    
    return '\n'.join(head) + truncation_marker + '\n'.join(tail), lines_removed


def truncate_by_tokens(
    text: str,
    max_tokens: int,
    preserve_ratio: float = 0.3,
) -> Tuple[str, int]:
    """Truncate text by token count, preserving start and end.
    
    Args:
        text: Text to truncate.
        max_tokens: Maximum tokens allowed.
        preserve_ratio: Ratio of preserved content to keep at start (rest at end).
        
    Returns:
        Tuple of (truncated_text, tokens_removed).
    """
    current_tokens = estimate_tokens(text)
    
    if current_tokens <= max_tokens:
        return text, 0
    
    # Estimate chars to keep based on token target
    target_chars = int(max_tokens * CHARS_PER_TOKEN_TEXT)
    head_chars = int(target_chars * preserve_ratio)
    tail_chars = target_chars - head_chars
    
    # Reserve space for truncation marker
    marker_chars = 100
    head_chars -= marker_chars // 2
    tail_chars -= marker_chars // 2
    
    if head_chars < 100 or tail_chars < 100:
        # Too small, just take from start
        return text[:target_chars] + "\n... [truncated] ...", current_tokens - max_tokens
    
    head = text[:head_chars]
    tail = text[-tail_chars:]
    
    tokens_removed = current_tokens - max_tokens
    marker = f"\n\n... [{tokens_removed} tokens truncated] ...\n\n"
    
    return head + marker + tail, tokens_removed


def truncate_tool_output(
    output: str,
    config: Optional[TruncationConfig] = None,
) -> str:
    """Truncate tool output to fit within limits.
    
    First tries line-based truncation, then token-based if needed.
    
    Args:
        output: Tool output to truncate.
        config: Truncation configuration.
        
    Returns:
        Truncated output.
    """
    if config is None:
        config = TruncationConfig()
    
    # First, truncate by lines
    truncated, lines_removed = truncate_by_lines(
        output,
        config.max_tool_output_lines,
        config.preserve_head_lines,
        config.preserve_tail_lines,
    )
    
    if lines_removed > 0:
        logger.debug(f"Truncated tool output: removed {lines_removed} lines")
    
    # Then check tokens and truncate further if needed
    tokens = estimate_tokens(truncated)
    if tokens > config.max_tool_output_tokens:
        truncated, tokens_removed = truncate_by_tokens(
            truncated,
            config.max_tool_output_tokens,
        )
        logger.debug(f"Truncated tool output: removed ~{tokens_removed} tokens")
    
    return truncated


# ============================================================================
# MEMORY MANAGER
# ============================================================================

@dataclass
class MemoryConfig:
    """Configuration for memory management."""
    # Context window limits
    max_context_tokens: int = 100000     # Leave headroom from model limit (131k)
    target_context_tokens: int = 80000   # Target after pruning
    
    # System prompt handling
    system_prompt_tokens: int = 8000     # Estimated system prompt tokens
    
    # Message preservation
    min_recent_messages: int = 10        # Always keep at least this many recent messages
    preserve_first_human: bool = True    # Keep the original task message
    
    # Truncation settings (fallback when no LLM)
    truncation: TruncationConfig = field(default_factory=TruncationConfig)
    
    # Summarization settings
    enable_summarization: bool = True    # Use LLM for summarization when available
    summary_threshold: int = 15          # Messages before summarization kicks in
    tool_output_summary_threshold: int = 2000  # Token threshold for tool output summarization
    max_tool_output_tokens: int = 1500   # Max tokens for summarized tool output


class MemoryManager:
    """Manages conversation memory and context window.
    
    Responsibilities:
    - Track token usage across messages
    - Prune old messages when approaching limits
    - Truncate large tool outputs
    - Optionally summarize conversation history
    """
    
    def __init__(self, config: Optional[MemoryConfig] = None):
        """Initialize memory manager.
        
        Args:
            config: Memory configuration.
        """
        self.config = config or MemoryConfig()
        self._token_cache: Dict[int, int] = {}  # message id -> token count
    
    def estimate_total_tokens(self, messages: List[BaseMessage]) -> int:
        """Estimate total tokens for a message list.
        
        Args:
            messages: List of messages.
            
        Returns:
            Total estimated tokens.
        """
        total = self.config.system_prompt_tokens  # Account for system prompt
        
        for msg in messages:
            msg_id = id(msg)
            if msg_id not in self._token_cache:
                self._token_cache[msg_id] = estimate_message_tokens(msg)
            total += self._token_cache[msg_id]
        
        return total
    
    def get_message_tokens(self, message: BaseMessage) -> int:
        """Get token count for a single message (cached).
        
        Args:
            message: Message to count.
            
        Returns:
            Token count.
        """
        msg_id = id(message)
        if msg_id not in self._token_cache:
            self._token_cache[msg_id] = estimate_message_tokens(message)
        return self._token_cache[msg_id]
    
    def should_prune(self, messages: List[BaseMessage]) -> bool:
        """Check if messages should be pruned.
        
        Args:
            messages: Current message list.
            
        Returns:
            True if pruning is needed.
        """
        total_tokens = self.estimate_total_tokens(messages)
        return total_tokens > self.config.max_context_tokens
    
    def prune_messages(
        self,
        messages: List[BaseMessage],
        force: bool = False,
    ) -> List[BaseMessage]:
        """Prune messages to fit within context window.
        
        Strategy:
        1. Always keep first human message (original task)
        2. Always keep most recent N messages
        3. Remove oldest messages in the middle
        4. Optionally summarize removed content
        
        Args:
            messages: Current message list.
            force: Force pruning even if under limit.
            
        Returns:
            Pruned message list.
        """
        total_tokens = self.estimate_total_tokens(messages)
        
        if not force and total_tokens <= self.config.max_context_tokens:
            return messages
        
        logger.info(
            f"Pruning messages: {total_tokens} tokens > {self.config.max_context_tokens} limit"
        )
        
        # Can't prune if too few messages
        if len(messages) <= self.config.min_recent_messages:
            logger.warning("Cannot prune: too few messages")
            return messages
        
        pruned = []
        tokens_removed = 0
        messages_removed = 0
        
        # Identify messages to keep
        first_human_idx = None
        if self.config.preserve_first_human:
            for i, msg in enumerate(messages):
                if isinstance(msg, HumanMessage):
                    first_human_idx = i
                    break
        
        # Always keep recent messages
        recent_start = len(messages) - self.config.min_recent_messages
        
        # Build pruned list
        for i, msg in enumerate(messages):
            keep = False
            
            # Keep first human message
            if i == first_human_idx:
                keep = True
            # Keep recent messages
            elif i >= recent_start:
                keep = True
            # Check if we're still over budget
            elif self.estimate_total_tokens(pruned) < self.config.target_context_tokens:
                keep = True
            
            if keep:
                pruned.append(msg)
            else:
                tokens_removed += self.get_message_tokens(msg)
                messages_removed += 1
        
        # If still over limit, truncate large messages in the kept list
        if self.estimate_total_tokens(pruned) > self.config.max_context_tokens:
            pruned = self._truncate_large_messages(pruned)
        
        logger.info(
            f"Pruned {messages_removed} messages (~{tokens_removed} tokens). "
            f"New total: {self.estimate_total_tokens(pruned)} tokens"
        )
        
        # Invalidate cache for removed messages
        self._token_cache = {
            id(msg): self._token_cache.get(id(msg), estimate_message_tokens(msg))
            for msg in pruned
        }
        
        return pruned
    
    def _truncate_large_messages(
        self,
        messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        """Truncate individual large messages.
        
        Args:
            messages: Messages to process.
            
        Returns:
            Messages with large content truncated.
        """
        truncated = []
        
        for msg in messages:
            tokens = self.get_message_tokens(msg)
            
            if tokens > self.config.truncation.max_message_tokens:
                # Truncate this message
                if isinstance(msg.content, str):
                    new_content, _ = truncate_by_tokens(
                        msg.content,
                        self.config.truncation.max_message_tokens,
                    )
                    # Create new message with truncated content
                    if isinstance(msg, HumanMessage):
                        msg = HumanMessage(content=new_content)
                    elif isinstance(msg, AIMessage):
                        msg = AIMessage(content=new_content)
                    elif isinstance(msg, ToolMessage):
                        msg = ToolMessage(
                            content=new_content,
                            tool_call_id=getattr(msg, 'tool_call_id', 'truncated'),
                        )
                    
                    # Update cache
                    self._token_cache[id(msg)] = estimate_message_tokens(msg)
                    logger.debug(f"Truncated large message: {tokens} -> {self._token_cache[id(msg)]} tokens")
            
            truncated.append(msg)
        
        return truncated
    
    def prepare_tool_output(self, output: str) -> str:
        """Prepare tool output for inclusion in messages.
        
        Truncates if necessary to prevent token explosion.
        
        Args:
            output: Raw tool output.
            
        Returns:
            Processed (possibly truncated) output.
        """
        return truncate_tool_output(output, self.config.truncation)
    
    def get_stats(self, messages: List[BaseMessage]) -> Dict[str, Any]:
        """Get memory statistics.
        
        Args:
            messages: Current message list.
            
        Returns:
            Dictionary of statistics.
        """
        total_tokens = self.estimate_total_tokens(messages)
        message_tokens = [
            (type(msg).__name__, self.get_message_tokens(msg))
            for msg in messages
        ]
        
        return {
            "total_tokens": total_tokens,
            "max_tokens": self.config.max_context_tokens,
            "usage_percent": round(total_tokens / self.config.max_context_tokens * 100, 1),
            "message_count": len(messages),
            "tokens_by_type": {
                "human": sum(t for name, t in message_tokens if name == "HumanMessage"),
                "ai": sum(t for name, t in message_tokens if name == "AIMessage"),
                "tool": sum(t for name, t in message_tokens if name == "ToolMessage"),
            },
            "largest_messages": sorted(message_tokens, key=lambda x: x[1], reverse=True)[:5],
        }
    
    def clear_cache(self) -> None:
        """Clear the token cache."""
        self._token_cache.clear()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def create_memory_manager(
    max_context_tokens: int = 100000,
    max_tool_output_tokens: int = 8000,
) -> MemoryManager:
    """Create a memory manager with common defaults.
    
    Args:
        max_context_tokens: Maximum context window tokens.
        max_tool_output_tokens: Maximum tokens per tool output.
        
    Returns:
        Configured MemoryManager.
    """
    config = MemoryConfig(
        max_context_tokens=max_context_tokens,
        truncation=TruncationConfig(
            max_tool_output_tokens=max_tool_output_tokens,
        ),
    )
    return MemoryManager(config)


# ============================================================================
# SMART MEMORY MANAGER (with LLM summarization)
# ============================================================================

class SmartMemoryManager(MemoryManager):
    """Enhanced memory manager with LLM-based summarization.
    
    Extends MemoryManager with intelligent summarization capabilities:
    - Summarizes large tool outputs instead of truncating
    - Creates conversation summaries for older messages
    - Maintains rolling context summary
    """
    
    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        llm: Optional[Any] = None,
    ):
        """Initialize smart memory manager.
        
        Args:
            config: Memory configuration.
            llm: LangChain LLM instance for summarization.
        """
        super().__init__(config)
        self.llm = llm
        self._summarizer = None  # Lazy init
        
        # Rolling summary of conversation
        self.rolling_summary: Optional[str] = None
        self.rolling_summary_tokens: int = 0
        
        # Track which tool outputs have been summarized
        self._tool_summaries: Dict[int, str] = {}
        
        # Archived conversation summaries
        self._archived_summaries: List[Dict[str, Any]] = []
    
    @property
    def summarizer(self):
        """Lazy-load summarizer to avoid circular imports."""
        if self._summarizer is None:
            from .summarizer import Summarizer
            self._summarizer = Summarizer(
                llm=self.llm,
                estimate_tokens_fn=estimate_tokens,
            )
        return self._summarizer
    
    def set_llm(self, llm: Any) -> None:
        """Set or update the LLM for summarization.
        
        Args:
            llm: LangChain LLM instance.
        """
        self.llm = llm
        if self._summarizer:
            self._summarizer.set_llm(llm)
    
    async def prepare_tool_output_async(
        self,
        tool_name: str,
        output: str,
    ) -> str:
        """Prepare tool output with intelligent summarization.
        
        Uses LLM to summarize large outputs instead of truncating.
        
        Args:
            tool_name: Name of the tool.
            output: Raw tool output.
            
        Returns:
            Processed (possibly summarized) output.
        """
        output_tokens = estimate_tokens(output)
        
        # If small enough, return as-is
        if output_tokens <= self.config.tool_output_summary_threshold:
            return output
        
        # Check cache
        cache_key = hash(f"{tool_name}:{output}")
        if cache_key in self._tool_summaries:
            return self._tool_summaries[cache_key]
        
        # Try LLM summarization if enabled and available
        if self.config.enable_summarization and self.llm is not None:
            try:
                result = await self.summarizer.summarize_tool_output(
                    tool_name,
                    output,
                    max_output_tokens=self.config.max_tool_output_tokens,
                )
                
                if result.success:
                    logger.info(
                        f"Summarized {tool_name} output: "
                        f"{result.original_tokens} -> {result.summary_tokens} tokens "
                        f"({result.compression_ratio:.1%} compression)"
                    )
                    self._tool_summaries[cache_key] = result.summary
                    return result.summary
                    
            except Exception as e:
                logger.warning(f"Tool output summarization failed: {e}")
        
        # Fall back to truncation
        return truncate_tool_output(output, self.config.truncation)
    
    def prepare_tool_output(self, output: str, tool_name: str = "tool") -> str:
        """Synchronous wrapper for tool output preparation.
        
        Tries async summarization, falls back to truncation.
        
        Args:
            output: Raw tool output.
            tool_name: Name of the tool.
            
        Returns:
            Processed output.
        """
        output_tokens = estimate_tokens(output)
        
        # If small enough, return as-is
        if output_tokens <= self.config.tool_output_summary_threshold:
            return output
        
        # Try to run async summarization
        if self.config.enable_summarization and self.llm is not None:
            try:
                # Check if we're already in an event loop
                try:
                    loop = asyncio.get_running_loop()
                    # We're in an async context, can't use asyncio.run
                    # Fall back to sync method
                except RuntimeError:
                    # No running loop, safe to use asyncio.run
                    return asyncio.run(
                        self.prepare_tool_output_async(tool_name, output)
                    )
            except Exception as e:
                logger.debug(f"Async summarization unavailable: {e}")
        
        # Fall back to truncation
        return truncate_tool_output(output, self.config.truncation)
    
    async def compress_messages_async(
        self,
        messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        """Compress messages using intelligent summarization.
        
        Strategy:
        1. Keep recent messages intact
        2. Summarize older messages into a context summary
        3. Insert summary as a SystemMessage at the start
        
        Args:
            messages: All conversation messages.
            
        Returns:
            Compressed message list.
        """
        total_tokens = self.estimate_total_tokens(messages)
        
        # Check if compression is needed
        if total_tokens <= self.config.max_context_tokens:
            return messages
        
        if len(messages) < self.config.summary_threshold:
            # Not enough messages to summarize meaningfully
            return self.prune_messages(messages)
        
        logger.info(
            f"Compressing conversation: {len(messages)} messages, "
            f"{total_tokens} tokens -> target {self.config.target_context_tokens}"
        )
        
        # Find split point - keep recent messages intact
        recent_count = self.config.min_recent_messages
        
        # Also preserve the first human message (original task)
        first_human_idx = 0
        for i, msg in enumerate(messages):
            if isinstance(msg, HumanMessage):
                first_human_idx = i
                break
        
        # Messages to keep vs summarize
        split_idx = len(messages) - recent_count
        
        # Ensure we have something to summarize
        if split_idx <= first_human_idx + 1:
            return self.prune_messages(messages)
        
        # Get the original task
        original_task = messages[first_human_idx] if first_human_idx < len(messages) else None
        
        # Messages to summarize (between first message and recent)
        to_summarize = messages[first_human_idx + 1:split_idx]
        recent_messages = messages[split_idx:]
        
        # Summarize old messages
        if self.config.enable_summarization and self.llm is not None:
            try:
                result = await self.summarizer.summarize_messages(
                    to_summarize,
                    target_tokens=self.config.target_context_tokens // 4,
                )
                
                summary_text = result.summary
                
                # Archive the summary
                self._archived_summaries.append({
                    "messages_count": len(to_summarize),
                    "original_tokens": result.original_tokens,
                    "summary_tokens": result.summary_tokens,
                    "summary": summary_text[:500],  # Store truncated for reference
                })
                
                logger.info(
                    f"Summarized {len(to_summarize)} messages: "
                    f"{result.original_tokens} -> {result.summary_tokens} tokens"
                )
                
            except Exception as e:
                logger.warning(f"Message summarization failed: {e}")
                summary_text = self._create_fallback_summary(to_summarize)
        else:
            summary_text = self._create_fallback_summary(to_summarize)
        
        # Build compressed message list
        compressed = []
        
        # Add summary as context
        summary_msg = SystemMessage(
            content=f"[Previous conversation context - {len(to_summarize)} messages summarized]\n\n{summary_text}"
        )
        compressed.append(summary_msg)
        
        # Add original task
        if original_task:
            compressed.append(original_task)
        
        # Add recent messages
        compressed.extend(recent_messages)
        
        # Update rolling summary
        self.rolling_summary = summary_text
        self.rolling_summary_tokens = estimate_tokens(summary_text)
        
        new_tokens = self.estimate_total_tokens(compressed)
        logger.info(
            f"Compression complete: {total_tokens} -> {new_tokens} tokens "
            f"({len(messages)} -> {len(compressed)} messages)"
        )
        
        # Clear and rebuild token cache
        self._token_cache.clear()
        for msg in compressed:
            self._token_cache[id(msg)] = estimate_message_tokens(msg)
        
        return compressed
    
    def compress_messages(
        self,
        messages: List[BaseMessage],
    ) -> List[BaseMessage]:
        """Synchronous wrapper for message compression.
        
        Args:
            messages: All conversation messages.
            
        Returns:
            Compressed message list.
        """
        total_tokens = self.estimate_total_tokens(messages)
        
        if total_tokens <= self.config.max_context_tokens:
            return messages
        
        # Try async compression
        if self.config.enable_summarization and self.llm is not None:
            try:
                try:
                    loop = asyncio.get_running_loop()
                    # Already in async context, can't use asyncio.run
                except RuntimeError:
                    # Safe to use asyncio.run
                    return asyncio.run(self.compress_messages_async(messages))
            except Exception as e:
                logger.warning(f"Async compression unavailable: {e}")
        
        # Fall back to simple pruning
        return self.prune_messages(messages)
    
    def _create_fallback_summary(self, messages: List[BaseMessage]) -> str:
        """Create a summary without LLM using extraction.
        
        Args:
            messages: Messages to summarize.
            
        Returns:
            Extracted summary.
        """
        parts = []
        
        # Extract key information from each message type
        actions = []
        results = []
        files_mentioned = set()
        
        for msg in messages:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            
            if isinstance(msg, HumanMessage):
                # User requests
                parts.append(f"• User requested: {content[:150]}")
                
            elif isinstance(msg, AIMessage):
                # Extract first meaningful sentence
                sentences = content.split('.')
                if sentences:
                    actions.append(sentences[0][:100])
                
                # Look for file references
                import re
                file_patterns = re.findall(r'[\w/]+\.(py|js|ts|json|yaml|yml|md|txt|html|css)', content)
                files_mentioned.update(file_patterns)
                
            elif isinstance(msg, ToolMessage):
                # Extract outcomes
                content_lower = content.lower()
                if any(x in content_lower for x in ['success', 'created', '✓', 'done']):
                    results.append("✓ " + content[:80])
                elif any(x in content_lower for x in ['error', 'failed', '✗']):
                    results.append("✗ " + content[:80])
        
        # Build summary
        summary_parts = ["Summary of previous conversation:"]
        
        if parts:
            summary_parts.extend(parts[:5])  # Limit to 5 user requests
        
        if actions:
            summary_parts.append("\nActions taken:")
            for action in actions[:8]:
                summary_parts.append(f"  - {action}")
        
        if results:
            summary_parts.append("\nResults:")
            for result in results[:10]:
                summary_parts.append(f"  {result}")
        
        if files_mentioned:
            summary_parts.append(f"\nFiles involved: {', '.join(list(files_mentioned)[:15])}")
        
        return '\n'.join(summary_parts)
    
    async def update_rolling_summary_async(
        self,
        new_messages: List[BaseMessage],
    ) -> None:
        """Update rolling summary with new messages.
        
        Args:
            new_messages: New messages to incorporate.
        """
        if not new_messages:
            return
        
        if self.config.enable_summarization and self.llm is not None:
            try:
                self.rolling_summary = await self.summarizer.update_rolling_summary(
                    new_messages,
                    max_summary_tokens=3000,
                )
                self.rolling_summary_tokens = estimate_tokens(self.rolling_summary)
            except Exception as e:
                logger.warning(f"Rolling summary update failed: {e}")
    
    def get_stats(self, messages: List[BaseMessage]) -> Dict[str, Any]:
        """Get memory statistics including summarization info.
        
        Args:
            messages: Current message list.
            
        Returns:
            Dictionary of statistics.
        """
        stats = super().get_stats(messages)
        
        # Add summarization stats
        stats["summarization_enabled"] = self.config.enable_summarization
        stats["llm_available"] = self.llm is not None
        stats["rolling_summary_tokens"] = self.rolling_summary_tokens
        stats["archived_summaries"] = len(self._archived_summaries)
        stats["tool_summaries_cached"] = len(self._tool_summaries)
        
        # Calculate potential savings
        if self._archived_summaries:
            total_original = sum(s["original_tokens"] for s in self._archived_summaries)
            total_summarized = sum(s["summary_tokens"] for s in self._archived_summaries)
            stats["tokens_saved_by_summarization"] = total_original - total_summarized
        
        return stats
    
    def clear_cache(self) -> None:
        """Clear all caches and summaries."""
        super().clear_cache()
        self._tool_summaries.clear()
        self._archived_summaries.clear()
        self.rolling_summary = None
        self.rolling_summary_tokens = 0
        if self._summarizer:
            self._summarizer.clear()


def create_smart_memory_manager(
    llm: Optional[Any] = None,
    max_context_tokens: int = 100000,
    enable_summarization: bool = True,
) -> SmartMemoryManager:
    """Create a smart memory manager with LLM summarization.
    
    Args:
        llm: LangChain LLM instance for summarization.
        max_context_tokens: Maximum context window tokens.
        enable_summarization: Whether to enable LLM summarization.
        
    Returns:
        Configured SmartMemoryManager.
    """
    config = MemoryConfig(
        max_context_tokens=max_context_tokens,
        enable_summarization=enable_summarization,
    )
    return SmartMemoryManager(config=config, llm=llm)
