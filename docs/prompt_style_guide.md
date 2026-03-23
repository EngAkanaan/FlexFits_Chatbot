# FLEX Fits V2 Prompt Style Guide

## Purpose
This guide standardizes prompt and policy writing for FLEX Fits AI Shoe Store Assistant (persona: Lina).

## Tone
1. Use friendly, practical customer support language.
2. Keep replies concise and direct.
3. Support Lebanese-aware small talk when greeting or rapport is requested.
4. Keep ordering and policy outputs deterministic.

## Mandatory Writing Rules
1. Start every instruction with an imperative verb.
2. Write one idea per numbered line.
3. Use explicit triggers with this form: Do X only when Y occurs.
4. Use exact defined terms consistently.
5. Keep policy sections independent and non-overlapping.
6. Ask one targeted clarifying question only when required fields are missing.
7. Avoid undefined terms, slang, and idioms in policy text.
8. Avoid weak trigger words such as suggest, maybe, if needed.

## XML Policy Structure
Every policy file must follow this shape:

1. `<policy_name>` root tag.
2. `<instructions>` numbered rules.
3. `<triggers>` explicit activation conditions.
4. `<constraints>` hard boundaries.
5. `<output_contract>` response formatting and behavior.
6. Optional `<example>` only when ambiguity remains high.

## Definitions Governance
1. Define each business term once in `system_policy.xml`.
2. Reuse exact term spelling across all files.
3. Do not introduce aliases for existing defined terms.

## Determinism Rules
1. Keep deterministic commerce logic authoritative over prompt wording.
2. Never invent product, stock, size, price, sale, or policy facts.
3. Never override order FSM stage transitions through prompt behavior.
4. Keep intent-to-policy mapping one-to-one for primary routing.

## Validation Checklist
1. XML tags are closed and well-formed.
2. Numbered rules are sequential and unambiguous.
3. Trigger conditions are explicit and testable.
4. Policy does not conflict with sale rule (`price == 39`) or total formula (`subtotal + 4`).
5. Output contract is concise and customer-friendly.
