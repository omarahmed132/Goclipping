import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
import google.generativeai as genai

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not BOT_TOKEN or not GEMINI_API_KEY:
    logger.error("Missing BOT_TOKEN or GEMINI_API_KEY!")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 مرحباً بك في بوت التقطيع الاحترافي!\n\n"
        "أرسل رابط البث (Twitch أو YouTube) ومعه شروط الكامبين والهاشتاجات المطلوبة في نفس الرسالة."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    words = raw_text.split()
    target_url = None
    for word in words:
        if word.startswith("http://") or word.startswith("https://"):
            target_url = word
            break

    if not target_url:
        await update.message.reply_text("⚠️ يرجى إرسال رابط صحيح يبدأ بـ http أو https.")
        return

    user_sessions[chat_id] = {
        "url": target_url,
        "instructions": raw_text
    }

    keyboard = [
        [
            InlineKeyboardButton("🎬 مقطع واحد فايرل (1)", callback_data="clips_1"),
            InlineKeyboardButton("🎬 مقطعان (2)", callback_data="clips_2"),
        ],
        [
            InlineKeyboardButton("🔥 3 مقاطع مميزة", callback_data="clips_3"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔢 اختر عدد المقاطع المطلوبة:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    session = user_sessions.get(chat_id)
    if not session:
        await query.edit_message_text("❌ انتهت الجلسة، أرسل الرابط مرة أخرى.")
        return

    data = query.data

    if data.startswith("clips_"):
        clip_count = int(data.split("_")[1])
        session["clip_count"] = clip_count

        keyboard = [
            [
                InlineKeyboardButton("📱 9:16 احترافي (فيديو كبير + بلور ناعم)", callback_data="dim_9_16"),
            ],
            [
                InlineKeyboardButton("🖥 16:9 يوتيوب أصلي", callback_data="dim_16_9"),
                InlineKeyboardButton("⏹ 1:1 مربع متناسق", callback_data="dim_1_1"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"✅ تم تحديد {clip_count} مقاطع.\n📐 اختر الأبعاد المناسبة:", reply_markup=reply_markup)
        return

    if data.startswith("dim_"):
        dim_choice = data
        url = session["url"]
        instructions = session.get("instructions", "")
        clip_count = session.get("clip_count", 1)

        await query.edit_message_text("⏳ 1/3: جاري سحب الصوت وتحليله بدقة لاختيار أقوى المشاهد...")

        work_dir = Path(f"work_{chat_id}")
        work_dir.mkdir(exist_ok=True)
        audio_file = work_dir / "stream_audio.mp3"

        try:
            # تنزيل الصوت الفعلي لأول 20 دقيقة أو مقطع خفيف لتحليله عبر Gemini
            subprocess.run([
                "yt-dlp", "-x", "--audio-format", "mp3",
                "--download-sections", "*00:00-25:00",
                "-o", str(audio_file),
                url
            ], check=True, timeout=180)

            await context.bot.send_message(chat_id=chat_id, text="🧠 2/3: الذكاء الاصطناعي يستمع للمحتوى ويختار اللحظات الفايرل حسب الشروط...")

            # رفع ملف الصوت إلى Gemini File API ليسمع الكلام والمشاعر بدقة
            uploaded_audio = genai.upload_file(str(audio_file), mime_type="audio/mp3")

            prompt = (
                f"You are a top-tier viral TikTok/Reels clipping editor.\n"
                f"Listen carefully to the audio and adhere strictly to these campaign rules: {instructions}\n\n"
                f"Find the absolute most viral, funny, shocking, or high-energy moments.\n"
                f"Extract exactly {clip_count} segments.\n"
                f"Each segment must be between 30 and 60 seconds long.\n"
                f"Write an irresistible pop-up hook (max 4 words, UPPERCASE) and a compelling caption with all required hashtags.\n"
                f"Return ONLY a valid JSON array: "
                f"[{{\"start\": 120, \"end\": 165, \"title\": \"...\", \"hook\": \"WAIT FOR IT!\", \"caption\": \"...\"}}]"
            )

            response = model.generate_content([uploaded_audio, prompt])
            clean_text = response.text.replace("```json", "").replace("```", "").strip()
            clips_data = json.loads(clean_text)

            # حذف الملف الصوتي المرفوع من خوادم جوجل بعد التحليل
            try:
                uploaded_audio.delete()
            except Exception:
                pass

            if not isinstance(clips_data, list) or len(clips_data) == 0:
                raise ValueError("AI response format invalid")

            await context.bot.send_message(chat_id=chat_id, text=f"✂️ 3/3: تم تحديد اللحظات بنجاح! جاري إنتاج {len(clips_data)} مقاطع بحجم الشاشة المثالي...")

            cmd = ["yt-dlp", "-g", "-f", "b[height<=1080]/b", url]
            stream_url = subprocess.check_output(cmd, text=True).strip().split("\n")[0]

            for i, clip in enumerate(clips_data, 1):
                start_sec = clip.get("start", 10)
                end_sec = clip.get("end", start_sec + 40)
                duration = max(20, min(end_sec - start_sec, 60))
                hook_text = clip.get("hook", "WATCH THIS").replace("'", "").replace(":", "")[:25]
                output_clip = work_dir / f"clip_{i}.mp4"

                if dim_choice == "dim_9_16":
                    # الفيديو الأساسي يشغل 1080 عرض بارتفاع مناسب وكبير (حوالي 65-70% من الشاشة)
                    # مع بلور أنيق وخفيف في الخلفية ومكان بارز للهوك في الأعلى
                    vf_filter = (
                        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
                        "[0:v]scale=1080:-2[fg];"
                        "[bg][fg]overlay=0:(H-h)/2,"
                        f"drawtext=text='{hook_text}':fontcolor=white:fontsize=52:bold=1:"
                        "box=1:boxcolor=black@0.75:boxborderw=12:x=(w-text_w)/2:y=(h*0.14)"
                    )
                elif dim_choice == "dim_1_1":
                    vf_filter = "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
                else:
                    vf_filter = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"

                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start_sec),
                    "-i", stream_url,
                    "-t", str(duration),
                    "-filter_complex", vf_filter,
                    "-c:v", "libx264",
                    "-crf", "25",
                    "-preset", "veryfast",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    str(output_clip)
                ]

                try:
                    subprocess.run(ffmpeg_cmd, check=True, timeout=300)
                except subprocess.CalledProcessError:
                    fallback_vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
                    subprocess.run([
                        "ffmpeg", "-y", "-ss", str(start_sec), "-i", stream_url,
                        "-t", str(duration), "-vf", fallback_vf,
                        "-c:v", "libx264", "-crf", "25", "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "128k", str(output_clip)
                    ], check=True, timeout=300)

                file_size_mb = output_clip.stat().st_size / (1024 * 1024)
                caption_text = (
                    f"🎬 <b>{clip.get('title', 'Viral Clip')}</b>\n\n"
                    f"{clip.get('caption', '')}"
                )

                if file_size_mb < 49.0:
                    with open(output_clip, "rb") as vid:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=vid,
                            caption=caption_text,
                            parse_mode="HTML",
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                        )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ المقطع ({i}) مساحته ({file_size_mb:.1f}MB) وتجاوز حد الـ 50MB.",
                    )

            await context.bot.send_message(chat_id=chat_id, text="🚀 تم تجهيز وإرسال المقاطع بنجاح!")

        except Exception as e:
            logger.error(f"Execution error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ أثناء المعالجة: {str(e)[:150]}")

        finally:
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
    req = HTTPXRequest(connect_timeout=60.0, read_timeout=300.0, write_timeout=300.0)
    app = ApplicationBuilder().token(BOT_TOKEN).request(req).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("Bot is active and running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
