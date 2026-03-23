@echo off
REM Telegram Bot + Supabase Integration Smoke Test Setup
REM This script sets up environment variables and runs the bot for testing

ECHO Telegram Bot + Supabase Integration Smoke Test
ECHO =============================================
ECHO.
ECHO This script will configure and start the Telegram bot with Supabase persistence.
ECHO.

REM Check for environment variables
IF NOT DEFINED FLEX_TELEGRAM_BOT_TOKEN (
    ECHO ERROR: FLEX_TELEGRAM_BOT_TOKEN is not set!
    ECHO Please set it before running this script:
    ECHO   set FLEX_TELEGRAM_BOT_TOKEN=your-token-here
    EXIT /B 1
)

IF NOT DEFINED SUPABASE_URL (
    ECHO ERROR: SUPABASE_URL is not set!
    ECHO Please set it before running this script:
    ECHO   set SUPABASE_URL=https://your-project.supabase.co
    EXIT /B 1
)

IF NOT DEFINED SUPABASE_SERVICE_ROLE_KEY (
    ECHO ERROR: SUPABASE_SERVICE_ROLE_KEY is not set!
    ECHO Please set it before running this script:
    ECHO   set SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
    EXIT /B 1
)

ECHO Configuration verified:
ECHO - Telegram Token: [CONFIGURED]
ECHO - Supabase URL: %SUPABASE_URL%
ECHO - Service Role Key: [CONFIGURED]
ECHO.
ECHO IMPORTANT SECURITY NOTE:
ECHO The Telegram bot token that was shared in chat should be rotated immediately in BotFather!
ECHO.
ECHO Starting Telegram bot in polling mode...
ECHO Press Ctrl+C to stop the bot.
ECHO.

cd /d "%~dp0"
python main.py --mode telegram

PAUSE
