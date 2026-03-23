# FlexFits ChatBot

A production-oriented AI shoe store assistant that supports product discovery, cart flow, and checkout through local chat and Telegram, with optional Supabase order persistence.

## Features
- Intent-driven product search and recommendation flow
- Retrieval-augmented responses using inventory and knowledge snippets
- Cart and checkout conversation flow with validation
- Telegram bot mode for customer support and ordering
- Optional Supabase integration for order and order-item storage
- Automated tests for pricing, filtering, RAG, and conversation behavior

## Tech Stack
- Python 3.10+
- Standard library HTTP client (`urllib`) for API calls
- Telegram Bot API (long polling)
- Supabase REST API
- Pytest for testing

## Installation and Setup
1. Clone the repository and enter the project folder.
2. Create and activate a virtual environment.
3. Install dependencies:
   - `pip install -r requirements.txt` (if present)
   - or install project dependencies manually.
4. Configure environment variables:
   - `FLEX_TELEGRAM_BOT_TOKEN` (required for Telegram mode)
   - `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` (optional, for persistence)

## Usage
- Local chat mode:
  - `python main.py --mode local`
- Telegram mode:
  - `python main.py --mode telegram`

## Project Structure
- `main.py`: application entry point
- `telegram_bot.py`: Telegram runtime and conversation handling
- `local_chat.py`: local CLI chat runtime
- `modules/`: core business logic (routing, RAG, memory, Supabase gateway)
- `data/`: static domain data (inventory, intents)
- `knowledge/`: FAQ and policy files used by retrieval/prompting
- `tests/`: automated test suite
