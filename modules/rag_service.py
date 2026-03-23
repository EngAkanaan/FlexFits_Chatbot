from __future__ import annotations

from typing import Dict, List

from .llm_client import get_llm_client
from .prompts import build_prompt_package, resolve_policy_name
from .retriever import Retriever
from .response_generator import generate_support_response


class RagService:
    def __init__(self, retriever: Retriever | None = None) -> None:
        self.retriever = retriever or Retriever()
        self.llm_client = get_llm_client()

    def build_context(
        self,
        user_message: str,
        intent: str,
        color_hint: str | None = None,
        gender_hint: str | None = None,
        top_k: int = 3,
    ) -> List[str]:
        faq_hits = self.retriever.retrieve_faq(user_message=user_message, intent=intent, top_k=3)
        inventory_hits = self.retriever.retrieve_inventory_snippets(
            user_message=user_message,
            intent=intent,
            top_k=top_k,
            color_hint=color_hint,
            gender_hint=gender_hint,
        )

        # Keep deterministic ordering while removing duplicate chunks.
        merged = faq_hits + inventory_hits
        seen = set()
        deduped: List[str] = []
        for chunk in merged:
            if chunk in seen:
                continue
            seen.add(chunk)
            deduped.append(chunk)
        return deduped

    def generate_support_reply(
        self,
        user_message: str,
        intent: str,
        state_entities: Dict[str, object],
        color_hint: str | None = None,
        gender_hint: str | None = None,
        top_k: int = 3,
    ) -> Dict[str, object]:
        policy_file = resolve_policy_name(intent)
        collection_query = "collection" in user_message.lower()
        effective_top_k = 30 if collection_query else top_k
        context_chunks = self.build_context(
            user_message=user_message,
            intent=intent,
            color_hint=color_hint,
            gender_hint=gender_hint,
            top_k=effective_top_k,
        )

        prompt_package = build_prompt_package(
            user_message=user_message,
            intent=intent,
            state_entities=state_entities,
            context_chunks=context_chunks,
        )

        base_reply = self.llm_client.generate(
            system_prompt=prompt_package.get("system", ""),
            user_prompt=prompt_package.get("user", ""),
        )
        final_reply = generate_support_response(
            base_reply,
            intent=intent,
            highlights=context_chunks,
            show_all=collection_query,
        )

        return {
            "reply": final_reply,
            "context_chunks": context_chunks,
            "prompt_package": prompt_package,
            "metadata": {
                "intent": intent,
                "policy_file": policy_file,
                "faq_chunk_count": len([c for c in context_chunks if "| type:" not in c]),
                "inventory_chunk_count": len([c for c in context_chunks if "| type:" in c]),
            },
        }
