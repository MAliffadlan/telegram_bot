import re
import time
import urllib.parse
from os import environ

import requests
from bs4 import BeautifulSoup
from expiringdict import ExpiringDict
from telebot import TeleBot
from telebot.types import Message

from ._utils import bot_reply_first, bot_reply_markdown, enrich_text_with_urls, logger

# API keys config
GOOGLE_GEMINI_KEY = environ.get("GOOGLE_GEMINI_API_KEY") or environ.get("GOOGLE_GEMINI_KEY") or environ.get("GEMIMI_PRO_KEY")
DEEPSEEK_API_KEY = environ.get("DEEPSEEK_API_KEY")

# Configuration for Google Gemini if available
if GOOGLE_GEMINI_KEY:
    import google.generativeai as genai
    from google.generativeai import ChatSession
    
    genai.configure(api_key=GOOGLE_GEMINI_KEY)
    generation_config = {
        "temperature": 0.7,
        "top_p": 1,
        "top_k": 1,
        "max_output_tokens": 8192,
    }
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

# Configuration for DeepSeek if available
if DEEPSEEK_API_KEY:
    from anthropic import Anthropic
    DEEPSEEK_BASE_URL = environ.get("DEEPSEEK_BASE_URL") or "https://api.openmodel.ai"
    if DEEPSEEK_BASE_URL.endswith("/v1"):
        DEEPSEEK_BASE_URL = DEEPSEEK_BASE_URL[:-3]
    DEEPSEEK_MODEL = environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"

    deepseek_client = Anthropic(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )

# Conversation system prompt for grounding
SYSTEM_INSTRUCTION = (
    "You are a helpful AI assistant integrated with Google Search (known as Mbah Google). "
    "You have been provided with real-time web search results matching the user's query. "
    "Answer the user's query accurately using the search results. Cite the sources you use "
    "by referencing their corresponding numbers, and add clickable markdown links, for example: [Title](URL). "
    "Make sure you cite multiple distinct sources where appropriate to build trust. "
    "Always respond in the same language as the user's query (usually Indonesian or English). "
    "Keep your tone friendly, clear, and natural."
)

# Global history cache
google_player_dict = ExpiringDict(max_len=1000, max_age_seconds=600)

def search_duckduckgo(query: str, max_results: int = 5) -> list:
    """Free web search scraper using DuckDuckGo HTML search results"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        result_divs = soup.find_all("div", class_="result")
        for div in result_divs[:max_results]:
            title_a = div.find("a", class_="result__a")
            snippet_a = div.find("a", class_="result__snippet")
            if not title_a:
                continue
            title = title_a.text.strip()
            link = title_a["href"]
            snippet = snippet_a.text.strip() if snippet_a else ""
            
            if "uddg=" in link:
                parsed = urllib.parse.urlparse(link)
                qs = urllib.parse.parse_qs(parsed.query)
                if "uddg" in qs:
                    link = qs["uddg"][0]
            results.append({"title": title, "url": link, "snippet": snippet})
        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def make_new_google_gemini_convo() -> ChatSession:
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config=generation_config,
        safety_settings=safety_settings,
        system_instruction=SYSTEM_INSTRUCTION,
    )
    return model.start_chat()

def get_google_gemini_player(player_id: str) -> ChatSession:
    if player_id not in google_player_dict:
        google_player_dict[player_id] = make_new_google_gemini_convo()
    return google_player_dict[player_id]

def get_google_deepseek_player(player_id: str) -> list:
    if player_id not in google_player_dict:
        google_player_dict[player_id] = []
    return google_player_dict[player_id]

def remove_google_player(player_id: str) -> None:
    if player_id in google_player_dict:
        del google_player_dict[player_id]

def google_handler(message: Message, bot: TeleBot) -> None:
    """Mbah Google : /google <question>"""
    m = message.text.strip()
    player_id = str(message.from_user.id)

    if m.strip().lower() == "clear":
        bot.reply_to(message, "Riwayat pencarian Google Anda telah dibersihkan.")
        remove_google_player(player_id)
        return

    if m.lower().startswith("new "):
        m = m[4:].strip()
        remove_google_player(player_id)

    who = "Mbah Google"
    # Show initial typing status
    reply_id = bot_reply_first(message, who, bot)

    try:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=reply_id.message_id,
            text=f"🔍 *Mbah Google sedang mencari informasi di web untuk:* `{m}`...",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    # Perform web search
    search_results = search_duckduckgo(m, max_results=5)

    if search_results:
        context = "Web Search Results:\n"
        for idx, r in enumerate(search_results, 1):
            context += f"Source [{idx}]:\nTitle: {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}\n\n"
        formatted_prompt = (
            f"{context}"
            f"User Query: {m}\n\n"
            f"Please answer the user query based on the search results provided above. Cite your sources using [Title](URL) links."
        )
    else:
        formatted_prompt = (
            f"No search results were found for the query: '{m}'. "
            f"Please inform the user about this and answer to the best of your knowledge."
        )

    # Process using primary model (Gemini)
    if GOOGLE_GEMINI_KEY:
        player = get_google_gemini_player(player_id)
        if len(player.history) > 10:
            player.history = player.history[2:]

        try:
            r = player.send_message(formatted_prompt, stream=True)
            s = ""
            start = time.time()
            for e in r:
                s += e.text
                if time.time() - start > 1.7:
                    start = time.time()
                    bot_reply_markdown(reply_id, who, s, bot, split_text=False)

            # Format search results references beautifully
            final_response = s
            if search_results:
                refs = "\n\n🌐 **Sumber Informasi:**\n"
                for idx, r in enumerate(search_results, 1):
                    # Escape special markdown characters in title
                    title_esc = r['title'].replace('[', '\\[').replace(']', '\\]').replace('*', '\\*').replace('_', '\\_')
                    refs += f"{idx}. [{title_esc}]({r['url']})\n"
                final_response += refs

            if not bot_reply_markdown(reply_id, who, final_response, bot):
                player.history.clear()
                return

        except Exception:
            logger.exception("Gemini google search handler error")
            bot.reply_to(message, "Maaf, terjadi kesalahan saat memproses jawaban dari Gemini.")
            try:
                player.history.clear()
            except Exception:
                pass
            return

    # Fallback to DeepSeek if configured
    elif DEEPSEEK_API_KEY:
        player_message = get_google_deepseek_player(player_id)
        player_message.append({"role": "user", "content": formatted_prompt})
        if len(player_message) > 10:
            player_message = player_message[2:]

        try:
            if len(player_message) > 2:
                if player_message[-1]["role"] == player_message[-2]["role"]:
                    player_message.pop()

            r = deepseek_client.messages.create(
                max_tokens=4096,
                messages=player_message,
                model=DEEPSEEK_MODEL,
                system=SYSTEM_INSTRUCTION,
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
                
                if time.time() - start > 1.7:
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

            # Append references beautifully
            final_response = final_text
            if search_results:
                refs = "\n\n🌐 **Sumber Informasi:**\n"
                for idx, r in enumerate(search_results, 1):
                    title_esc = r['title'].replace('[', '\\[').replace(']', '\\]').replace('*', '\\*').replace('_', '\\_')
                    refs += f"{idx}. [{title_esc}]({r['url']})\n"
                final_response += refs

            if not bot_reply_markdown(reply_id, who, final_response, bot):
                player_message.clear()
                return

            # Save the plain model response without thinking block to conversation history
            player_message.append(
                {
                    "role": "assistant",
                    "content": s,
                }
            )

        except Exception:
            logger.exception("DeepSeek google search handler error")
            bot.reply_to(message, "Maaf, terjadi kesalahan saat memproses jawaban dari DeepSeek.")
            player_message.clear()
            return
    else:
        bot.reply_to(message, "Tidak ada kunci API Gemini atau DeepSeek yang terkonfigurasi untuk pencarian.")

def register(bot: TeleBot) -> None:
    bot.register_message_handler(google_handler, commands=["google", "search", "mbahgoogle"], pass_bot=True)
    bot.register_message_handler(google_handler, regexp="^(google|search|mbahgoogle):", pass_bot=True)
