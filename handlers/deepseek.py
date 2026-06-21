import time
from os import environ

from anthropic import Anthropic
from expiringdict import ExpiringDict
from telebot import TeleBot
from telebot.types import Message
from telegramify_markdown import markdownify

from ._utils import bot_reply_first, bot_reply_markdown, enrich_text_with_urls, logger

DEEPSEEK_API_KEY = environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = environ.get("DEEPSEEK_BASE_URL") or "https://api.openmodel.ai"
if DEEPSEEK_BASE_URL.endswith("/v1"):
    DEEPSEEK_BASE_URL = DEEPSEEK_BASE_URL[:-3]
DEEPSEEK_MODEL = environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"
DEEPSEEK_PRO_MODEL = environ.get("DEEPSEEK_PRO_MODEL") or "deepseek-v4-pro"

if DEEPSEEK_API_KEY:
    client = Anthropic(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

# Global history cache
deepseek_player_dict = ExpiringDict(max_len=1000, max_age_seconds=600)
deepseek_pro_player_dict = ExpiringDict(max_len=1000, max_age_seconds=600)

DEEPSEEK_SYSTEM_PROMPT = "You are a helpful assistant. Always respond in the same language as the user's query (usually Indonesian or English). Keep your tone friendly and natural."



def deepseek_handler(message: Message, bot: TeleBot) -> None:
    """deepseek : /deepseek <question>"""
    m = message.text.strip()

    player_message = []
    if str(message.from_user.id) not in deepseek_player_dict:
        deepseek_player_dict[str(message.from_user.id)] = player_message
    else:
        player_message = deepseek_player_dict[str(message.from_user.id)]

    if m.strip() == "clear":
        bot.reply_to(message, "just clear your deepseek messages history")
        player_message.clear()
        return
    if m[:9].lower() == "/deepseek":
        m = m[9:].strip()
    if m[:4].lower() == "new ":
        m = m[4:].strip()
        player_message.clear()
    m = enrich_text_with_urls(m)

    who = "DeepSeek"
    reply_id = bot_reply_first(message, who, bot)

    player_message.append({"role": "user", "content": m})
    if len(player_message) > 10:
        player_message = player_message[2:]

    deepseek_reply_text = ""
    try:
        if len(player_message) > 2:
            if player_message[-1]["role"] == player_message[-2]["role"]:
                player_message.pop()
        r = client.messages.create(
            max_tokens=4096,
            messages=player_message,
            model=DEEPSEEK_MODEL,
            system=DEEPSEEK_SYSTEM_PROMPT,
        )
        if not r.content:
            deepseek_reply_text = f"{who} did not answer."
            player_message.pop()
        else:
            texts = []
            for block in r.content:
                b_type = getattr(block, "type", None)
                if b_type == "text":
                    texts.append(getattr(block, "text", ""))
                elif b_type == "thinking":
                    thinking_text = getattr(block, "thinking", "") or (
                        block.model_extra.get("thinking")
                        if hasattr(block, "model_extra") and block.model_extra
                        else ""
                    )
                    if thinking_text:
                        formatted = "\n".join(f"> {line}" for line in thinking_text.strip().split("\n"))
                        texts.append(f"*{who} Thinking:*\n{formatted}\n\n")
            
            deepseek_reply_text = "".join(texts).strip()
            
            # Save plain text to history
            player_message.append(
                {
                    "role": r.role,
                    "content": deepseek_reply_text,
                }
            )

    except Exception:
        logger.exception("DeepSeek handler error")
        bot.reply_to(message, "answer wrong maybe up to the max token")
        player_message.pop()
        return

    bot_reply_markdown(reply_id, who, deepseek_reply_text, bot)


def deepseek_pro_handler(message: Message, bot: TeleBot) -> None:
    """deepseek_pro : /deepseek_pro <question>"""
    m = message.text.strip()

    player_message = []
    if str(message.from_user.id) not in deepseek_pro_player_dict:
        deepseek_pro_player_dict[str(message.from_user.id)] = player_message
    else:
        player_message = deepseek_pro_player_dict[str(message.from_user.id)]

    if m.strip() == "clear":
        bot.reply_to(message, "just clear your deepseek messages history")
        player_message.clear()
        return
    if m[:13].lower() == "/deepseek_pro":
        m = m[13:].strip()
    if m[:4].lower() == "new ":
        m = m[4:].strip()
        player_message.clear()
    m = enrich_text_with_urls(m)

    who = "DeepSeek Pro"
    reply_id = bot_reply_first(message, who, bot)

    player_message.append({"role": "user", "content": m})
    if len(player_message) > 10:
        player_message = player_message[2:]

    try:
        if len(player_message) > 2:
            if player_message[-1]["role"] == player_message[-2]["role"]:
                player_message.pop()
        r = client.messages.create(
            max_tokens=4096,
            messages=player_message,
            model=DEEPSEEK_PRO_MODEL,
            system=DEEPSEEK_SYSTEM_PROMPT,
            stream=True,
        )
        s = ""
        thinking_s = ""
        start = time.time()
        for e in r:
            if e.type == "content_block_delta":
                if hasattr(e.delta, "text") and e.delta.text:
                    s += e.delta.text
                elif hasattr(e.delta, "thinking") and e.delta.thinking:
                    thinking_s += e.delta.thinking
            
            if time.time() - start > 0.7:
                start = time.time()
                full_text = ""
                if thinking_s:
                    formatted_thinking = "\n".join(f"> {line}" for line in thinking_s.strip().split("\n"))
                    full_text += f"*Thinking:*\n{formatted_thinking}\n\n"
                full_text += s
                if full_text.strip():
                    bot_reply_markdown(reply_id, who, full_text, bot, split_text=False)

        final_text = ""
        if thinking_s:
            formatted_thinking = "\n".join(f"> {line}" for line in thinking_s.strip().split("\n"))
            final_text += f"*Thinking:*\n{formatted_thinking}\n\n"
        final_text += s

        if not bot_reply_markdown(reply_id, who, final_text, bot):
            player_message.clear()
            return

        player_message.append(
            {
                "role": "assistant",
                "content": markdownify(final_text),
            }
        )

    except Exception:
        logger.exception("DeepSeek Pro handler error")
        bot.reply_to(message, "answer wrong maybe up to the max token")
        player_message.clear()
        return


if DEEPSEEK_API_KEY:

    def register(bot: TeleBot) -> None:
        bot.register_message_handler(deepseek_handler, commands=["deepseek"], pass_bot=True)
        bot.register_message_handler(deepseek_handler, regexp="^deepseek:", pass_bot=True)
        bot.register_message_handler(
            deepseek_pro_handler, commands=["deepseek_pro"], pass_bot=True
        )
        bot.register_message_handler(
            deepseek_pro_handler, regexp="^deepseek_pro:", pass_bot=True
        )
