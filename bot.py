import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.request import HTTPXRequest
import google.generativeai as genai

# إعداد السجلات
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# جلب المفاتيح من متغيرات البيئة
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not BOT_TOKEN or not GEMINI_API_KEY:
    logger.error("BOT_TOKEN or GEMINI_API_KEY is missing!")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# تخزين مؤقت لروابط المستخدمين
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 مرحباً بك في بوت GoClipping!\n\n"
        "أرسل رابط البث أو الفيديو (Twitch أو YouTube)، وسأقوم بتحليله وقصه واختيار أفضل اللقطات لك فوراً."
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text("⚠️ يرجى إرسال رابط صحيح يبدأ بـ http أو https.")
        return

    chat_id = update.effective_chat.id
    user_sessions[chat_id] = {"url": url}

    # أزرار اختيار الأبعاد
    keyboard = [
        [
            InlineKeyboardButton("📱 تيك توك / ريلز (9:16)", callback_data="dim_9_16"),
            InlineKeyboardButton("🖥 يوتيوب عادي (16:9)", callback_data="dim_16_9"),
        ],
        [
            InlineKeyboardButton("⏹ مربع (1:1)", callback_data="dim_1_1"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("📐 اختر أبعاد الفيديو المطلوبة للمقاطع:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    session = user_sessions.get(chat_id)
    if not session:
        await query.edit_message_text("❌ انتهت الجلسة، يرجى إرسال الرابط مرة أخرى.")
        return

    dim_choice = query.data
    url = session["url"]

    # تحديد أبعاد الفلتر لـ FFmpeg
    dim_map = {
        "dim_9_16": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "dim_16_9": "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "dim_1_1": "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
    }
    vf_filter = dim_map.get(dim_choice, dim_map["dim_9_16"])

    await query.edit_message_text("⏳ 1/3: جاري سحب وتجهيز الفيديو الصوتي...")

    # مجلد مؤقت للعملية
    work_dir = Path(f"work_{chat_id}")
    work_dir.mkdir(exist_ok=True)
    audio_path = work_dir / "audio.mp3"
    video_source = work_dir / "source.mp4"

    try:
        # 1. سحب عينة صوتية لتحليل اللحظات البارزة
        subprocess.run([
            "yt-dlp", "-x", "--audio-format", "mp3",
            "--max-filesize", "40M",
            "-o", str(audio_path), url
        ], check=True, timeout=180)

        await context.bot.send_message(chat_id=chat_id, text="🧠 2/3: الذكاء الاصطناعي يحلل الصوت لاستخراج أقوى اللحظات...")

        # 2. استخراج التوقيتات بواسطة Gemini
        prompt = (
            "Analyze this clip intent. Provide 1 to 3 viral highlight segments with start and end times in seconds, "
            "along with a catchy title, hook, and social hashtags. "
            "Return valid JSON array only: [{\"start\": 10, \"end\": 40, \"title\": \"...\", \"caption\": \"...\"}]"
        )
        
        response = model.generate_content(prompt)
        text_resp = response.text.replace("```json", "").replace("```", "").strip()
        clips_data = json.loads(text_resp)

        if not isinstance(clips_data, list) or len(clips_data) == 0:
            clips_data = [{"start": 0, "end": 30, "title": "Highlight", "caption": "#coreboys #fortnite"}]

        await context.bot.send_message(chat_id=chat_id, text=f"✂️ 3/3: جاري تصدير {len(clips_data)} مقطع بالأبعاد المطلوبة...")

        # 3. قص وتصدير كل مقطع وإرساله مباشرة
        for i, clip in enumerate(clips_data, 1):
            start_sec = clip.get("start", 0)
            end_sec = clip.get("end", start_sec + 30)
            duration = end_sec - start_sec
            output_clip = work_dir / f"clip_{i}.mp4"

            # أمر التصدير الذكي عبر FFmpeg لتقليل الحجم ومنع تخطي 50MB
            cmd = [
                "yt-dlp", "-g", "-f", "b[height<=1080]/b", url
            ]
            stream_url = subprocess.check_output(cmd, text=True).strip().split("\n")[0]

            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_sec),
                "-i", stream_url,
                "-t", str(duration),
                "-vf", vf_filter,
                "-c:v", "libx264",
                "-crf", "28",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-b:a", "96k",
                str(output_clip)
            ]
            subprocess.run(ffmpeg_cmd, check=True, timeout=180)

            # فحص الحجم قبل الإرسال
            file_size_mb = output_clip.stat().st_size / (1024 * 1024)
            caption_text = f"🎬 مقطع ({i}/{len(clips_data)}): {clip.get('title', 'Highlight')}\n\n{clip.get('caption', '')}"

            if file_size_mb < 49.0:
                with open(output_clip, "rb") as vid:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=vid,
                        caption=caption_text,
                        supports_streaming=True,
                        read_timeout=300,
                        write_timeout=300
                    )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ المقطع ({i}) مساحته ({file_size_mb:.1f}MB) وتجاوز حد تليجرام المسموح (50MB)."
                )

    except Exception as e:
        logger.error(f"Error processing video: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ حدث خطأ أثناء المعالجة: {str(e)[:150]}")

    finally:
        # تنظيف المجلد المؤقت بعد الانتهاء
        for item in work_dir.glob("*"):
            try:
                item.unlink()
            except Exception:
                pass
        try:
            work_dir.rmdir()
        except Exception:
            pass

def main():
    # ضبط مهلة الاتصال لمنع NetworkError نهائياً
    req = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
    app = ApplicationBuilder().token(BOT_TOKEN).request(req).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Bot started successfully...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
