from __future__ import annotations

import os
import re
from typing import Protocol


class LlmClient(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        ...


class DeterministicLlmClient:
    """Deterministic support text generator for policy/RAG scaffolding."""

    @staticmethod
    def _extract_intent(user_prompt: str) -> str:
        match = re.search(r"<intent>(.*?)</intent>", user_prompt, re.IGNORECASE | re.DOTALL)
        if not match:
            return "fallback"
        return match.group(1).strip().lower()

    @staticmethod
    def _extract_message(user_prompt: str) -> str:
        match = re.search(r"<message>(.*?)</message>", user_prompt, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return match.group(1).strip().lower()

    @staticmethod
    def _is_arabic_or_lebanese(text: str) -> bool:
        if not text:
            return False
        if re.search(r"[\u0600-\u06FF]", text):
            return True
        lebanese_tokens = [
            "marhaba",
            "mar7aba",
            "salam",
            "alaykom",
            "alaykum",
            "bade",
            "badde",
            "shu",
            "se3er",
            "kifak",
            "kifek",
            "hayda",
            "hal",
            "akhbar",
        ]
        return any(token in text for token in lebanese_tokens)

    @staticmethod
    def _pick_variant(seed_text: str, options: list[str]) -> str:
        if not options:
            return ""
        index = abs(hash(seed_text)) % len(options)
        return options[index]

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        intent = self._extract_intent(user_prompt)
        message = self._extract_message(user_prompt)
        arabic_mode = self._is_arabic_or_lebanese(message)

        if intent == "greeting":
            if arabic_mode:
                return self._pick_variant(
                    user_prompt,
                    [
                        "مرحبا. أنا نِلي من FlexFits. كيف فيني ساعدك اليوم؟",
                        "أهلا وسهلا! أنا نِلي من FlexFits. خبرني شو نوع الشوز يلي بدك ياه.",
                        "سلام! أنا نِلي. قديش مقاسك وشو الستايل يلي بتحبه؟",
                    ],
                )
            return self._pick_variant(
                user_prompt,
                [
                    "Marhaba. I am Nelly from FlexFits. How can I help you today?",
                    "Hi. Nelly here from FlexFits. What are you looking for today?",
                    "Hey, welcome to FlexFits. Tell me what kind of shoes you want.",
                ],
            )

        if intent == "small_talk":
            if arabic_mode:
                return self._pick_variant(
                    user_prompt,
                    [
                        "تمام الحمدلله. شو بتحب شوفلك اليوم؟",
                        "كله منيح. خبرني شو بدك من موديل أو مقاس.",
                    ],
                )
            return self._pick_variant(
                user_prompt,
                [
                    "I am good, thank you. How can I help with shoes today?",
                    "All good here. Tell me what style or size you want.",
                ],
            )

        if intent == "list_categories":
            return "We sell Running Shoes, Casual Shoes, Hiking Shoes, Lifestyle Sneakers, and Basketball Shoes."

        if intent == "help":
            if arabic_mode:
                return "أنا نِلي من FlexFits. فيني ساعدك بالموديلات، المقاسات، الأسعار، التوصيل، والترجيع."
            return "I am Nelly from FlexFits. I can help with availability, sizes, prices, delivery, and returns."

        if intent == "thank_you":
            return "You're welcome. I am here if you need anything else."

        if intent == "goodbye":
            return "Goodbye. Thank you for visiting FlexFits."

        if intent == "payment_methods":
            return "We accept USD, LBP, OMT Wallet, and WHISH."

        if intent == "delivery_timeline":
            return "Delivery across Lebanon takes 4-6 business days. Delivery fee is $4."

        if intent in {"about_authenticity", "about_location", "about_showroom", "about_exchange", "about_refund"}:
            return "We are an online-only Lebanese store with authentic products. For exchange or refund details, I can connect you with support."

        if intent == "delivery_info":
            return "Yes, we deliver across Lebanon. Delivery add-on is $4."

        if intent == "return_policy":
            return "I can help with return and exchange policy details based on our approved rules."

        if intent == "sale_query":
            return "Sure. Here are our in-stock sale options at $39."

        if intent == "human_handoff":
            return "Of course. I can connect you with human support."

        if intent == "create_order":
            if arabic_mode:
                return "أكيد. خبرني أي موديل وأي مقاس بدك، وأنا بكمّل معك الطلب خطوة خطوة."
            return "Sure. Which model and size do you want to order? I will walk you through it."

        if intent == "show_all":
            if arabic_mode:
                return "أكيد. هيدي كل الخيارات يلي لقيتها إلك."
            return "Sure. Here are all the options I found."

        if intent in {"search_product", "check_availability", "check_category_availability", "recommend", "check_price"}:
            if arabic_mode:
                if intent == "check_price":
                    return "أكيد. هلق بخبرك السعر حسب الموديل والمقاس المتوفر."
                return "أكيد. هيدي النتائج يلي لقيتها حسب طلبك."
            return "Sure. Here is what I found for your request."

        if arabic_mode:
            return "فيني ساعدك بالموديلات، المقاسات، الأسعار، التوصيل، والترجيع. خبرني شو بدك."
        return "I can help with shoes, sizes, prices, delivery, and returns. Tell me what you need."


def llm_enabled() -> bool:
    raw = os.getenv("FLEX_ENABLE_LLM", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_llm_client() -> LlmClient:
    # Default remains deterministic for safety and reproducibility.
    return DeterministicLlmClient()
