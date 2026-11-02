import os
import re
import json
import asyncio
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from google import genai

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


user_tasks = {}

def analyze_audio_with_gemini(audio_path, campaign_rules):
    """رفع الصوت مباشرة إلى Gemini لتفريغه ومطابقة الشروط بدقة وسرعة"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    uploaded_audio = client.files.upload(file=audio_path)
    
    prompt = f"""
أنت مخرج ومحرر فيديو فيروسي محترف لمنصات Shorts و TikTok.
استمع للملف الصوتي بدقة، وطبق شروط الكامبين التالية:
\"\"\"{campaign_rules}\"\"\"

المطلوب:
1. استخرج من 1 إلى 5 مقاطع حماسية ومتباعدة تحقق الشروط بدقة.
2. مدة كل مقطع بين 25 إلى 50 ثانية.
3. استخرج لكل مقطع:
   - start_seconds (بداية المقطع)
   - end_seconds (نهاية المقطع)
   - title (عنوان المقطع 3-4 كلمات)
   - hook_title (هوك علوي عريض بالأحرف الكبيرة ALL CAPS)
   - description (كابشن إنجليزي فيروسي يبدأ بسؤال جذاب ويحتوي الهاشتاجات المطلوبة)

أجب بصيغة JSON نظيفة فقط:
{{
  "clips": [
    {{
      "start_seconds": 15,
      "end_seconds": 50,
      "title": "Insane Moment",
      "hook_title": "HE ACTUALLY SAID THIS?!",
      "description": "Watch what just happened! #coreboys #fortnite"
    }}
  ]
}}
"""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[uploaded_audio, prompt]
    )
    
    try:
        client.files.delete(name=uploaded_audio.name)
    except:
        pass

    clean_json = re.sub(r"^```json\s*|```$", "", response.text.strip(), flags=re.MULTILINE).strip()
    return json.loads(clean_json)

def build_selection_keyboard(clips, selected_indices):
    buttons = []
    for idx, clip in enumerate(clips):
        is_selected = idx in selected_indices
        check = "✅ " if is_selected else "⬜ "
        duration = clip.get("end_seconds", 30) - clip.get("start_seconds", 0)
        btn_text = f"{check}{idx+1}. {clip.get('title', 'Clip')} ({duration}s)"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"toggle_{idx}")])
    
    buttons.append([
        InlineKeyboardButton("🚀 تصدير المقاطع المحددة", callback_data="export_selected"),
        InlineKeyboardButton("⚡ تصدير كل المقاطع", callback_data="export_all")
    ])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! أرسل رابط الفيديو وشروط الكامبين وسأجهز المقاطع فوراً.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    chat_id = update.message.chat_id
    
    url_match = re.search(r'(https?://[^\s]+)', text)
    if not url_match:
        await update.message.reply_text("⚠️ يرجى إرسال رابط صالح مع الشروط.")
        return

    video_url = url_match.group(0)
    campaign_rules = text.replace(video_url, "").strip() or "Clip best 30-50s viral moment"

    await update.message.reply_text("📥 1/3: جاري تنزيل الصوت والفيديو بسرعة...")
    input_video = f"in_{chat_id}.mp4"
    input_audio = f"audio_{chat_id}.mp3"
    
    subprocess.run(f'yt-dlp -f "best[height<=720][ext=mp4]/best[ext=mp4]" -o "{input_video}" "{video_url}"', shell=True)
    subprocess.run(f'ffmpeg -y -i "{input_video}" -vn -ar 16000 -ac 1 -b:a 64k "{input_audio}"', shell=True)

    if not os.path.exists(input_audio):
        await update.message.reply_text("❌ تعذر تنزيل الملف، تأكد من صحة الرابط.")
        return

    await update.message.reply_text("🧠 2/3: الذكاء الاصطناعي يحلل الصوت ويطابق شروط الكامبين...")
    try:
        data = analyze_audio_with_gemini(input_audio, campaign_rules)
        clips = data.get("clips", [])
    except Exception as e:
        clips = [{
            "start_seconds": 10,
            "end_seconds": 45,
            "title": "Stream Highlight",
            "hook_title": "BEST MOMENT",
            "description": "Insane moment! #coreboys #fortnite"
        }]

    if os.path.exists(input_audio): os.remove(input_audio)

    user_tasks[chat_id] = {
        "input_file": input_video,
        "clips": clips,
        "selected": set()
    }

    reply_markup = build_selection_keyboard(clips, user_tasks[chat_id]["selected"])
    await update.message.reply_text(
        f"🎯 تم استخراج {len(clips)} مقاطع متوافقة مع الشروط!\n\n"
        "حدد المقاطع التي تريد تصديرها ثم اضغط تصدير:",
        reply_markup=reply_markup
    )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if chat_id not in user_tasks:
        await query.edit_message_text("❌ انتهت الجلسة، أعد إرسال الرابط.")
        return

    task = user_tasks[chat_id]
    clips = task["clips"]
    selected = task["selected"]

    if data.startswith("toggle_"):
        idx = int(data.split("_")[1])
        if idx in selected: selected.remove(idx)
        else: selected.add(idx)
        await query.edit_message_reply_markup(reply_markup=build_selection_keyboard(clips, selected))
        return

    to_export = list(range(len(clips))) if data == "export_all" else sorted(list(selected))
    if not to_export:
        await query.answer("⚠️ حدد مقطعاً واحداً على الأقل!", show_alert=True)
        return

    input_file = task["input_file"]
    await query.edit_message_text(f"✂️ 3/3: جاري تصدير {len(to_export)} مقطع بالأبعاد والهوك...")

    for count, idx in enumerate(to_export, start=1):
        clip = clips[idx]
        start_sec = clip.get("start_seconds", 0)
        duration = max(5, clip.get("end_seconds", 30) - start_sec)
        hook_text = clip.get("hook_title", "").replace("'", "").replace(":", "")
        output_file = f"out_{chat_id}_{idx}.mp4"

        vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,drawtext=text='{hook_text}':fontcolor=yellow:fontsize=48:box=1:boxcolor=black@0.75:boxborderw=12:x=(w-text_w)/2:y=180"

        cmd = f'ffmpeg -y -ss {start_sec} -i "{input_file}" -t {duration} -vf "{vf}" -c:v libx264 -preset ultrafast -crf 23 -c:a aac "{output_file}"'
        subprocess.run(cmd, shell=True)

        await context.bot.send_message(chat_id=chat_id, text=f"🎬 مقطع ({count}/{len(to_export)}): {clip.get('title')}")
        with open(output_file, 'rb') as vf_send:
            await context.bot.send_video(chat_id=chat_id, video=vf_send, supports_streaming=True)

        caption_msg = f"📌 الهوك:\n`{clip.get('hook_title')}`\n\n📝 الكابشن والهاشتاجات:\n`{clip.get('description')}`"
        await context.bot.send_message(chat_id=chat_id, text=caption_msg, parse_mode="Markdown")

        if os.path.exists(output_file): os.remove(output_file)

    if os.path.exists(input_file): os.remove(input_file)
    del user_tasks[chat_id]
    await context.bot.send_message(chat_id=chat_id, text="🎉 تم الانتهاء بنجاح!")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CallbackQueryHandler(button_click))
    print("✅ الإيجنت جاهز تماماً على Render!")
    app.run_polling()

if __name__ == "__main__":
    main()
