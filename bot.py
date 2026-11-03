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

# إعداد السجلات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# جلب المفاتيح من المتغيرات
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

if not BOT_TOKEN or not GEMINI_API_KEY:
    logger.error("BOT_TOKEN or GEMINI_API_KEY is missing!")
    sys.exit(1)

genai.configure(api_key=GEMINI_API_KEY)

# تجربة وتعيين موديل Gemini المناسب
def get_ai_model():
    candidates = ["gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-pro"]
    for name in candidates:
        try:
            return genai.GenerativeModel(name)
        except Exception:
            continue
    return genai.GenerativeModel("gemini-1.5-flash")

model = get_ai_model()

# تخزين جلسات المستخدمين المؤقتة
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 مرحباً بك في بوت GoClipping الاحترافي!\n\n"
        "أرسل رابط البث أو الفيديو متبوعاً بأي شروط أو ملاحظات للكامبين.\n"
        "سأقوم بتحليل المحتوى، قص أفضل اللقطات الفايرل، تطبيق الإيديت الضبابي، وإرسال المقاطع جاهزة للنشر فوراً."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # استخراج الرابط من نص الرسالة
    words = raw_text.split()
    target_url = None
    for word in words:
        if word.startswith("http://") or word.startswith("https://"):
            target_url = word
            break

    if not target_url:
        await update.message.reply_text("⚠️ يرجى تضمين رابط صحيح يبدأ بـ http أو https داخل الرسالة.")
        return

    # حفظ الرابط وباقي نص الرسالة كشروط للمحتوى
    user_sessions[chat_id] = {
        "url": target_url,
        "instructions": raw_text
    }

    # الخطوة 1: اختيار عدد المقاطع المطلوبة
    keyboard = [
        [
            InlineKeyboardButton("🎬 مقطع واحد (1)", callback_data="clips_1"),
            InlineKeyboardButton("🎬 مقطعان (2)", callback_data="clips_2"),
        ],
        [
            InlineKeyboardButton("🔥 3 مقاطع", callback_data="clips_3"),
            InlineKeyboardButton("🚀 5 مقاطع", callback_data="clips_5"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔢 حدد عدد المقاطع التي تريد استخراجها:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    session = user_sessions.get(chat_id)
    if not session:
        await query.edit_message_text("❌ انتهت الجلسة، يرجى إرسال الرابط والشروط مرة أخرى.")
        return

    data = query.data

    # إذا اختار المستخدم عدد المقاطع
    if data.startswith("clips_"):
        count = int(data.split("_")[1])
        session["clip_count"] = count

        # الانتقال لاختيار الأبعاد
        keyboard = [
            [
                InlineKeyboardButton("📱 9:16 تيك توك / ريلز (خلفية ضبابية)", callback_data="dim_9_16"),
            ],
            [
                InlineKeyboardButton("🖥 16:9 يوتيوب أصلي", callback_data="dim_16_9"),
                InlineKeyboardButton("⏹ 1:1 مربع", callback_data="dim_1_1"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"✅ تم تحديد {count} مقاطع.\n📐 اختر الآن أبعاد الفيديو المطلوبة:", reply_markup=reply_markup)
        return

    # إذا اختار المستخدم الأبعاد
    if data.startswith("dim_"):
        dim_choice = data
        url = session["url"]
        instructions = session.get("instructions", "")
        clip_count = session.get("clip_count", 1)

        await query.edit_message_text("⏳ 1/3: جاري قراءة شروط الكامبين وتحليل الفيديو بواسطة الذكاء الاصطناعي...")

        work_dir = Path(f"work_{chat_id}")
        work_dir.mkdir(exist_ok=True)

        try:
            # استخراج أوقات المقاطع والهاشتاجات بناءً على الشروط
            prompt = (
                f"You are a viral social media video editor. Analyze this request and video conditions.\n"
                f"User Instructions and Campaign Guidelines: {instructions}\n\n"
                f"Extract exactly {clip_count} exciting, viral highlight segments.\n"
                f"Each segment duration must be between 20 to 50 seconds.\n"
                f"Adhere strictly to all campaign guidelines, required hashtags, and rules mentioned in instructions.\n"
                f"Return ONLY a valid JSON array of objects: "
                f"[{{\"start\": 25, \"end\": 65, \"title\": \"Viral Title\", \"hook\": \"WATCH THIS!\", \"caption\": \"Full text with hashtags\"}}]"
            )

            clips_data = []
            try:
                response = model.generate_content(prompt)
                clean_text = response.text.replace("```json", "").replace("```", "").strip()
                clips_data = json.loads(clean_text)
            except Exception as ai_err:
                logger.warning(f"AI parsing error: {ai_err}")
                # احتياطي تلقائي يفي بعدد المقاطع في حال عدم اكتمال استجابة الذكاء الاصطناعي
                clips_data = [
                    {
                        "start": 15 + (idx * 60),
                        "end": 50 + (idx * 60),
                        "title": f"Highlight #{idx + 1}",
                        "hook": "UNBELIEVABLE MOMENT!",
                        "caption": "#coreboys #fortnite"
                    }
                    for idx in range(clip_count)
                ]

            if not isinstance(clips_data, list) or len(clips_data) == 0:
                clips_data = [{
                    "start": 15,
                    "end": 55,
                    "title": "Stream Highlight",
                    "hook": "BEST MOMENT",
                    "caption": "#coreboys #fortnite"
                }]

            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✂️ 2/3: تم استخراج {len(clips_data)} لقطات فايرل. جاري المعالجة والإيديت الضبابي لكل مقطع..."
            )

            # جلب مسار البث بجودة تدعم المعالجة السريعة
            cmd = ["yt-dlp", "-g", "-f", "b[height<=1080]/b", url]
            stream_url = subprocess.check_output(cmd, text=True).strip().split("\n")[0]

            for i, clip in enumerate(clips_data, 1):
                start_sec = clip.get("start", 10)
                end_sec = clip.get("end", start_sec + 40)
                duration = max(15, min(end_sec - start_sec, 60))
                hook_text = clip.get("hook", "").replace("'", "").replace(":", "")[:30]
                output_clip = work_dir / f"clip_{i}.mp4"

                # فلتر الإيديت الضبابي والـ Pop-up Hook
                if dim_choice == "dim_9_16":
                    # خلفية ضبابية مموهة تملأ الشاشة + الفيديو ممركز في المنتصف + نص الهوك البارز بالأعلى
                    vf_filter = (
                        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
                        "[0:v]scale=1080:-2[fg];"
                        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                        f"drawtext=text='{hook_text}':fontcolor=yellow:fontsize=48:box=1:boxcolor=black@0.6:boxborderw=10:x=(w-text_w)/2:y=180"
                    )
                elif dim_choice == "dim_1_1":
                    vf_filter = "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080"
                else:
                    vf_filter = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"

                # أمر FFmpeg السريع والمضغوط لتفادي تخطي 50MB
                ffmpeg_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start_sec),
                    "-i", stream_url,
                    "-t", str(duration),
                    "-filter_complex", vf_filter,
                    "-c:v", "libx264",
                    "-crf", "27",
                    "-preset", "veryfast",
                    "-c:a", "aac",
                    "-b:a", "96k",
                    str(output_clip)
                ]

                # تنفيذ القص
                try:
                    subprocess.run(ffmpeg_cmd, check=True, timeout=300)
                except subprocess.CalledProcessError:
                    # فلتر بديل مبسط في حال واجه السيرفر نقصاً في خطوط النصوص
                    fallback_vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
                    ffmpeg_fallback = [
                        "ffmpeg", "-y",
                        "-ss", str(start_sec),
                        "-i", stream_url,
                        "-t", str(duration),
                        "-vf", fallback_vf,
                        "-c:v", "libx264",
                        "-crf", "27",
                        "-preset", "veryfast",
                        "-c:a", "aac",
                        "-b:a", "96k",
                        str(output_clip)
                    ]
                    subprocess.run(ffmpeg_fallback, check=True, timeout=300)

                file_size_mb = output_clip.stat().st_size / (1024 * 1024)
                caption_text = (
                    f"🎬 مقطع ({i}/{len(clips_data)}): {clip.get('title', 'Highlight')}\n\n"
                    f"{clip.get('caption', '')}"
                )

                # إرسال الفيديو مباشرة
                if file_size_mb < 49.0:
                    with open(output_clip, "rb") as vid:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=vid,
                            caption=caption_text,
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                        )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"⚠️ المقطع ({i}) حجمه ({file_size_mb:.1f}MB) وتجاوز حد التيليجرام.",
                    )

            await context.bot.send_message(chat_id=chat_id, text="✨ تم الانتهاء من تصدير وإرسال جميع المقاطع بنجاح!")

        except Exception as e:
            logger.error(f"Execution error: {e}")
            await context.bot.send_message(chat_id=chat_id, text=f"❌ حدث خطأ أثناء المعالجة: {str(e)[:150]}")

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
