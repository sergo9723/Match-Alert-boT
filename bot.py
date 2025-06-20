import os
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def alert_command(update, context):
    match_info = {
        'teams': 'Team A vs Team B',
        'time_left': '6 минут',
        'score': '78–80',
        'odds': '1.25',
        'url': 'https://1xbet.com/live/basketball/12345'
    }

    text = (
        f"🏀 *Матч:* {match_info['teams']}
"
        f"⏱ *Осталось:* {match_info['time_left']}
"
        f"📊 *Счёт:* {match_info['score']}
"
        f"💸 *Коэф:* {match_info['odds']}"
    )

    keyboard = [[InlineKeyboardButton("🔗 Смотреть матч", url=match_info['url'])]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text, parse_mode='Markdown',
        reply_markup=reply_markup
    )

updater = Updater(TOKEN)
updater.dispatcher.add_handler(CommandHandler("alert", alert_command))

if __name__ == "__main__":
    updater.start_polling()
    updater.idle()
