"""
Bedrock LLM Summarization Module
==================================

Integrates with Amazon Bedrock to generate chat summaries, detect
user sentiment, and extract topic keywords.

Features:
    - Multi-region round-robin for higher throughput
    - Adaptive retry with exponential backoff for throttling
    - Content filter detection with simplified prompt retry
    - Structured prompt engineering with strict PII rules
"""

import itertools
import re
import time
from typing import Dict, List, Tuple

import boto3
from botocore.config import Config

from .pii import sanitize_text


class ChatSummarizer:
    """
    Summarizes chat text using Amazon Bedrock's Converse API.

    Distributes requests across multiple regions in a round-robin pattern
    to maximize throughput beyond single-region limits.

    Args:
        regions: List of AWS regions to use (e.g., ["us-east-1", "us-west-2"]).
        model_ids: Mapping of region → model ID or inference profile.
        max_workers: Thread count (used for connection pool sizing).

    Example:
        >>> summarizer = ChatSummarizer(
        ...     regions=["us-east-1", "us-west-2"],
        ...     model_ids={"us-east-1": "amazon.nova-micro-v1:0", ...},
        ...     max_workers=100,
        ... )
        >>> result = summarizer.summarize("Turn 1:\\nUser: Hi\\nBot: Hello!")
        >>> result["sentiment"]
        'Positive'
    """

    def __init__(self, regions: List[str], model_ids: Dict[str, str], max_workers: int):
        boto_config = Config(
            max_pool_connections=max_workers + 5,
            retries={"max_attempts": 5, "mode": "adaptive"},
        )

        self._clients: List[Tuple] = []
        for region in regions:
            client = boto3.client("bedrock-runtime", region_name=region, config=boto_config)
            model_id = model_ids.get(region, "us.amazon.nova-micro-v1:0")
            self._clients.append((client, model_id))

        self._cycle = itertools.cycle(self._clients)

    def summarize(self, chat_text: str) -> Dict[str, str]:
        """
        Generate a summary, sentiment, and keywords for a chat.

        Pipeline:
            1. Sanitize PII from input text
            2. Truncate to 8000 characters
            3. Send structured prompt to Bedrock
            4. Parse response into fields
            5. Retry with simpler prompt if content filtered

        Args:
            chat_text: Full chat text (all turns concatenated).

        Returns:
            Dict with keys:
                - summary: Flow summary (max 1000 chars)
                - sentiment: "Positive", "Negative", or "Neutral"
                - keywords: Comma-separated topics (max 500 chars)
        """
        if not chat_text or chat_text.strip() == "":
            return {"summary": "NO_CONTENT", "sentiment": "Neutral", "keywords": ""}

        client, model_id = next(self._cycle)

        sanitized = sanitize_text(chat_text)
        truncated = sanitized[:8000]

        prompt = self._build_prompt(truncated)
        payload = self._build_payload(prompt)

        content_filtered = False
        max_retries = 5

        for attempt in range(max_retries):
            try:
                response = client.converse(modelId=model_id, **payload)

                stop_reason = response.get("stopReason", "")
                if "content_filtered" in stop_reason.lower() or "guardrail" in stop_reason.lower():
                    content_filtered = True
                    break

                text = response["output"]["message"]["content"][0]["text"].strip()

                if "content filters" in text.lower():
                    content_filtered = True
                    break

                return self._parse_response(text)

            except Exception as e:
                error_str = str(e)
                if "ThrottlingException" in error_str:
                    time.sleep(2 ** attempt)
                elif "content" in error_str.lower() and "filter" in error_str.lower():
                    content_filtered = True
                    break
                elif attempt == max_retries - 1:
                    return {"summary": f"ERROR: {error_str[:200]}", "sentiment": "ERROR", "keywords": ""}
                else:
                    time.sleep(1)

        if content_filtered:
            return self._retry_simple(client, model_id, truncated)

        return {"summary": "ERROR: max retries exceeded", "sentiment": "ERROR", "keywords": ""}

    def _build_prompt(self, text: str) -> str:
        """Construct the structured summarization prompt."""
        return (
            "You are an analytics tool summarizing chatbot chats. "
            "STRICT RULES:\n"
            "- Do NOT include any names, email addresses, phone numbers, account numbers, "
            "order IDs, product IDs, URLs, physical addresses, or financial details in your summary.\n"
            "- Do NOT reproduce any offensive, abusive, or inappropriate language.\n"
            "- Describe all topics in generic terms.\n"
            "- If the chat contains sensitive content, summarize the intent without details.\n\n"
            "Analyze this multi-turn chat between a User and a Bot. "
            "The turns are in chronological order.\n\n"
            "Provide:\n"
            "1. SUMMARY: In under 150 words, describe how the chat flowed — "
            "what the user initially asked, how the bot responded, "
            "how the topic evolved across turns, and what the final outcome or resolution was.\n"
            "2. SENTIMENT: The user's overall sentiment based ONLY on the user's messages. "
            "Consider signals like: unresolved issues, repeated attempts, terse replies, "
            "expressions of frustration, or being redirected without resolution. "
            "Exactly one of: Positive, Negative, Neutral\n"
            "3. KEYWORDS: Extract 3-8 keywords or short phrases from the bot's responses "
            "that capture the main topics discussed. Use generic terms, no PII or IDs.\n\n"
            "Respond in this exact format:\n"
            "SUMMARY: <your summary>\n"
            "SENTIMENT: <Positive or Negative or Neutral>\n"
            "KEYWORDS: <keyword1, keyword2, keyword3, ...>\n\n"
            f"Chat:\n{text}"
        )

    def _build_payload(self, prompt: str) -> dict:
        """Build the Bedrock Converse API request payload."""
        return {
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 250, "temperature": 0.2, "topP": 0.9},
        }

    def _parse_response(self, text: str) -> Dict[str, str]:
        """Parse structured SUMMARY/SENTIMENT/KEYWORDS response."""
        summary = text
        sentiment = "Neutral"
        keywords = ""

        if "SUMMARY:" in text and "SENTIMENT:" in text:
            if "KEYWORDS:" in text:
                parts = text.split("KEYWORDS:")
                keywords = parts[1].strip().split("\n")[0].strip()
                before_keywords = parts[0]
            else:
                before_keywords = text

            if "SENTIMENT:" in before_keywords:
                sent_parts = before_keywords.split("SENTIMENT:")
                summary = sent_parts[0].replace("SUMMARY:", "").strip()
                sentiment_raw = sent_parts[1].strip().split("\n")[0].strip().rstrip(".").lower()
                sentiment = self._classify_sentiment(sentiment_raw)

        return {"summary": summary[:1000], "sentiment": sentiment, "keywords": keywords[:500]}

    def _classify_sentiment(self, raw: str) -> str:
        """Map raw sentiment text to Positive/Negative/Neutral."""
        negative_signals = [
            "negative", "frustrated", "frustration", "dissatisfied",
            "unhappy", "angry", "upset", "disappointed", "annoyed",
            "irritated", "unresolved", "not satisfied", "displeased",
        ]
        if "positive" in raw and "not positive" not in raw:
            return "Positive"
        elif any(signal in raw for signal in negative_signals):
            return "Negative"
        return "Neutral"

    def _retry_simple(self, client, model_id: str, text: str) -> Dict[str, str]:
        """Retry with a simplified prompt after content filtering."""
        try:
            simple_text = re.sub(r'[*#\\_\[\](){}|`~]', '', text)
            simple_text = re.sub(r'\s+', ' ', simple_text)[:4000]

            prompt = (
                "Briefly summarize this chat in 2-3 sentences. "
                "What was the topic? Was the user satisfied, frustrated, or neutral? "
                "Respond in this exact format:\n"
                "SUMMARY: <your summary>\n"
                "SENTIMENT: <Positive or Negative or Neutral>\n\n"
                f"{simple_text}"
            )
            payload = {
                "messages": [{"role": "user", "content": [{"text": prompt}]}],
                "inferenceConfig": {"maxTokens": 200, "temperature": 0.1, "topP": 0.9},
            }

            response = client.converse(modelId=model_id, **payload)
            text = response["output"]["message"]["content"][0]["text"].strip()

            if "content filters" in text.lower():
                return {"summary": "CONTENT_FILTERED", "sentiment": "Neutral", "keywords": ""}

            sentiment = "Neutral"
            if "SENTIMENT:" in text:
                raw = text.split("SENTIMENT:")[1].strip().split("\n")[0].strip().rstrip(".").lower()
                sentiment = self._classify_sentiment(raw)

            summary = (
                text.split("SENTIMENT:")[0].replace("SUMMARY:", "").strip()
                if "SENTIMENT:" in text else text
            )
            return {"summary": summary[:1000], "sentiment": sentiment, "keywords": ""}

        except Exception:
            return {"summary": "CONTENT_FILTERED", "sentiment": "Neutral", "keywords": ""}
